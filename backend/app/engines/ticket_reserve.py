"""Open lend tickets reserve capacity in the top strip only."""

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

def adjust_available_count(list_len: int, reserved: int) -> dict:
    reserved = _clamp(reserved, 0, list_len)
    return {
        "list_len": list_len,
        "reserved": reserved,
        "count_available": max(0, list_len - reserved),
    }

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

def _clamp(n: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, n))

def _distinct_items(rows) -> set:
    out = set()
    for r in rows:
        if r.get("item_id") is not None:
            out.add(int(r["item_id"]))
    return out
