"""预演/确认/直接借出/归还的对齐不变量：

- 预演只写票：可借栏、顶细条可借数、loans 三处不动
- 确认成功：可借栏少一件、顶细条可借数减一、借还记录多一笔
- 确认失败（非法借用人/应还日/叠单）：三处停在预演前，不留半截
- 叠单时至多一笔 active，item 只有一种 status
"""

import pytest
from fastapi.testclient import TestClient

from app import seed
from app.db import connect
from app.main import app

# seed 数据：item 1/2/3 available，item 4 on_loan + 一笔 active loan
SEED_AVAILABLE = 3


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    seed.init_db()
    return TestClient(app)


def _board(c):
    return c.get("/api/board").json()


def _insert_raw_ticket(item_id, borrower, due_date):
    """绕过预演校验直接造票，模拟非法借用人/应还日的确认路径。"""
    c = connect()
    cur = c.execute(
        "INSERT INTO lend_tickets(item_id,borrower,due_date,status,created_at) VALUES (?,?,?,?,?)",
        (item_id, borrower, due_date, "open", "2026-01-01T00:00:00+00:00"))
    c.commit()
    tid = cur.lastrowid
    c.close()
    return tid


def _ticket_status(tid):
    c = connect()
    row = c.execute("SELECT status FROM lend_tickets WHERE id=?", (tid,)).fetchone()
    c.close()
    return row["status"]


def _active_loans(c, item_id):
    return [l for l in c.get("/api/loans").json()["active"] if l["item_id"] == item_id]


def test_preview_only_writes_ticket(client):
    before = _board(client)
    loans_before = client.get("/api/loans").json()
    assert before["counts"]["available"] == SEED_AVAILABLE

    r = client.post("/api/items/1/preview", json={"borrower": "邻居", "due_date": "2026-12-31"})
    assert r.status_code == 200

    after = _board(client)
    assert after["counts"] == before["counts"]            # 顶细条不动
    assert [i["id"] for i in after["available"]] == [i["id"] for i in before["available"]]  # 可借栏不动
    assert all("preview_hold" not in i for i in after["available"])
    assert "reserved" not in after["counts"]
    assert client.get("/api/loans").json() == loans_before  # 借还记录不动


def test_confirm_success_moves_all_three(client):
    tid = client.post("/api/items/1/preview", json={"borrower": "邻居", "due_date": "2026-12-31"}).json()["ticket_id"]
    r = client.post(f"/api/tickets/{tid}/confirm")
    assert r.status_code == 200

    b = _board(client)
    assert b["counts"]["available"] == SEED_AVAILABLE - 1       # 顶细条减一
    assert 1 not in [i["id"] for i in b["available"]]           # 可借栏少一件
    assert len(_active_loans(client, 1)) == 1                   # 借还记录多一笔
    assert _ticket_status(tid) == "consumed"


def test_confirm_invalid_ticket_data_leaves_no_half_state(client):
    tid = _insert_raw_ticket(1, borrower="  ", due_date="2026-12-31")  # 非法借用人
    r = client.post(f"/api/tickets/{tid}/confirm")
    assert r.status_code == 400

    b = _board(client)
    assert b["counts"]["available"] == SEED_AVAILABLE           # 三处停在预演前
    assert 1 in [i["id"] for i in b["available"]]
    assert _active_loans(client, 1) == []
    assert _ticket_status(tid) == "failed"

    tid2 = _insert_raw_ticket(2, borrower="邻居", due_date="not-a-date")  # 非法应还日
    assert client.post(f"/api/tickets/{tid2}/confirm").status_code == 400
    assert _board(client)["counts"]["available"] == SEED_AVAILABLE
    assert _active_loans(client, 2) == []
    assert _ticket_status(tid2) == "failed"


def test_confirm_after_direct_lend_fails_clean(client):
    tid = client.post("/api/items/1/preview", json={"borrower": "邻居", "due_date": "2026-12-31"}).json()["ticket_id"]
    assert client.post("/api/items/1/lend", json={"borrower": "别人", "due_date": "2026-11-30"}).status_code == 200

    r = client.post(f"/api/tickets/{tid}/confirm")              # 叠单：票确认失败
    assert r.status_code == 409

    b = _board(client)
    assert b["counts"]["available"] == SEED_AVAILABLE - 1       # 只反映真实那笔借出
    assert len(_active_loans(client, 1)) == 1                   # 至多一笔 active
    assert _ticket_status(tid) == "failed"
    item = {i["id"]: i for i in client.get("/api/items").json()}[1]
    assert item["status"] == "on_loan"                          # 一种 status，无半截


def test_direct_lend_validates_and_rejects_overlap(client):
    assert client.post("/api/items/1/lend", json={"borrower": " ", "due_date": "2026-12-31"}).status_code == 400
    assert client.post("/api/items/1/lend", json={"borrower": "邻居", "due_date": "2026-13-01"}).status_code == 400
    assert _board(client)["counts"]["available"] == SEED_AVAILABLE  # 失败零副作用
    assert _active_loans(client, 1) == []

    assert client.post("/api/items/1/lend", json={"borrower": "邻居", "due_date": "2026-12-31"}).status_code == 200
    assert client.post("/api/items/1/lend", json={"borrower": "邻居", "due_date": "2026-12-31"}).status_code == 409
    assert len(_active_loans(client, 1)) == 1                   # 叠单仍至多一笔


def test_double_confirm_single_active(client):
    tid = client.post("/api/items/1/preview", json={"borrower": "邻居", "due_date": "2026-12-31"}).json()["ticket_id"]
    assert client.post(f"/api/tickets/{tid}/confirm").status_code == 200
    assert client.post(f"/api/tickets/{tid}/confirm").status_code == 409  # ticket_consumed
    assert len(_active_loans(client, 1)) == 1


def test_return_restores_available(client):
    lid = client.post("/api/items/1/lend", json={"borrower": "邻居", "due_date": "2026-12-31"}).json()["loan_id"]
    assert client.post(f"/api/loans/{lid}/return").status_code == 200

    b = _board(client)
    assert b["counts"]["available"] == SEED_AVAILABLE
    assert 1 in [i["id"] for i in b["available"]]
    assert _active_loans(client, 1) == []

    assert client.post(f"/api/loans/{lid}/return").status_code == 400  # 重复归还不叠单
    assert _board(client)["counts"]["available"] == SEED_AVAILABLE
