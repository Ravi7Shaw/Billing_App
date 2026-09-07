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

# ------------------------------------------------------ new business tables

DEFAULT_GST_RATE = 5.0


class Database:
    def __init__(self, path: str = None):
        self.path = path or _default_db_path()
        self._init_schema()
        self._run_migrations()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA busy_timeout = 10000")

        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
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
        # ---------------------------------------------------------- payments

    def add_payment(
        self,
        bill_id,
        amount,
        payment_mode="Cash",
        payment_date=None,
        notes="",
    ):
        if amount <= 0:
            raise ValueError("Payment amount must be greater than 0.")

        if payment_date is None:
            payment_date = datetime.now().strftime("%Y-%m-%d")

        try:
            datetime.strptime(payment_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid payment date. Expected YYYY-MM-DD.")

        with self._conn() as conn:
            bill = conn.execute(
                """
                SELECT id, customer_id, total
                FROM bills
                WHERE id=?
                """,
                (bill_id,),
            ).fetchone()

            if not bill:
                raise ValueError("Bill not found.")

            paid_row = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS paid
                FROM payments
                WHERE bill_id=?
                """,
                (bill_id,),
            ).fetchone()

            already_paid = paid_row["paid"]
            balance = bill["total"] - already_paid

            if amount > balance:
                raise ValueError(
                    f"Payment exceeds outstanding balance of Rs. {balance:.2f}."
                )

            cur = conn.execute(
                """
                INSERT INTO payments(
                    bill_id,
                    customer_id,
                    payment_date,
                    amount,
                    payment_mode,
                    notes
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    bill_id,
                    bill["customer_id"],
                    payment_date,
                    amount,
                    payment_mode,
                    notes,
                ),
            )

            return cur.lastrowid

    def get_bill_paid_amount(self, bill_id):
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(SUM(amount), 0) AS paid
                FROM payments
                WHERE bill_id=?
                """,
                (bill_id,),
            ).fetchone()

            return row["paid"]

    def get_bill_balance(self, bill_id):
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    b.total,
                    COALESCE(SUM(p.amount), 0) AS paid
                FROM bills b
                LEFT JOIN payments p ON p.bill_id = b.id
                WHERE b.id=?
                GROUP BY b.id
                """,
                (bill_id,),
            ).fetchone()

            if not row:
                return 0.0

            return max(row["total"] - row["paid"], 0)

    def get_bill_payment_history(self, bill_id):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT *
                FROM payments
                WHERE bill_id=?
                ORDER BY payment_date DESC, id DESC
                """,
                (bill_id,),
            ).fetchall()

    def get_customer_balance(self, customer_id):
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(b.total), 0) AS total_billed,
                    COALESCE(
                        (
                            SELECT SUM(p.amount)
                            FROM payments p
                            WHERE p.customer_id=?
                        ),
                        0
                    ) AS total_paid
                FROM bills b
                WHERE b.customer_id=?
                """,
                (customer_id, customer_id),
            ).fetchone()

            total_billed = row["total_billed"]
            total_paid = row["total_paid"]

            return {
                "total_billed": total_billed,
                "total_paid": total_paid,
                "balance": max(total_billed - total_paid, 0),
            }

    def get_customer_payment_history(self, customer_id):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT
                    p.*,
                    b.bill_no,
                    b.bill_date
                FROM payments p
                LEFT JOIN bills b ON b.id = p.bill_id
                WHERE p.customer_id=?
                ORDER BY p.payment_date DESC, p.id DESC
                """,
                (customer_id,),
            ).fetchall()

    def get_customer_balances(self):
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT
                    c.id,
                    c.name,
                    c.phone,

                    COALESCE(SUM(b.total), 0) AS total_billed,

                    COALESCE(
                        (
                            SELECT SUM(p.amount)
                            FROM payments p
                            WHERE p.customer_id = c.id
                        ),
                        0
                    ) AS total_paid

                FROM customers c
                LEFT JOIN bills b
                    ON b.customer_id = c.id

                GROUP BY c.id
                ORDER BY c.name
                """
            ).fetchall()

            result = []
            for row in rows:
                data = dict(row)
                data["balance"] = max(data["total_billed"] - data["total_paid"], 0)
                result.append(data)

            return result

    def _column_exists(self, conn, table_name, column_name):
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return any(row["name"] == column_name for row in rows)
        # ---------------------------------------------------------- expenses

    def add_expense(
        self,
        category,
        amount,
        payment_mode="Cash",
        description="",
        expense_date=None,
    ):
        category = category.strip()

        if not category:
            raise ValueError("Expense category is required.")

        if amount <= 0:
            raise ValueError("Expense amount must be greater than 0.")

        if expense_date is None:
            expense_date = datetime.now().strftime("%Y-%m-%d")

        try:
            datetime.strptime(expense_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid expense date. Expected YYYY-MM-DD.")

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO expenses(
                    expense_date,
                    category,
                    amount,
                    payment_mode,
                    description
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    expense_date,
                    category,
                    amount,
                    payment_mode,
                    description,
                ),
            )

            return cur.lastrowid

    def get_expenses(self, date_from=None, date_to=None):
        q = """
            SELECT *
            FROM expenses
            WHERE 1=1
        """

        params = []

        if date_from:
            q += " AND date(expense_date) >= date(?)"
            params.append(date_from)

        if date_to:
            q += " AND date(expense_date) <= date(?)"
            params.append(date_to)

        q += " ORDER BY expense_date DESC, id DESC"

        with self._conn() as conn:
            return conn.execute(q, params).fetchall()

    def delete_expense(self, expense_id):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM expenses WHERE id=?",
                (expense_id,),
            )

    def update_expense(
        self,
        expense_id,
        category,
        amount,
        payment_mode="Cash",
        description="",
        expense_date=None,
    ):
        category = category.strip()

        if not category:
            raise ValueError("Expense category is required.")

        if amount <= 0:
            raise ValueError("Expense amount must be greater than 0.")

        if expense_date is None:
            expense_date = datetime.now().strftime("%Y-%m-%d")

        try:
            datetime.strptime(expense_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid expense date. Expected YYYY-MM-DD.")

        with self._conn() as conn:
            conn.execute(
                """
                UPDATE expenses
                SET expense_date=?,
                    category=?,
                    amount=?,
                    payment_mode=?,
                    description=?
                WHERE id=?
                """,
                (
                    expense_date,
                    category,
                    amount,
                    payment_mode,
                    description,
                    expense_id,
                ),
            )

    def stat_expenses(self, date_from=None, date_to=None):
        q = """
            SELECT COALESCE(SUM(amount), 0) AS expenses
            FROM expenses
            WHERE 1=1
        """

        params = []

        if date_from:
            q += " AND date(expense_date) >= date(?)"
            params.append(date_from)

        if date_to:
            q += " AND date(expense_date) <= date(?)"
            params.append(date_to)

        with self._conn() as conn:
            return conn.execute(q, params).fetchone()["expenses"]

        # --------------------------------------------------------- employees

    def add_employee(self, name, phone="", role=""):
        name = name.strip()

        if not name:
            raise ValueError("Employee name is required.")

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO employees(name, phone, role)
                VALUES (?, ?, ?)
                """,
                (
                    name,
                    phone.strip(),
                    role.strip(),
                ),
            )

            return cur.lastrowid

    def get_employees(self, active_only=True):
        q = "SELECT * FROM employees"

        if active_only:
            q += " WHERE active=1"

        q += " ORDER BY name"

        with self._conn() as conn:
            return conn.execute(q).fetchall()

    def update_employee(self, employee_id, name, phone="", role="", active=1):
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE employees
                SET name=?, phone=?, role=?, active=?
                WHERE id=?
                """,
                (
                    name.strip(),
                    phone.strip(),
                    role.strip(),
                    active,
                    employee_id,
                ),
            )

    def delete_employee(self, employee_id):
        with self._conn() as conn:
            conn.execute(
                """
                UPDATE employees
                SET active=0
                WHERE id=?
                """,
                (employee_id,),
            )

    # -------------------------------------------------------- attendance

    def save_attendance(
        self,
        employee_id,
        attendance_date,
        status,
        notes="",
    ):
        if status not in ("Full Day", "Half Day", "Absent"):
            raise ValueError("Invalid attendance status.")

        try:
            datetime.strptime(attendance_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid attendance date. Expected YYYY-MM-DD.")

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO attendance(
                    employee_id,
                    attendance_date,
                    status,
                    notes
                )
                VALUES (?, ?, ?, ?)

                ON CONFLICT(employee_id, attendance_date)
                DO UPDATE SET
                    status=excluded.status,
                    notes=excluded.notes
                """,
                (
                    employee_id,
                    attendance_date,
                    status,
                    notes,
                ),
            )

    def get_attendance(
        self,
        date_from=None,
        date_to=None,
        employee_id=None,
    ):
        q = """
            SELECT
                a.*,
                e.name AS employee_name,
                e.role AS employee_role
            FROM attendance a
            JOIN employees e
                ON e.id = a.employee_id
            WHERE 1=1
        """

        params = []

        if date_from:
            q += " AND date(a.attendance_date) >= date(?)"
            params.append(date_from)

        if date_to:
            q += " AND date(a.attendance_date) <= date(?)"
            params.append(date_to)

        if employee_id:
            q += " AND a.employee_id=?"
            params.append(employee_id)

        q += """
            ORDER BY
                a.attendance_date DESC,
                e.name
        """

        with self._conn() as conn:
            return conn.execute(q, params).fetchall()

    def delete_attendance(self, attendance_id):
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM attendance WHERE id=?",
                (attendance_id,),
            )

    def _run_migrations(self):
        with self._conn() as conn:
            # -------------------------------------------------
            # GST columns
            # -------------------------------------------------

            if not self._column_exists(conn, "items", "gst_rate"):
                conn.execute(
                    """
                    ALTER TABLE items
                    ADD COLUMN gst_rate REAL NOT NULL DEFAULT 5.0
                    """
                )

            if not self._column_exists(conn, "bills", "taxable_amount"):
                conn.execute(
                    """
                    ALTER TABLE bills
                    ADD COLUMN taxable_amount REAL NOT NULL DEFAULT 0
                    """
                )

            if not self._column_exists(conn, "bills", "gst_rate"):
                conn.execute(
                    """
                    ALTER TABLE bills
                    ADD COLUMN gst_rate REAL NOT NULL DEFAULT 0
                    """
                )

            if not self._column_exists(conn, "bills", "gst_amount"):
                conn.execute(
                    """
                    ALTER TABLE bills
                    ADD COLUMN gst_amount REAL NOT NULL DEFAULT 0
                    """
                )

            if not self._column_exists(conn, "bill_items", "gst_rate"):
                conn.execute(
                    """
                    ALTER TABLE bill_items
                    ADD COLUMN gst_rate REAL NOT NULL DEFAULT 0
                    """
                )

            if not self._column_exists(conn, "bill_items", "gst_amount"):
                conn.execute(
                    """
                    ALTER TABLE bill_items
                    ADD COLUMN gst_amount REAL NOT NULL DEFAULT 0
                    """
                )

            # -------------------------------------------------
            # New tables
            # -------------------------------------------------

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bill_id INTEGER NOT NULL
                        REFERENCES bills(id) ON DELETE CASCADE,
                    customer_id INTEGER
                        REFERENCES customers(id) ON DELETE SET NULL,
                    payment_date TEXT NOT NULL
                        DEFAULT (date('now','localtime')),
                    amount REAL NOT NULL CHECK(amount > 0),
                    payment_mode TEXT NOT NULL DEFAULT 'Cash',
                    notes TEXT
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    expense_date TEXT NOT NULL
                        DEFAULT (date('now','localtime')),
                    category TEXT NOT NULL,
                    amount REAL NOT NULL CHECK(amount > 0),
                    payment_mode TEXT NOT NULL DEFAULT 'Cash',
                    description TEXT
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS employees (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    phone TEXT,
                    role TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL
                        DEFAULT (datetime('now','localtime'))
                )
                """
            )

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS attendance (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL
                        REFERENCES employees(id) ON DELETE CASCADE,
                    attendance_date TEXT NOT NULL,
                    status TEXT NOT NULL
                        CHECK(status IN ('Full Day', 'Half Day', 'Absent')),
                    notes TEXT,
                    UNIQUE(employee_id, attendance_date)
                )
                """
            )

            # -------------------------------------------------
            # Preserve existing bills as already-paid historical sales.
            # Before partial payments existed, a saved bill represented a
            # completed sale. This prevents every old bill from appearing
            # as an outstanding customer balance after the migration.
            # -------------------------------------------------
            conn.execute(
                """
                INSERT INTO payments(
                    bill_id,
                    customer_id,
                    payment_date,
                    amount,
                    payment_mode,
                    notes
                )
                SELECT
                    b.id,
                    b.customer_id,
                    date(b.bill_date),
                    b.total,
                    COALESCE(b.payment_mode, 'Cash'),
                    'Migrated from historical bill'
                FROM bills b
                WHERE b.total > 0
                  AND NOT EXISTS (
                      SELECT 1
                      FROM payments p
                      WHERE p.bill_id = b.id
                  )
                """
            )

            # -------------------------------------------------
            # Indexes
            # -------------------------------------------------

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_bill
                ON payments(bill_id)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_customer
                ON payments(customer_id)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_payments_date
                ON payments(payment_date)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_expenses_date
                ON expenses(expense_date)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_expenses_category
                ON expenses(category)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attendance_date
                ON attendance(attendance_date)
                """
            )

            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_attendance_employee
                ON attendance(employee_id)
                """
            )

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

    def _next_bill_no_conn(self, conn, bill_date=None):
        if bill_date is None:
            bill_date = datetime.now().strftime("%Y-%m-%d")

        date_key = bill_date.replace("-", "")
        prefix = f"INV-{date_key}-"

        row = conn.execute(
            """
            SELECT COALESCE(
                MAX(CAST(substr(bill_no, ?) AS INTEGER)),
                0
            ) AS max_seq
            FROM bills
            WHERE bill_no LIKE ?
            """,
            (
                len(prefix) + 1,
                f"{prefix}%",
            ),
        ).fetchone()

        return f"{prefix}{row['max_seq'] + 1:03d}"

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

        # --------------------------------------------------------------- GST

    def get_item_gst_rate(self, item_id):
        with self._conn() as conn:
            row = conn.execute(
                "SELECT gst_rate FROM items WHERE id=?",
                (item_id,),
            ).fetchone()

            if not row:
                return DEFAULT_GST_RATE

            return row["gst_rate"]

    def update_item_gst_rate(self, item_id, gst_rate):
        if gst_rate < 0:
            raise ValueError("GST rate cannot be negative.")

        with self._conn() as conn:
            conn.execute(
                """
                UPDATE items
                SET gst_rate=?
                WHERE id=?
                """,
                (
                    gst_rate,
                    item_id,
                ),
            )

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

    def next_bill_no(self, bill_date=None):
        with self._conn() as conn:
            return self._next_bill_no_conn(conn, bill_date)

    def save_bill(
        self,
        customer_id,
        items,
        subtotal,
        discount_percent,
        discount_amount,
        total,
        payment_mode,
        bill_date=None,
        taxable_amount=None,
        gst_rate=0.0,
        gst_amount=0.0,
        initial_payment_amount=None,
        payment_notes="",
    ):
        """
        Save an entire bill as one atomic transaction.

        Existing callers that do not pass GST/payment arguments continue to
        work. In that case the bill is treated as fully paid, preserving the
        old application's behaviour.

        GST values are snapshots stored on the bill and bill lines. The
        billing UI will calculate/pass these values when GST support is wired
        into the Billing tab.

        bill_date:
            YYYY-MM-DD
            Defaults to today.
        """

        if not items:
            raise ValueError("Cannot save an empty bill.")

        if bill_date is None:
            bill_date = datetime.now().strftime("%Y-%m-%d")

        try:
            datetime.strptime(bill_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid bill date. Expected YYYY-MM-DD.")

        if taxable_amount is None:
            taxable_amount = max(float(subtotal) - float(discount_amount), 0.0)

        if gst_rate < 0:
            raise ValueError("GST rate cannot be negative.")

        if gst_amount < 0:
            raise ValueError("GST amount cannot be negative.")

        if initial_payment_amount is None:
            # Backward compatibility: the current Billing tab has no
            # partial-payment field yet, so its bills are fully paid.
            initial_payment_amount = float(total)

        initial_payment_amount = float(initial_payment_amount)
        total = float(total)

        if initial_payment_amount < 0:
            raise ValueError("Initial payment cannot be negative.")

        if initial_payment_amount > total + 0.01:
            raise ValueError(
                f"Initial payment cannot exceed bill total of Rs. {total:.2f}."
            )

        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")

            bill_no = self._next_bill_no_conn(conn, bill_date)

            cur = conn.execute(
                """
                INSERT INTO bills(
                    bill_no,
                    customer_id,
                    bill_date,
                    subtotal,
                    discount_percent,
                    discount_amount,
                    total,
                    payment_mode,
                    taxable_amount,
                    gst_rate,
                    gst_amount
                )
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    bill_no,
                    customer_id,
                    bill_date,
                    subtotal,
                    discount_percent,
                    discount_amount,
                    total,
                    payment_mode,
                    taxable_amount,
                    gst_rate,
                    gst_amount,
                ),
            )

            bill_id = cur.lastrowid

            for it in items:
                quantity = it["quantity"]

                if quantity <= 0:
                    raise ValueError(f"Invalid quantity for item: {it['name']}")

                line_gst_rate = float(it.get("gst_rate", gst_rate or 0.0))
                line_gst_amount = float(it.get("gst_amount", 0.0))

                if line_gst_rate < 0:
                    raise ValueError("GST rate cannot be negative.")

                if line_gst_amount < 0:
                    raise ValueError("GST amount cannot be negative.")

                conn.execute(
                    """
                    INSERT INTO bill_items(
                        bill_id,
                        item_id,
                        item_name_snapshot,
                        category_snapshot,
                        quantity,
                        rate,
                        subtotal,
                        gst_rate,
                        gst_amount
                    )
                    VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        bill_id,
                        it.get("item_id"),
                        it["name"],
                        it.get("category", ""),
                        quantity,
                        it["rate"],
                        it["subtotal"],
                        line_gst_rate,
                        line_gst_amount,
                    ),
                )

                # IMPORTANT:
                # We deliberately do NOT block a bill because of
                # recorded stock quantity.
                if it.get("item_id"):
                    conn.execute(
                        """
                        UPDATE items
                        SET stock_qty = MAX(stock_qty - ?, 0)
                        WHERE id = ?
                        """,
                        (
                            quantity,
                            it["item_id"],
                        ),
                    )

            # Save the first payment in the same transaction as the bill.
            # A zero payment is valid for a fully-credit sale, so simply
            # don't create a row in that case.
            if initial_payment_amount > 0:
                conn.execute(
                    """
                    INSERT INTO payments(
                        bill_id,
                        customer_id,
                        payment_date,
                        amount,
                        payment_mode,
                        notes
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bill_id,
                        customer_id,
                        bill_date,
                        initial_payment_amount,
                        payment_mode,
                        payment_notes,
                    ),
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
        q = """
            SELECT
                COALESCE(SUM(total), 0) AS revenue,
                COUNT(*) AS bill_count
            FROM bills
            WHERE 1=1
        """
        params = []

        if date_from:
            q += " AND date(bill_date) >= date(?)"
            params.append(date_from)
        if date_to:
            q += " AND date(bill_date) <= date(?)"
            params.append(date_to)

        with self._conn() as conn:
            row = conn.execute(q, params).fetchone()

            qp = """
                SELECT COALESCE(SUM(bi.quantity), 0) AS pieces
                FROM bill_items bi
                JOIN bills b ON b.id = bi.bill_id
                WHERE 1=1
            """
            pparams = []

            if date_from:
                qp += " AND date(b.bill_date) >= date(?)"
                pparams.append(date_from)
            if date_to:
                qp += " AND date(b.bill_date) <= date(?)"
                pparams.append(date_to)

            pieces = conn.execute(qp, pparams).fetchone()["pieces"]

            expense_q = """
                SELECT COALESCE(SUM(amount), 0) AS expenses
                FROM expenses
                WHERE 1=1
            """
            expense_params = []

            if date_from:
                expense_q += " AND date(expense_date) >= date(?)"
                expense_params.append(date_from)
            if date_to:
                expense_q += " AND date(expense_date) <= date(?)"
                expense_params.append(date_to)

            expenses = conn.execute(expense_q, expense_params).fetchone()["expenses"]

            # Outstanding credit is based on bills in the selected period.
            outstanding_q = """
                SELECT COALESCE(SUM(
                    CASE
                        WHEN b.total - COALESCE(
                            (SELECT SUM(p.amount)
                             FROM payments p
                             WHERE p.bill_id = b.id), 0
                        ) > 0
                        THEN b.total - COALESCE(
                            (SELECT SUM(p.amount)
                             FROM payments p
                             WHERE p.bill_id = b.id), 0
                        )
                        ELSE 0
                    END
                ), 0) AS outstanding
                FROM bills b
                WHERE 1=1
            """
            outstanding_params = []

            if date_from:
                outstanding_q += " AND date(b.bill_date) >= date(?)"
                outstanding_params.append(date_from)
            if date_to:
                outstanding_q += " AND date(b.bill_date) <= date(?)"
                outstanding_params.append(date_to)

            outstanding = conn.execute(outstanding_q, outstanding_params).fetchone()[
                "outstanding"
            ]

            # Payments collected during the selected period.
            payment_q = """
                SELECT COALESCE(SUM(amount), 0) AS collected
                FROM payments
                WHERE 1=1
            """
            payment_params = []

            if date_from:
                payment_q += " AND date(payment_date) >= date(?)"
                payment_params.append(date_from)
            if date_to:
                payment_q += " AND date(payment_date) <= date(?)"
                payment_params.append(date_to)

            collected = conn.execute(payment_q, payment_params).fetchone()["collected"]

            revenue = row["revenue"]

            return {
                "revenue": revenue,
                "expenses": expenses,
                "net_profit": revenue - expenses,
                "payments_collected": collected,
                "outstanding": outstanding,
                "bill_count": row["bill_count"],
                "pieces": pieces,
                "avg_bill": (revenue / row["bill_count"]) if row["bill_count"] else 0,
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
