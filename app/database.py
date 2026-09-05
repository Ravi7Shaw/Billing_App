"""
database.py
------------
All SQLite access for the Cloth Shop Billing System lives here.

Design notes:
- Nothing about categories / brands / items / rates is hardcoded in Python.
  The schema only defines *structure*. On first run we insert a small set of
  starter rows (the categories the shop described) purely as convenience
  seed DATA -- every one of those rows can be renamed, deleted, or added to
  from the Inventory tab at any time. No business data lives in code.
- All money values are stored as REAL (rupees, 2 decimal convention enforced
  at the UI layer).
- Dates are stored as ISO strings ("YYYY-MM-DD" or full timestamp) so they
  sort and filter correctly.
"""

import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager

DB_FILENAME = "cloth_shop.db"


def _default_db_path() -> str:
    """Store the DB next to the app, in a user-writable location."""
    base = os.path.join(os.path.expanduser("~"), ".cloth_shop_billing")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, DB_FILENAME)


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL
);

-- "subtypes" is a generic second-level grouping under a category.
-- For Jeans this holds brands (LP, Mufti, US Polo...).
-- For T-Shirts this holds styles (Round Neck, Collar).
-- For categories that don't need it, it's simply left empty.
CREATE TABLE IF NOT EXISTS subtypes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id   INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    UNIQUE(category_id, name)
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    category_id   INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
    subtype_id    INTEGER REFERENCES subtypes(id) ON DELETE SET NULL,
    barcode       TEXT UNIQUE,
    size          TEXT,
    color         TEXT,
    rate          REAL NOT NULL DEFAULT 0,
    stock_qty     INTEGER NOT NULL DEFAULT 0,
    active        INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS customers (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    phone         TEXT UNIQUE,
    address       TEXT,
    notes         TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- What a customer says they want next time ("wishlist" / follow-up notes)
CREATE TABLE IF NOT EXISTS customer_wishlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id     INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    item_description TEXT NOT NULL,
    date_added      TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    fulfilled       INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS bills (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_no           TEXT UNIQUE NOT NULL,
    customer_id       INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    bill_date         TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    subtotal          REAL NOT NULL,
    discount_percent  REAL NOT NULL DEFAULT 0,
    discount_amount   REAL NOT NULL DEFAULT 0,
    total             REAL NOT NULL,
    payment_mode      TEXT DEFAULT 'Cash'
);

CREATE TABLE IF NOT EXISTS bill_items (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id             INTEGER NOT NULL REFERENCES bills(id) ON DELETE CASCADE,
    item_id             INTEGER REFERENCES items(id) ON DELETE SET NULL,
    item_name_snapshot  TEXT NOT NULL,
    category_snapshot   TEXT,
    quantity            INTEGER NOT NULL,
    rate                REAL NOT NULL,
    subtotal            REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_category ON items(category_id);
CREATE INDEX IF NOT EXISTS idx_items_barcode ON items(barcode);
CREATE INDEX IF NOT EXISTS idx_bills_date ON bills(bill_date);
CREATE INDEX IF NOT EXISTS idx_bill_items_bill ON bill_items(bill_id);
CREATE INDEX IF NOT EXISTS idx_customers_phone ON customers(phone);
"""

# Purely a starting point matching what the shop described -- fully editable
# / deletable from the Inventory tab. Not referenced anywhere else in code.
STARTER_CATEGORIES = ["Shirts", "T-Shirts", "Jeans", "Cotton Pants", "Ladies"]
STARTER_SUBTYPES = {
    "T-Shirts": ["Round Neck", "Collar"],
    "Jeans": ["LP", "Mufti", "US Polo"],
}


class Database:
    def __init__(self, path: str = None):
        self.path = path or _default_db_path()
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            cur = conn.execute("SELECT COUNT(*) AS c FROM categories")
            if cur.fetchone()["c"] == 0:
                for cat in STARTER_CATEGORIES:
                    conn.execute("INSERT INTO categories(name) VALUES (?)", (cat,))
                for cat_name, subs in STARTER_SUBTYPES.items():
                    row = conn.execute(
                        "SELECT id FROM categories WHERE name=?", (cat_name,)
                    ).fetchone()
                    if row:
                        for s in subs:
                            conn.execute(
                                "INSERT INTO subtypes(category_id, name) VALUES (?,?)",
                                (row["id"], s),
                            )

    # ---------------------------------------------------------- categories
    def get_categories(self):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM categories ORDER BY name").fetchall()

    def add_category(self, name: str):
        with self._conn() as conn:
            conn.execute("INSERT INTO categories(name) VALUES (?)", (name.strip(),))

    def rename_category(self, cat_id: int, new_name: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE categories SET name=? WHERE id=?", (new_name.strip(), cat_id)
            )

    def delete_category(self, cat_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM categories WHERE id=?", (cat_id,))

    # ------------------------------------------------------------ subtypes
    def get_subtypes(self, category_id: int):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM subtypes WHERE category_id=? ORDER BY name",
                (category_id,),
            ).fetchall()

    def add_subtype(self, category_id: int, name: str):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO subtypes(category_id, name) VALUES (?,?)",
                (category_id, name.strip()),
            )

    def rename_subtype(self, subtype_id: int, new_name: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE subtypes SET name=? WHERE id=?", (new_name.strip(), subtype_id)
            )

    def delete_subtype(self, subtype_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM subtypes WHERE id=?", (subtype_id,))

    # --------------------------------------------------------------- items
    def get_items(
        self, category_id=None, subtype_id=None, active_only=True, search=None
    ):
        q = """
            SELECT items.*, categories.name AS category_name,
                   subtypes.name AS subtype_name
            FROM items
            JOIN categories ON categories.id = items.category_id
            LEFT JOIN subtypes ON subtypes.id = items.subtype_id
            WHERE 1=1
        """
        params = []
        if active_only:
            q += " AND items.active=1"
        if category_id:
            q += " AND items.category_id=?"
            params.append(category_id)
        if subtype_id:
            q += " AND items.subtype_id=?"
            params.append(subtype_id)
        if search:
            q += " AND items.name LIKE ?"
            params.append(f"%{search}%")
        q += " ORDER BY items.name"
        with self._conn() as conn:
            return conn.execute(q, params).fetchall()

    def get_item_by_barcode(self, barcode: str):
        with self._conn() as conn:
            return conn.execute(
                """SELECT items.*, categories.name AS category_name,
                          subtypes.name AS subtype_name
                   FROM items JOIN categories ON categories.id = items.category_id
                   LEFT JOIN subtypes ON subtypes.id = items.subtype_id
                   WHERE items.barcode=? AND items.active=1""",
                (barcode.strip(),),
            ).fetchone()

    def get_item_by_id(self, item_id: int):
        with self._conn() as conn:
            return conn.execute(
                """SELECT items.*, categories.name AS category_name,
                          subtypes.name AS subtype_name
                   FROM items JOIN categories ON categories.id = items.category_id
                   LEFT JOIN subtypes ON subtypes.id = items.subtype_id
                   WHERE items.id=?""",
                (item_id,),
            ).fetchone()

    def find_item_by_name(self, category_id, subtype_id, name):
        """Exact-ish match used to recognise a manually typed item that
        already exists, so we don't create duplicate catalog rows."""
        with self._conn() as conn:
            q = "SELECT * FROM items WHERE category_id=? AND name=? "
            params = [category_id, name]
            if subtype_id:
                q += "AND subtype_id=?"
                params.append(subtype_id)
            else:
                q += "AND subtype_id IS NULL"
            return conn.execute(q, params).fetchone()

    def add_item(
        self, name, category_id, subtype_id, barcode, size, color, rate, stock_qty=0
    ):
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO items(name, category_id, subtype_id, barcode, size,
                                      color, rate, stock_qty)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    name.strip(),
                    category_id,
                    subtype_id,
                    (barcode.strip() if barcode else None) or None,
                    size or None,
                    color or None,
                    rate,
                    stock_qty,
                ),
            )
            return cur.lastrowid

    def update_item(
        self,
        item_id,
        name,
        category_id,
        subtype_id,
        barcode,
        size,
        color,
        rate,
        stock_qty,
        active=1,
    ):
        with self._conn() as conn:
            conn.execute(
                """UPDATE items SET name=?, category_id=?, subtype_id=?, barcode=?,
                       size=?, color=?, rate=?, stock_qty=?, active=?
                   WHERE id=?""",
                (
                    name.strip(),
                    category_id,
                    subtype_id,
                    (barcode.strip() if barcode else None) or None,
                    size or None,
                    color or None,
                    rate,
                    stock_qty,
                    active,
                    item_id,
                ),
            )

    def delete_item(self, item_id):
        with self._conn() as conn:
            conn.execute("UPDATE items SET active=0 WHERE id=?", (item_id,))

    def adjust_stock(self, item_id, delta):
        with self._conn() as conn:
            conn.execute(
                "UPDATE items SET stock_qty = stock_qty + ? WHERE id=?",
                (delta, item_id),
            )

    # ---------------------------------------------------------- customers
    def search_customers(self, text=""):
        with self._conn() as conn:
            if text:
                return conn.execute(
                    """SELECT * FROM customers
                       WHERE name LIKE ? OR phone LIKE ?
                       ORDER BY name""",
                    (f"%{text}%", f"%{text}%"),
                ).fetchall()
            return conn.execute("SELECT * FROM customers ORDER BY name").fetchall()

    def get_customer_by_phone(self, phone):
        with self._conn() as conn:
            return conn.execute(
                "SELECT * FROM customers WHERE phone=?", (phone.strip(),)
            ).fetchone()

    def get_customer_by_id(self, cid):
        with self._conn() as conn:
            return conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()

    def add_customer(self, name, phone, address="", notes=""):
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO customers(name, phone, address, notes) VALUES (?,?,?,?)",
                (
                    name.strip(),
                    (phone.strip() if phone else None) or None,
                    address,
                    notes,
                ),
            )
            return cur.lastrowid

    def update_customer(self, cid, name, phone, address, notes):
        with self._conn() as conn:
            conn.execute(
                "UPDATE customers SET name=?, phone=?, address=?, notes=? WHERE id=?",
                (
                    name.strip(),
                    (phone.strip() if phone else None) or None,
                    address,
                    notes,
                    cid,
                ),
            )

    def delete_customer(self, cid):
        """Deletes the customer record and their wishlist. Past bills are
        kept for sales records but become anonymous walk-in sales
        (customer_id set to NULL by the foreign key)."""
        with self._conn() as conn:
            conn.execute("DELETE FROM customers WHERE id=?", (cid,))

    def get_customer_purchase_history(self, cid):
        with self._conn() as conn:
            return conn.execute(
                """SELECT * FROM bills WHERE customer_id=? ORDER BY bill_date DESC""",
                (cid,),
            ).fetchall()

    def get_customer_total_spent(self, cid):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(total),0) AS s, COUNT(*) AS c FROM bills WHERE customer_id=?",
                (cid,),
            ).fetchone()
            return row["s"], row["c"]

    # ---------------------------------------------------------- wishlist
    def add_wishlist(self, customer_id, description):
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO customer_wishlist(customer_id, item_description) VALUES (?,?)",
                (customer_id, description.strip()),
            )

    def get_wishlist(self, customer_id):
        with self._conn() as conn:
            return conn.execute(
                """SELECT * FROM customer_wishlist WHERE customer_id=?
                   ORDER BY fulfilled ASC, date_added DESC""",
                (customer_id,),
            ).fetchall()

    def set_wishlist_fulfilled(self, wishlist_id, fulfilled=1):
        with self._conn() as conn:
            conn.execute(
                "UPDATE customer_wishlist SET fulfilled=? WHERE id=?",
                (fulfilled, wishlist_id),
            )

    def delete_wishlist(self, wishlist_id):
        with self._conn() as conn:
            conn.execute("DELETE FROM customer_wishlist WHERE id=?", (wishlist_id,))

    def all_open_wishlist(self):
        """Used by the statistics tab for 'customer preferences'."""
        with self._conn() as conn:
            return conn.execute(
                """SELECT cw.item_description, cw.date_added, c.name AS customer_name,
                          c.phone AS customer_phone
                   FROM customer_wishlist cw
                   JOIN customers c ON c.id = cw.customer_id
                   WHERE cw.fulfilled=0
                   ORDER BY cw.date_added DESC"""
            ).fetchall()

    # -------------------------------------------------------------- bills
    def next_bill_no(self):
        today = datetime.now().strftime("%Y%m%d")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM bills WHERE bill_no LIKE ?",
                (f"INV-{today}-%",),
            ).fetchone()
            seq = row["c"] + 1
            return f"INV-{today}-{seq:03d}"

    def save_bill(
        self,
        customer_id,
        items,
        subtotal,
        discount_percent,
        discount_amount,
        total,
        payment_mode,
    ):
        """items: list of dicts with item_id, name, category, quantity, rate, subtotal"""
        bill_no = self.next_bill_no()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO bills(bill_no, customer_id, subtotal, discount_percent,
                                      discount_amount, total, payment_mode)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    bill_no,
                    customer_id,
                    subtotal,
                    discount_percent,
                    discount_amount,
                    total,
                    payment_mode,
                ),
            )
            bill_id = cur.lastrowid
            for it in items:
                conn.execute(
                    """INSERT INTO bill_items(bill_id, item_id, item_name_snapshot,
                            category_snapshot, quantity, rate, subtotal)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        bill_id,
                        it.get("item_id"),
                        it["name"],
                        it.get("category"),
                        it["quantity"],
                        it["rate"],
                        it["subtotal"],
                    ),
                )
                if it.get("item_id"):
                    conn.execute(
                        "UPDATE items SET stock_qty = MAX(stock_qty - ?, 0) WHERE id=?",
                        (it["quantity"], it["item_id"]),
                    )
            return bill_id, bill_no

    def get_bill(self, bill_id):
        with self._conn() as conn:
            bill = conn.execute(
                """SELECT bills.*, customers.name AS customer_name, customers.phone AS customer_phone
                   FROM bills LEFT JOIN customers ON customers.id = bills.customer_id
                   WHERE bills.id=?""",
                (bill_id,),
            ).fetchone()
            items = conn.execute(
                "SELECT * FROM bill_items WHERE bill_id=?", (bill_id,)
            ).fetchall()
            return bill, items

    def delete_bill(self, bill_id):
        """Deletes a bill and its line items, and restores stock quantities
        that were deducted when the bill was made."""
        with self._conn() as conn:
            items = conn.execute(
                "SELECT item_id, quantity FROM bill_items WHERE bill_id=?", (bill_id,)
            ).fetchall()
            for it in items:
                if it["item_id"]:
                    conn.execute(
                        "UPDATE items SET stock_qty = stock_qty + ? WHERE id=?",
                        (it["quantity"], it["item_id"]),
                    )
            conn.execute("DELETE FROM bills WHERE id=?", (bill_id,))

    def search_bills(self, date_from=None, date_to=None, search_text=None):
        q = """SELECT bills.*, customers.name AS customer_name, customers.phone AS customer_phone,
                      (SELECT COUNT(*) FROM bill_items WHERE bill_items.bill_id = bills.id) AS item_count,
                      (SELECT COALESCE(SUM(quantity),0) FROM bill_items WHERE bill_items.bill_id = bills.id) AS piece_count
               FROM bills LEFT JOIN customers ON customers.id = bills.customer_id
               WHERE 1=1"""
        params = []
        if date_from:
            q += " AND date(bills.bill_date) >= date(?)"
            params.append(date_from)
        if date_to:
            q += " AND date(bills.bill_date) <= date(?)"
            params.append(date_to)
        if search_text:
            q += " AND (bills.bill_no LIKE ? OR customers.name LIKE ? OR customers.phone LIKE ?)"
            like = f"%{search_text}%"
            params += [like, like, like]
        q += " ORDER BY bills.bill_date DESC"
        with self._conn() as conn:
            return conn.execute(q, params).fetchall()

    # --------------------------------------------------------- statistics
    def stat_totals(self, date_from=None, date_to=None):
        q = "SELECT COALESCE(SUM(total),0) AS revenue, COUNT(*) AS bill_count FROM bills WHERE 1=1"
        params = []
        if date_from:
            q += " AND date(bill_date) >= date(?)"
            params.append(date_from)
        if date_to:
            q += " AND date(bill_date) <= date(?)"
            params.append(date_to)
        with self._conn() as conn:
            row = conn.execute(q, params).fetchone()
            qp = """SELECT COALESCE(SUM(bi.quantity),0) AS pieces
                    FROM bill_items bi JOIN bills b ON b.id = bi.bill_id WHERE 1=1"""
            pparams = []
            if date_from:
                qp += " AND date(b.bill_date) >= date(?)"
                pparams.append(date_from)
            if date_to:
                qp += " AND date(b.bill_date) <= date(?)"
                pparams.append(date_to)
            pieces = conn.execute(qp, pparams).fetchone()["pieces"]
            return {
                "revenue": row["revenue"],
                "bill_count": row["bill_count"],
                "pieces": pieces,
                "avg_bill": (row["revenue"] / row["bill_count"])
                if row["bill_count"]
                else 0,
            }

    def stat_daily_sales(self, date_from=None, date_to=None):
        """Returns list of (date, revenue) for every day that had sales."""
        q = """SELECT date(bill_date) AS d, SUM(total) AS revenue
               FROM bills WHERE 1=1"""
        params = []
        if date_from:
            q += " AND date(bill_date) >= date(?)"
            params.append(date_from)
        if date_to:
            q += " AND date(bill_date) <= date(?)"
            params.append(date_to)
        q += " GROUP BY date(bill_date) ORDER BY d"
        with self._conn() as conn:
            return conn.execute(q, params).fetchall()

    def stat_monthly_sales(self):
        """Returns list of (month 'YYYY-MM', revenue, bill_count) for every
        month that had at least one sale, oldest first. Used for the
        Monthly Sales chart, which always shows the shop's full history
        regardless of the Statistics page's Period filter."""
        with self._conn() as conn:
            return conn.execute(
                """SELECT strftime('%Y-%m', bill_date) AS month,
                          SUM(total) AS revenue, COUNT(*) AS bill_count
                   FROM bills
                   GROUP BY month
                   ORDER BY month"""
            ).fetchall()

    def stat_top_items(self, date_from=None, date_to=None, limit=10, by="quantity"):
        # Prefer the item's *current* name/category (via item_id) so that
        # renaming an item or its category in Inventory is reflected in
        # past statistics too. Falls back to the name recorded at the time
        # of sale only if the original item can no longer be found (e.g.
        # it was part of very old data with no linked item record).
        order_col = "total_qty" if by == "quantity" else "total_revenue"
        q = f"""SELECT COALESCE(items.name, bi.item_name_snapshot) AS name,
                       COALESCE(categories.name, bi.category_snapshot) AS category,
                       SUM(bi.quantity) AS total_qty, SUM(bi.subtotal) AS total_revenue
                FROM bill_items bi
                JOIN bills b ON b.id = bi.bill_id
                LEFT JOIN items ON items.id = bi.item_id
                LEFT JOIN categories ON categories.id = items.category_id
                WHERE 1=1"""
        params = []
        if date_from:
            q += " AND date(b.bill_date) >= date(?)"
            params.append(date_from)
        if date_to:
            q += " AND date(b.bill_date) <= date(?)"
            params.append(date_to)
        q += f"""
                GROUP BY COALESCE(items.name, bi.item_name_snapshot),
                         COALESCE(categories.name, bi.category_snapshot)
                ORDER BY {order_col} DESC LIMIT ?"""
        params.append(limit)
        with self._conn() as conn:
            return conn.execute(q, params).fetchall()

    def stat_category_sales(self, date_from=None, date_to=None):
        # Same live-name preference as stat_top_items -- a renamed category
        # shows its current name for all its historical sales.
        q = """SELECT COALESCE(categories.name, bi.category_snapshot) AS category,
                      SUM(bi.quantity) AS qty, SUM(bi.subtotal) AS revenue
               FROM bill_items bi
               JOIN bills b ON b.id = bi.bill_id
               LEFT JOIN items ON items.id = bi.item_id
               LEFT JOIN categories ON categories.id = items.category_id
               WHERE 1=1"""
        params = []
        if date_from:
            q += " AND date(b.bill_date) >= date(?)"
            params.append(date_from)
        if date_to:
            q += " AND date(b.bill_date) <= date(?)"
            params.append(date_to)
        q += " GROUP BY COALESCE(categories.name, bi.category_snapshot) ORDER BY revenue DESC"
        with self._conn() as conn:
            return conn.execute(q, params).fetchall()

    def stat_top_customers(self, date_from=None, date_to=None, limit=10):
        q = """SELECT c.name AS name, c.phone AS phone, COUNT(b.id) AS visits,
                      SUM(b.total) AS total_spent
               FROM bills b JOIN customers c ON c.id = b.customer_id WHERE 1=1"""
        params = []
        if date_from:
            q += " AND date(b.bill_date) >= date(?)"
            params.append(date_from)
        if date_to:
            q += " AND date(b.bill_date) <= date(?)"
            params.append(date_to)
        q += " GROUP BY c.id ORDER BY total_spent DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            return conn.execute(q, params).fetchall()
