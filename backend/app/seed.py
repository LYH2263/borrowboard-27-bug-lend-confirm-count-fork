from app.db import connect

def init_db():
    c = connect()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS items(
      id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, owner TEXT, status TEXT, data_quality TEXT
    );
    CREATE TABLE IF NOT EXISTS loans(
      id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, borrower TEXT, status TEXT,
      due_date TEXT, lent_at TEXT, returned_at TEXT
    );
    CREATE TABLE IF NOT EXISTS lend_tickets(
      id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INT, borrower TEXT, due_date TEXT,
      status TEXT, created_at TEXT, resolved_at TEXT
    );
    CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
    -- 至多一笔 active：DB 级兜底，叠单/并发也不可能叠出第二笔
    CREATE UNIQUE INDEX IF NOT EXISTS loans_one_active_per_item
      ON loans(item_id) WHERE status='active';
    """)
    if c.execute("SELECT COUNT(*) c FROM items").fetchone()["c"] == 0:
        c.executemany("INSERT INTO items(title,owner,status,data_quality) VALUES (?,?,?,?)", [
            ("电钻", "老周", "available", "clean"),
            ("折叠桌", "小陈", "available", "clean"),
            ("脏数据-无主", "", "available", "dirty"),
            ("已外借样例", "阿强", "on_loan", "clean"),
        ])
        c.execute(
            "INSERT INTO loans(item_id,borrower,status,due_date,lent_at) VALUES (?,?,?,?,?)",
            (4, "邻居甲", "active", "2020-06-01", "2020-05-01"),
        )
        c.execute("INSERT INTO settings(key,value) VALUES ('board_name','木色邻里板')")
        c.commit()
    reconcile_item_status(c)
    c.close()

def reconcile_item_status(c):
    """items.status 与 loans 对齐：一种 status，清掉历史遗留的半截状态。

    有 active 借出的物品必须是 on_loan；没有 active 借出的必须是 available。
    """
    c.execute("""UPDATE items SET status='on_loan'
                 WHERE status='available'
                   AND id IN (SELECT item_id FROM loans WHERE status='active')""")
    c.execute("""UPDATE items SET status='available'
                 WHERE status='on_loan'
                   AND id NOT IN (SELECT item_id FROM loans WHERE status='active')""")
    c.commit()
