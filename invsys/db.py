"""数据层：表结构 + 版本迁移 + 六个仓储（只写 SQL）。"""

import contextlib
import sqlite3

# 迁移脚本：按版本号顺序执行，已执行过的记录在 schema_version 里
MIGRATIONS = [
    (1, """
    CREATE TABLE IF NOT EXISTS user (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        username      TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        salt          TEXT NOT NULL,
        role          TEXT NOT NULL CHECK (role IN ('admin', 'clerk'))
    );

    CREATE TABLE IF NOT EXISTS warehouse (
        id   INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS product (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        sku        TEXT NOT NULL UNIQUE,
        name       TEXT NOT NULL,
        unit       TEXT NOT NULL DEFAULT '件',
        safe_stock INTEGER NOT NULL DEFAULT 0,
        created    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );

    CREATE TABLE IF NOT EXISTS stock (
        product_id   INTEGER NOT NULL REFERENCES product(id),
        warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
        qty          INTEGER NOT NULL DEFAULT 0 CHECK (qty >= 0),
        PRIMARY KEY (product_id, warehouse_id)
    );

    CREATE TABLE IF NOT EXISTS doc (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        no           TEXT NOT NULL UNIQUE,
        type         TEXT NOT NULL CHECK (type IN ('purchase', 'sale', 'adjust')),
        warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
        remark       TEXT NOT NULL DEFAULT '',
        total_amount REAL NOT NULL DEFAULT 0,
        created      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );

    CREATE TABLE IF NOT EXISTS doc_item (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        doc_id     INTEGER NOT NULL REFERENCES doc(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES product(id),
        qty        INTEGER NOT NULL CHECK (qty > 0),
        price      REAL NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS stock_movement (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id   INTEGER NOT NULL REFERENCES product(id),
        warehouse_id INTEGER NOT NULL REFERENCES warehouse(id),
        doc_id       INTEGER REFERENCES doc(id),
        type         TEXT NOT NULL,
        qty_delta    INTEGER NOT NULL,
        qty_before   INTEGER NOT NULL,
        qty_after    INTEGER NOT NULL,
        created      TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );

    CREATE INDEX IF NOT EXISTS idx_movement_product ON stock_movement(product_id, id DESC);
    CREATE INDEX IF NOT EXISTS idx_item_doc ON doc_item(doc_id);
    """),
    (2, """
    ALTER TABLE product ADD COLUMN category TEXT NOT NULL DEFAULT '';
    """),
    (3, """
    CREATE TABLE IF NOT EXISTS customer (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        name    TEXT NOT NULL,
        phone   TEXT NOT NULL DEFAULT '',
        address TEXT NOT NULL DEFAULT '',
        created TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );

    CREATE TABLE IF NOT EXISTS sales_order (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        no           TEXT NOT NULL UNIQUE,
        customer_id  INTEGER NOT NULL REFERENCES customer(id),
        status       TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft','confirmed','shipped','cancelled')),
        total_amount REAL NOT NULL DEFAULT 0,
        created      TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        confirmed_at TEXT,
        shipped_at   TEXT
    );

    CREATE TABLE IF NOT EXISTS sales_order_item (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id   INTEGER NOT NULL REFERENCES sales_order(id) ON DELETE CASCADE,
        product_id INTEGER NOT NULL REFERENCES product(id),
        qty        INTEGER NOT NULL CHECK (qty > 0),
        price      REAL NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_order_item ON sales_order_item(order_id);
    CREATE INDEX IF NOT EXISTS idx_order_status ON sales_order(status, id DESC);
    """),
]


@contextlib.contextmanager
def session(path):
    conn = sqlite3.connect(path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def migrate(path):
    """执行未应用的迁移，返回 (起始版本, 结束版本)。"""
    with session(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT)")
        current = conn.execute("SELECT IFNULL(MAX(version), 0) FROM schema_version").fetchone()[0]
        for version, sql in MIGRATIONS:
            if version <= current:
                continue
            conn.executescript(sql)
            conn.execute("INSERT INTO schema_version(version, applied_at) VALUES(?, datetime('now','localtime'))",
                         (version,))
    return current, MIGRATIONS[-1][0]


def rows(rows):
    return [dict(r) for r in rows]


class UserRepo:
    def __init__(self, conn):
        self.conn = conn

    def get(self, username):
        row = self.conn.execute("SELECT * FROM user WHERE username=?", (username,)).fetchone()
        return dict(row) if row else None

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]

    def create(self, username, password_hash, salt, role):
        self.conn.execute("INSERT INTO user(username, password_hash, salt, role) VALUES(?,?,?,?)",
                          (username, password_hash, salt, role))


class ProductRepo:
    def __init__(self, conn):
        self.conn = conn

    def list(self, keyword=None, offset=0, limit=20):
        where, args = "", []
        if keyword:
            where, args = "WHERE sku LIKE ? OR name LIKE ?", [f"%{keyword}%"] * 2
        total = self.conn.execute(f"SELECT COUNT(*) FROM product {where}", args).fetchone()[0]
        data = self.conn.execute(
            f"SELECT * FROM product {where} ORDER BY id DESC LIMIT ? OFFSET ?", args + [limit, offset]
        ).fetchall()
        return total, rows(data)

    def get(self, product_id):
        row = self.conn.execute("SELECT * FROM product WHERE id=?", (product_id,)).fetchone()
        return dict(row) if row else None

    def by_sku(self, sku):
        row = self.conn.execute("SELECT * FROM product WHERE sku=?", (sku,)).fetchone()
        return dict(row) if row else None

    def create(self, sku, name, unit, safe_stock, category):
        cur = self.conn.execute(
            "INSERT INTO product(sku, name, unit, safe_stock, category) VALUES(?,?,?,?,?)",
            (sku, name, unit, safe_stock, category),
        )
        return cur.lastrowid


class WarehouseRepo:
    def __init__(self, conn):
        self.conn = conn

    def list(self):
        return rows(self.conn.execute("SELECT * FROM warehouse ORDER BY id").fetchall())

    def get(self, warehouse_id):
        row = self.conn.execute("SELECT * FROM warehouse WHERE id=?", (warehouse_id,)).fetchone()
        return dict(row) if row else None

    def create(self, code, name):
        cur = self.conn.execute("INSERT INTO warehouse(code, name) VALUES(?,?)", (code, name))
        return cur.lastrowid


class StockRepo:
    def __init__(self, conn):
        self.conn = conn

    def qty(self, product_id, warehouse_id):
        row = self.conn.execute(
            "SELECT qty FROM stock WHERE product_id=? AND warehouse_id=?", (product_id, warehouse_id)
        ).fetchone()
        return row["qty"] if row else 0

    def increase(self, product_id, warehouse_id, n):
        self.conn.execute(
            "INSERT INTO stock(product_id, warehouse_id, qty) VALUES(?,?,?) "
            "ON CONFLICT(product_id, warehouse_id) DO UPDATE SET qty = qty + excluded.qty",
            (product_id, warehouse_id, n),
        )

    def try_decrease(self, product_id, warehouse_id, n) -> bool:
        """原子扣减：一条 UPDATE 完成"判断够不够 + 扣掉"，并发下不会超卖。"""
        cur = self.conn.execute(
            "UPDATE stock SET qty = qty - ? WHERE product_id=? AND warehouse_id=? AND qty >= ?",
            (n, product_id, warehouse_id, n),
        )
        return cur.rowcount > 0

    def set_qty(self, product_id, warehouse_id, qty):
        self.conn.execute(
            "INSERT INTO stock(product_id, warehouse_id, qty) VALUES(?,?,?) "
            "ON CONFLICT(product_id, warehouse_id) DO UPDATE SET qty = excluded.qty",
            (product_id, warehouse_id, qty),
        )

    def list(self, warehouse_id=None, keyword=None):
        sql = """SELECT s.product_id, s.warehouse_id, s.qty, p.sku, p.name, p.unit, p.safe_stock
                 FROM stock s JOIN product p ON p.id = s.product_id WHERE 1=1"""
        args = []
        if warehouse_id:
            sql += " AND s.warehouse_id = ?"
            args.append(warehouse_id)
        if keyword:
            sql += " AND (p.sku LIKE ? OR p.name LIKE ?)"
            args += [f"%{keyword}%"] * 2
        sql += " ORDER BY p.id"
        return rows(self.conn.execute(sql, args).fetchall())


class DocRepo:
    def __init__(self, conn):
        self.conn = conn

    def get_by_no(self, no):
        row = self.conn.execute("SELECT * FROM doc WHERE no=?", (no,)).fetchone()
        return dict(row) if row else None

    def create(self, no, doc_type, warehouse_id, remark, total_amount):
        cur = self.conn.execute(
            "INSERT INTO doc(no, type, warehouse_id, remark, total_amount) VALUES(?,?,?,?,?)",
            (no, doc_type, warehouse_id, remark, total_amount),
        )
        return cur.lastrowid

    def add_item(self, doc_id, product_id, qty, price):
        self.conn.execute("INSERT INTO doc_item(doc_id, product_id, qty, price) VALUES(?,?,?,?)",
                          (doc_id, product_id, qty, price))

    def items(self, doc_id):
        return rows(self.conn.execute(
            """SELECT i.*, p.sku, p.name FROM doc_item i JOIN product p ON p.id = i.product_id
               WHERE i.doc_id=? ORDER BY i.id""", (doc_id,)).fetchall())

    def list(self, doc_type=None, limit=20):
        where, args = "", []
        if doc_type:
            where, args = "WHERE type=?", [doc_type]
        return rows(self.conn.execute(
            f"SELECT * FROM doc {where} ORDER BY id DESC LIMIT ?", args + [limit]).fetchall())

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM doc").fetchone()[0]


class MovementRepo:
    def __init__(self, conn):
        self.conn = conn

    def add(self, product_id, warehouse_id, doc_id, mtype, delta, before, after):
        self.conn.execute(
            """INSERT INTO stock_movement(product_id, warehouse_id, doc_id, type, qty_delta, qty_before, qty_after)
               VALUES(?,?,?,?,?,?,?)""",
            (product_id, warehouse_id, doc_id, mtype, delta, before, after),
        )

    def list(self, product_id=None, limit=50):
        where, args = "", []
        if product_id:
            where, args = "WHERE m.product_id=?", [product_id]
        return rows(self.conn.execute(
            f"""SELECT m.*, p.sku, p.name FROM stock_movement m JOIN product p ON p.id = m.product_id
                {where} ORDER BY m.id DESC LIMIT ?""", args + [limit]).fetchall())


class ReportRepo:
    def __init__(self, conn):
        self.conn = conn

    def low_stock(self):
        return rows(self.conn.execute(
            """SELECT p.sku, p.name, p.safe_stock, IFNULL(SUM(s.qty),0) AS total_qty
               FROM product p LEFT JOIN stock s ON s.product_id = p.id
               GROUP BY p.id HAVING total_qty < p.safe_stock ORDER BY total_qty"""
        ).fetchall())

    def summary(self):
        row = self.conn.execute(
            """SELECT (SELECT COUNT(*) FROM product)   AS products,
                      (SELECT COUNT(*) FROM warehouse) AS warehouses,
                      (SELECT IFNULL(SUM(qty),0) FROM stock) AS stock_total,
                      (SELECT COUNT(*) FROM doc)       AS docs,
                      (SELECT IFNULL(ROUND(SUM(total_amount),2),0) FROM doc WHERE type='sale') AS sales_amount
            """
        ).fetchone()
        return dict(row)


class CustomerRepo:
    def __init__(self, conn):
        self.conn = conn

    def list(self, keyword=None, limit=50):
        where, args = "", []
        if keyword:
            where, args = "WHERE name LIKE ? OR phone LIKE ?", [f"%{keyword}%"] * 2
        return rows(self.conn.execute(
            f"SELECT * FROM customer {where} ORDER BY id DESC LIMIT ?", args + [limit]).fetchall())

    def get(self, customer_id):
        row = self.conn.execute("SELECT * FROM customer WHERE id=?", (customer_id,)).fetchone()
        return dict(row) if row else None

    def create(self, name, phone, address):
        cur = self.conn.execute("INSERT INTO customer(name, phone, address) VALUES(?,?,?)",
                                (name, phone, address))
        return self.get(cur.lastrowid)

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM customer").fetchone()[0]


class OrderRepo:
    def __init__(self, conn):
        self.conn = conn

    def get_by_no(self, no):
        row = self.conn.execute("SELECT * FROM sales_order WHERE no=?", (no,)).fetchone()
        return dict(row) if row else None

    def create(self, no, customer_id, total):
        cur = self.conn.execute(
            "INSERT INTO sales_order(no, customer_id, total_amount) VALUES(?,?,?)",
            (no, customer_id, total))
        return cur.lastrowid

    def add_item(self, order_id, product_id, qty, price):
        self.conn.execute(
            "INSERT INTO sales_order_item(order_id, product_id, qty, price) VALUES(?,?,?,?)",
            (order_id, product_id, qty, price))

    def items(self, order_id):
        return rows(self.conn.execute(
            """SELECT i.*, p.sku, p.name FROM sales_order_item i JOIN product p ON p.id = i.product_id
               WHERE i.order_id=? ORDER BY i.id""", (order_id,)).fetchall())

    def set_status(self, order_id, status, stamp_field=None):
        if stamp_field:
            self.conn.execute(
                f"UPDATE sales_order SET status=?, {stamp_field}=datetime('now','localtime') WHERE id=?",
                (status, order_id))
        else:
            self.conn.execute("UPDATE sales_order SET status=? WHERE id=?", (status, order_id))

    def list(self, status=None, limit=50):
        where, args = "", []
        if status:
            where, args = "WHERE o.status=?", [status]
        return rows(self.conn.execute(
            f"""SELECT o.*, c.name AS customer_name FROM sales_order o
                JOIN customer c ON c.id = o.customer_id {where}
                ORDER BY o.id DESC LIMIT ?""", args + [limit]).fetchall())

    def count(self, status=None):
        if status:
            return self.conn.execute("SELECT COUNT(*) FROM sales_order WHERE status=?", (status,)).fetchone()[0]
        return self.conn.execute("SELECT COUNT(*) FROM sales_order").fetchone()[0]


class DashboardRepo:
    def __init__(self, conn):
        self.conn = conn

    def today_orders(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM sales_order WHERE date(created)=date('now','localtime')").fetchone()[0]

    def month_amount(self):
        return self.conn.execute(
            """SELECT IFNULL(ROUND(SUM(total_amount),2),0) FROM sales_order
               WHERE status='shipped' AND strftime('%Y-%m', created)=strftime('%Y-%m','now','localtime')"""
        ).fetchone()[0]

    def top_products(self, limit=5):
        return rows(self.conn.execute(
            """SELECT p.sku, p.name, SUM(i.qty) AS qty, ROUND(SUM(i.qty * i.price),2) AS amount
               FROM sales_order_item i
               JOIN sales_order o ON o.id = i.order_id
               JOIN product p ON p.id = i.product_id
               WHERE o.status = 'shipped'
               GROUP BY p.id ORDER BY qty DESC LIMIT ?""", (limit,)).fetchall())

    def low_stock_count(self):
        return self.conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT p.id, p.safe_stock, IFNULL(SUM(s.qty),0) AS total
                   FROM product p LEFT JOIN stock s ON s.product_id = p.id
                   GROUP BY p.id HAVING total < p.safe_stock)""").fetchone()[0]
