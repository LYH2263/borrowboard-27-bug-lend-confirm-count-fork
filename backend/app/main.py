from datetime import date, datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from app import seed
from app.db import connect
from app.engines.borrow_rules import can_lend, classify_loans, validate_lend
from app.engines import ticket_reserve as tr

app = FastAPI(title="Borrowboard", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
def _startup(): seed.init_db()

def _begin_write():
    """One write lock for every mutation: check-then-write always runs serialized."""
    c = connect()
    c.isolation_level = None  # explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK
    c.execute("BEGIN IMMEDIATE")
    return c

def _rollback(c):
    try: c.execute("ROLLBACK")
    except Exception: pass

@app.get("/api/health")
def health(): return {"ok": True, "project": "borrowboard"}

@app.get("/api/items")
def items():
    c = connect(); rows = [dict(r) for r in c.execute("SELECT * FROM items")]; c.close(); return rows

@app.get("/api/board")
def board():
    c = connect()
    available = [dict(r) for r in c.execute("SELECT * FROM items WHERE status='available'")]
    loans = [dict(r) for r in c.execute(
        """SELECT loans.*, items.title FROM loans JOIN items ON items.id=loans.item_id
           WHERE loans.status='active'""")]
    reserved_ids = tr.reserved_item_ids(c)
    open_n = tr.open_ticket_count(c)
    c.close()
    cls = classify_loans(loans, date.today().isoformat())
    # 顶细条可借数恒等于可借栏条数；开放票只以 reserved 角标出现，绝不预扣
    return {
        "available": tr.mark_preview_rows(available, reserved_ids),
        "active": cls["active"],
        "overdue": cls["overdue"],
        "counts": {"available": len(available), "active": len(cls["active"]),
                   "overdue": len(cls["overdue"]), "reserved": open_n},
    }

class ItemIn(BaseModel):
    title: str
    owner: str

@app.post("/api/items")
def add_item(body: ItemIn):
    c = connect()
    cur = c.execute("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)",
                    (body.title, body.owner, "available", "clean"))
    c.commit(); iid = cur.lastrowid; c.close(); return {"id": iid}

class LendIn(BaseModel):
    borrower: str
    due_date: str

@app.post("/api/items/{iid}/lend")
def lend(iid: int, body: LendIn):
    """Direct lend: same rules and same write lock as ticket confirm — one path, one status."""
    v = validate_lend(body.borrower, body.due_date)
    if not v["ok"]:
        raise HTTPException(400, v["reason"])
    c = _begin_write()
    try:
        item = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
        if not item:
            _rollback(c); raise HTTPException(404, "item")
        active = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
        check = can_lend(item["status"], active)
        if not check["ok"]:
            _rollback(c); raise HTTPException(409, check["reason"])
        cur = c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
            (iid, body.borrower.strip(), "active", body.due_date.strip(), datetime.now(timezone.utc).isoformat()))
        c.execute("UPDATE items SET status='on_loan' WHERE id=?", (iid,))
        c.execute("COMMIT")
        return {"loan_id": cur.lastrowid}
    except HTTPException:
        raise
    except Exception:
        _rollback(c)
        raise
    finally:
        c.close()

@app.post("/api/items/{iid}/preview")
def preview_lend(iid: int, body: LendIn):
    """Dry-run a lend: validate + snapshot current lendability, issue a ticket.

    Writes only lend_tickets — items/loans (and thus board counts) are untouched.
    """
    v = validate_lend(body.borrower, body.due_date)
    if not v["ok"]:
        raise HTTPException(400, v["reason"])
    c = connect()
    item = c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone()
    if not item: c.close(); raise HTTPException(404, "item")
    active = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'", (iid,)).fetchone()["c"]
    check = can_lend(item["status"], active)
    if not check["ok"]:
        c.close(); raise HTTPException(409, check["reason"])
    cur = c.execute(
        "INSERT INTO lend_tickets(item_id,borrower,due_date,status,created_at) VALUES (?,?,?,?,?)",
        (iid, body.borrower.strip(), body.due_date.strip(), "open", datetime.now(timezone.utc).isoformat()))
    c.commit(); tid = cur.lastrowid; c.close()
    return {"ticket_id": tid, "item_id": iid, "title": item["title"],
            "borrower": body.borrower.strip(), "due_date": body.due_date.strip()}

@app.post("/api/tickets/{tid}/confirm")
def confirm_ticket(tid: int):
    """Confirm a preview ticket: re-run lend rules under the write lock, then commit atomically.

    Success lands loan + item flip + ticket consume in one transaction. Failure
    (invalid borrower/due date, item gone, already on loan) touches only the
    ticket — items/loans, and thus the board, stay exactly as before preview.
    """
    c = _begin_write()
    try:
        t = c.execute("SELECT * FROM lend_tickets WHERE id=?", (tid,)).fetchone()
        if not t:
            _rollback(c); raise HTTPException(404, "ticket")
        if t["status"] != "open":
            _rollback(c); raise HTTPException(409, "ticket_" + t["status"])
        v = validate_lend(t["borrower"], t["due_date"])
        item = c.execute("SELECT * FROM items WHERE id=?", (t["item_id"],)).fetchone()
        active = c.execute("SELECT COUNT(*) c FROM loans WHERE item_id=? AND status='active'",
                           (t["item_id"],)).fetchone()["c"]
        check = can_lend(item["status"], active) if item else {"ok": False, "reason": "item_missing"}
        if not v["ok"] or not check["ok"]:
            now = datetime.now(timezone.utc).isoformat()
            c.execute("UPDATE lend_tickets SET status='failed', resolved_at=? WHERE id=?", (now, tid))
            c.execute("COMMIT")
            if not v["ok"]:
                raise HTTPException(400, v["reason"])
            raise HTTPException(404 if not item else 409, check["reason"])
        now = datetime.now(timezone.utc).isoformat()
        cur = c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
            (t["item_id"], t["borrower"], "active", t["due_date"], now))
        r = c.execute("UPDATE items SET status='on_loan' WHERE id=? AND status='available'", (t["item_id"],))
        if r.rowcount != 1:
            _rollback(c); raise HTTPException(409, "race_lost")
        lid = cur.lastrowid
        c.execute("UPDATE lend_tickets SET status='consumed', resolved_at=? WHERE id=?", (now, tid))
        c.execute("COMMIT")
        return {"loan_id": lid, "item_id": t["item_id"], "borrower": t["borrower"], "due_date": t["due_date"]}
    except HTTPException:
        raise
    except Exception:
        _rollback(c)
        raise
    finally:
        c.close()

@app.post("/api/tickets/{tid}/cancel")
def cancel_ticket(tid: int):
    """Void an open ticket. Touches only the ticket — the hold is released, nothing else moves."""
    c = _begin_write()
    try:
        t = c.execute("SELECT * FROM lend_tickets WHERE id=?", (tid,)).fetchone()
        if not t:
            _rollback(c); raise HTTPException(404, "ticket")
        if t["status"] != "open":
            _rollback(c); raise HTTPException(409, "ticket_" + t["status"])
        now = datetime.now(timezone.utc).isoformat()
        c.execute("UPDATE lend_tickets SET status='cancelled', resolved_at=? WHERE id=?", (now, tid))
        c.execute("COMMIT")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        _rollback(c)
        raise
    finally:
        c.close()

@app.post("/api/loans/{lid}/return")
def return_loan(lid: int):
    c = _begin_write()
    try:
        loan = c.execute("SELECT * FROM loans WHERE id=?", (lid,)).fetchone()
        if not loan:
            _rollback(c); raise HTTPException(404, "loan")
        if loan["status"] != "active":
            _rollback(c); raise HTTPException(400, "not_active")
        now = datetime.now(timezone.utc).isoformat()
        c.execute("UPDATE loans SET status='returned', returned_at=? WHERE id=? AND status='active'", (now, lid))
        c.execute("UPDATE items SET status='available' WHERE id=?", (loan["item_id"],))
        c.execute("COMMIT")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception:
        _rollback(c)
        raise
    finally:
        c.close()

@app.get("/api/loans")
def loans():
    c = connect()
    rows = [dict(r) for r in c.execute(
        "SELECT loans.*, items.title FROM loans JOIN items ON items.id=loans.item_id ORDER BY loans.id DESC")]
    c.close()
    return classify_loans(rows, date.today().isoformat())

@app.get("/api/settings")
def settings():
    c = connect(); rows = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}; c.close(); return rows
