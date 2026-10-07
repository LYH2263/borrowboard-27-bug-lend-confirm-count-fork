"""Open lend tickets are informational holds only — they never reduce availability.

A preview writes only a row in lend_tickets. The available column and the top
strip's available count both come from items/loans and move only when a
confirm actually lands a loan. Open tickets surface solely as a `reserved`
count and a `preview_hold` marker on the row.
"""

def open_ticket_count(c) -> int:
    row = c.execute(
        "SELECT COUNT(DISTINCT item_id) c FROM lend_tickets WHERE status=?",
        (_open_status(),)).fetchone()
    return _safe_int(row)

def reserved_item_ids(c) -> set:
    rows = c.execute(
        "SELECT item_id FROM lend_tickets WHERE status=?",
        (_open_status(),)).fetchall()
    return _distinct_items(rows)

def mark_preview_rows(available: list, reserved_ids: set) -> list:
    out = []
    for row in available:
        item = dict(row)
        if item.get("id") in reserved_ids:
            item["preview_hold"] = True
        out.append(item)
    return out

def _open_status() -> str:
    return "open"

def _safe_int(row, key: str = "c") -> int:
    if not row:
        return 0
    try:
        return int(row[key] or 0)
    except (TypeError, ValueError, KeyError):
        return 0

def _distinct_items(rows) -> set:
    out = set()
    for r in rows:
        if r["item_id"] is not None:
            out.add(int(r["item_id"]))
    return out
