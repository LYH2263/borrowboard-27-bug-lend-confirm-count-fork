"""One active loan per item + overdue detection."""

import re
from datetime import date

_DUE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def validate_lend(borrower: str, due_date: str) -> dict:
    if not (borrower or "").strip():
        return {"ok": False, "reason": "borrower_required"}
    due = (due_date or "").strip()
    if not _DUE_DATE_RE.match(due):
        return {"ok": False, "reason": "due_date_invalid"}
    try:
        date.fromisoformat(due)
    except ValueError:
        return {"ok": False, "reason": "due_date_invalid"}
    return {"ok": True, "reason": ""}

def can_lend(item_status: str, active_loans: int) -> dict:
    if item_status != "available":
        return {"ok": False, "reason": "item_not_available"}
    if active_loans > 0:
        return {"ok": False, "reason": "already_on_loan"}
    return {"ok": True, "reason": ""}

def is_overdue(due_date: str, today: str, loan_status: str) -> bool:
    if loan_status != "active":
        return False
    return bool(due_date) and due_date < today

def classify_loans(loans: list[dict], today: str) -> dict:
    active, overdue, returned = [], [], []
    for L in loans:
        st = L.get("status")
        if st == "returned":
            returned.append(L)
        elif is_overdue(L.get("due_date"), today, st):
            overdue.append({**L, "overdue": True})
        elif st == "active":
            active.append({**L, "overdue": False})
    return {"active": active, "overdue": overdue, "returned": returned}
