"""预演/确认闸的对齐不变量：

- 预演只写票：可借栏、顶细条可借数、loans 三处不动
- 确认成功：可借栏少一件、顶细条可借数减一、借还记录多一笔
- 确认失败（非法借用人/应还日、叠单）：三处停在预演前，不留半截
- 叠单（预演×直接借出×归还）：一种 status、至多一笔 active
"""
import pytest
from fastapi.testclient import TestClient

from app.db import connect
from app.main import app
from app import seed


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with TestClient(app) as c:
        yield c


def board(client):
    b = client.get("/api/board").json()
    # 恒等式：顶细条可借数 == 可借栏条数，任何时候都成立
    assert b["counts"]["available"] == len(b["available"])
    return b


def active_loans(client):
    return client.get("/api/loans").json()["active"]


def first_available(client):
    return board(client)["available"][0]["id"]


def preview(client, iid, borrower="邻居", due_date="2026-12-31"):
    r = client.post(f"/api/items/{iid}/preview", json={"borrower": borrower, "due_date": due_date})
    assert r.status_code == 200
    return r.json()["ticket_id"]


def ticket_status(tid):
    c = connect()
    row = c.execute("SELECT status FROM lend_tickets WHERE id=?", (tid,)).fetchone()
    c.close()
    return row["status"]


def item_status(iid):
    c = connect()
    row = c.execute("SELECT status FROM items WHERE id=?", (iid,)).fetchone()
    c.close()
    return row["status"]


def plant_ticket(iid, borrower, due_date):
    """绕过预演校验直接落票，模拟历史/脏票。"""
    c = connect()
    cur = c.execute(
        "INSERT INTO lend_tickets(item_id,borrower,due_date,status,created_at) VALUES (?,?,?,?,?)",
        (iid, borrower, due_date, "open", "2026-10-07T00:00:00+00:00"))
    c.commit()
    tid = cur.lastrowid
    c.close()
    return tid


def test_preview_writes_only_ticket(client):
    before = board(client)
    loans_before = active_loans(client)
    iid = before["available"][0]["id"]

    tid = preview(client, iid)

    after = board(client)
    assert [i["id"] for i in after["available"]] == [i["id"] for i in before["available"]]
    assert after["counts"]["available"] == before["counts"]["available"]
    assert after["counts"]["reserved"] == before["counts"]["reserved"] + 1
    assert active_loans(client) == loans_before
    assert ticket_status(tid) == "open"
    assert item_status(iid) == "available"


def test_confirm_success_moves_all_three(client):
    before = board(client)
    loans_before = len(active_loans(client))
    iid = before["available"][0]["id"]
    tid = preview(client, iid)

    r = client.post(f"/api/tickets/{tid}/confirm")
    assert r.status_code == 200

    after = board(client)
    assert len(after["available"]) == len(before["available"]) - 1
    assert after["counts"]["available"] == before["counts"]["available"] - 1
    assert iid not in [i["id"] for i in after["available"]]
    assert len(active_loans(client)) == loans_before + 1
    assert after["counts"]["reserved"] == before["counts"]["reserved"]
    assert ticket_status(tid) == "consumed"
    assert item_status(iid) == "on_loan"


@pytest.mark.parametrize("borrower,due_date", [
    ("   ", "2026-12-31"),      # 非法借用人
    ("邻居", "2026-13-40"),      # 非法应还日
    ("邻居", "not-a-date"),
])
def test_confirm_failure_invalid_ticket_leaves_no_half_state(client, borrower, due_date):
    before = board(client)
    loans_before = active_loans(client)
    iid = before["available"][0]["id"]
    tid = plant_ticket(iid, borrower, due_date)

    r = client.post(f"/api/tickets/{tid}/confirm")
    assert r.status_code == 400

    after = board(client)
    assert after["available"] == before["available"]
    assert after["counts"] == before["counts"]
    assert active_loans(client) == loans_before
    assert item_status(iid) == "available"  # 关键：不得留下 on_loan 无 loan 的半截
    assert ticket_status(tid) == "failed"


def test_preview_then_direct_lend_confirm_fails_clean(client):
    """预演 × 直接借出叠单：确认失败，但只有直接借出那一笔 active。"""
    before = board(client)
    iid = before["available"][0]["id"]
    tid = preview(client, iid)

    r = client.post(f"/api/items/{iid}/lend", json={"borrower": "直接借", "due_date": "2026-11-01"})
    assert r.status_code == 200

    r = client.post(f"/api/tickets/{tid}/confirm")
    assert r.status_code == 409

    after = board(client)
    assert len([l for l in active_loans(client) if l["item_id"] == iid]) == 1
    assert item_status(iid) == "on_loan"
    assert ticket_status(tid) == "failed"
    assert len(after["available"]) == len(before["available"]) - 1


def test_double_confirm_single_active(client):
    iid = first_available(client)
    tid = preview(client, iid)
    assert client.post(f"/api/tickets/{tid}/confirm").status_code == 200

    r = client.post(f"/api/tickets/{tid}/confirm")
    assert r.status_code == 409

    assert len([l for l in active_loans(client) if l["item_id"] == iid]) == 1
    assert ticket_status(tid) == "consumed"


def test_direct_lend_validates_like_preview(client):
    iid = first_available(client)
    loans_before = len(active_loans(client))

    assert client.post(f"/api/items/{iid}/lend",
                       json={"borrower": " ", "due_date": "2026-12-31"}).status_code == 400
    assert client.post(f"/api/items/{iid}/lend",
                       json={"borrower": "邻居", "due_date": "junk"}).status_code == 400

    assert len(active_loans(client)) == loans_before
    assert item_status(iid) == "available"


def test_return_restores_available(client):
    before = board(client)
    iid = before["available"][0]["id"]
    lid = client.post(f"/api/items/{iid}/lend",
                      json={"borrower": "邻居", "due_date": "2026-11-01"}).json()["loan_id"]
    assert board(client)["counts"]["available"] == before["counts"]["available"] - 1

    r = client.post(f"/api/loans/{lid}/return")
    assert r.status_code == 200

    after = board(client)
    assert after["counts"]["available"] == before["counts"]["available"]
    assert item_status(iid) == "available"
    loans = client.get("/api/loans").json()
    assert not [l for l in loans["active"] if l["item_id"] == iid]
    # 重复归还不叠单
    assert client.post(f"/api/loans/{lid}/return").status_code == 400


def test_cancel_releases_hold(client):
    before = board(client)
    iid = before["available"][0]["id"]
    tid = preview(client, iid)
    assert board(client)["counts"]["reserved"] == before["counts"]["reserved"] + 1

    r = client.post(f"/api/tickets/{tid}/cancel")
    assert r.status_code == 200

    after = board(client)
    assert after["counts"] == before["counts"]
    assert after["available"] == before["available"]
    assert ticket_status(tid) == "cancelled"
    # 取消后的票不能再确认
    assert client.post(f"/api/tickets/{tid}/confirm").status_code == 409


def test_reconcile_heals_legacy_half_state(client):
    """历史半截（on_loan 但无 active loan）在启动对齐时被修复。"""
    iid = first_available(client)
    c = connect()
    c.execute("UPDATE items SET status='on_loan' WHERE id=?", (iid,))
    c.commit()
    c.close()

    seed.init_db()

    assert item_status(iid) == "available"
    b = board(client)
    assert iid in [i["id"] for i in b["available"]]
