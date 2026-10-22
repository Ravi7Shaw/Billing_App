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
import csv
import shutil
import tempfile
from datetime import datetime
from contextlib import contextmanager

from money import money
from reporting import ReportingQueries, money_cents, MoneySum
from offers import OfferQueries
from exchanges import ExchangeQueries
from employees import EmployeeQueries
from commerce import CommerceQueries
from labels import LabelQueries

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

-- Performance fix: bills.customer_id and bill_items.item_id had no index,
-- so every customer-history / balances lookup and every "top selling
-- item" stat was a full table scan. Harmless at a handful of rows, but
-- this is exactly what turns into visible lag once a shop has a few
-- hundred bills.
CREATE INDEX IF NOT EXISTS idx_bills_customer ON bills(customer_id);
CREATE INDEX IF NOT EXISTS idx_bill_items_item ON bill_items(item_id);
CREATE INDEX IF NOT EXISTS idx_customers_name ON customers(name);
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


class Database(LabelQueries, CommerceQueries, ReportingQueries, OfferQueries, ExchangeQueries, EmployeeQueries):
    def __init__(self, path: str = None):
        self.path = path or _default_db_path()
        self._init_schema()
        self._run_migrations()
        self._ensure_report_indexes()
        self._ensure_offer_schema()
        self._ensure_exchange_schema()
        self._ensure_employee_schema()
        self._ensure_commerce_schema()
        self._ensure_label_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.create_function("money_cents", 1, money_cents, deterministic=True)
        conn.create_aggregate("sum_money_cents", 1, MoneySum)

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

    def _ensure_report_indexes(self):
        # date(...) indexes preserve existing timestamp/date semantics.
        with self._conn() as conn:
            for statement in (
                "CREATE INDEX IF NOT EXISTS idx_bills_day ON bills(date(bill_date))",
                "CREATE INDEX IF NOT EXISTS idx_payments_day ON payments(date(payment_date))",
                "CREATE INDEX IF NOT EXISTS idx_expenses_day ON expenses(date(expense_date))",
                "CREATE INDEX IF NOT EXISTS idx_bills_customer_date ON bills(customer_id,bill_date DESC,id DESC)",
            ):
                conn.execute(statement)

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
            return conn.execute("SELECT * FROM categories WHERE active=1 ORDER BY name").fetchall()
        # ---------------------------------------------------------- payments

    def add_payment(
        self,
        bill_id,
        amount,
        payment_mode="Cash",
        payment_date=None,
        notes="",
    ):
        amount = money(amount)
        if amount <= 0:
            raise ValueError("Payment amount must be greater than 0.")

        if payment_date is None:
            payment_date = datetime.now().strftime("%Y-%m-%d")

        try:
            datetime.strptime(payment_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid payment date. Expected YYYY-MM-DD.")

        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
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
                SELECT COALESCE(sum_money_cents(amount), 0)/100.0 AS paid
                FROM payments
                WHERE bill_id=?
                """,
                (bill_id,),
            ).fetchone()

            already_paid = paid_row["paid"]
            balance = money(money(bill["total"]) - money(already_paid))

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
                SELECT COALESCE(sum_money_cents(amount), 0)/100.0 AS paid
                FROM payments
                WHERE bill_id=?
                """,
                (bill_id,),
            ).fetchone()

            return money(row["paid"])

    def get_bill_balance(self, bill_id):
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    b.total,
                    COALESCE(sum_money_cents(p.amount), 0)/100.0 AS paid
                FROM bills b
                LEFT JOIN payments p ON p.bill_id = b.id
                WHERE b.id=?
                GROUP BY b.id
                """,
                (bill_id,),
            ).fetchone()

            if not row:
                return 0.0

            return max(money(money(row["total"]) - money(row["paid"])), 0)

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
            row = conn.execute("""SELECT
                COALESCE(SUM(money_cents(b.total)),0)/100.0 AS total_billed,
                COALESCE(SUM((SELECT COALESCE(sum_money_cents(p.amount),0)
                    FROM payments p WHERE p.bill_id=b.id)),0)/100.0 AS total_paid,
                COALESCE(SUM(MAX(money_cents(b.total)-(SELECT COALESCE(sum_money_cents(p.amount),0)
                    FROM payments p WHERE p.bill_id=b.id),0)),0)/100.0 AS balance
                FROM bills b WHERE b.customer_id=?""",(customer_id,)).fetchone()
            return dict(row)

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
            rows = conn.execute("""SELECT c.id,c.name,c.phone,
                COALESCE(SUM(money_cents(b.total)),0)/100.0 AS total_billed,
                COALESCE(SUM((SELECT COALESCE(sum_money_cents(p.amount),0)
                    FROM payments p WHERE p.bill_id=b.id)),0)/100.0 AS total_paid,
                COALESCE(SUM(MAX(money_cents(b.total)-(SELECT COALESCE(sum_money_cents(p.amount),0)
                    FROM payments p WHERE p.bill_id=b.id),0)),0)/100.0 AS balance
                FROM customers c LEFT JOIN bills b ON b.customer_id=c.id
                GROUP BY c.id ORDER BY c.name,c.id""").fetchall()
            return [dict(row) for row in rows]

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
        employee_id=None,
        godown_id=1,
    ):
        category = category.strip()

        if not category:
            raise ValueError("Expense category is required.")

        amount = money(amount)
        if amount <= 0:
            raise ValueError("Expense amount must be greater than 0.")

        if expense_date is None:
            expense_date = datetime.now().strftime("%Y-%m-%d")

        try:
            datetime.strptime(expense_date, "%Y-%m-%d")
        except ValueError:
            raise ValueError("Invalid expense date. Expected YYYY-MM-DD.")

        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._location(conn, godown_id)
            if employee_id is not None:
                self._require_employee(conn,employee_id,active=True)
            cur = conn.execute(
                """
                INSERT INTO expenses(
                    expense_date,
                    category,
                    amount,
                    payment_mode,
                    description,
                    created_at,
                    employee_id
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    expense_date,
                    category,
                    amount,
                    payment_mode,
                    description,
                    created_at,
                    employee_id,
                ),
            )

            conn.execute('UPDATE expenses SET godown_id=? WHERE id=?', (godown_id, cur.lastrowid))
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
        name = ' '.join(name.split())

        if not name:
            raise ValueError("Employee name is required.")

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO employees(name, phone, role, name_key)
                VALUES (?, ?, ?, ?)
                """,
                (
                    name,
                    phone.strip(),
                    role.strip(),
                    name.casefold(),
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
                SET name=?, phone=?, role=?, active=?, name_key=?
                WHERE id=?
                """,
                (
                    name.strip(),
                    phone.strip(),
                    role.strip(),
                    active,
                    " ".join(name.split()).casefold(),
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

    def save_attendance(self, employee_id, attendance_date, status, notes=""):
        self.set_employee_attendance(employee_id,attendance_date,status,notes)

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
            conn.execute("BEGIN IMMEDIATE")
            # Table creation is the migration marker. Existing payments tables
            # already support credit sales, including bills with no payments.
            legacy_payments = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='payments'"
            ).fetchone() is None
            if not self._column_exists(conn, "categories", "active"):
                conn.execute("ALTER TABLE categories ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
            if not self._column_exists(conn, "bill_items", "stock_deducted"):
                # NULL means a historical deduction was not recorded.
                conn.execute("ALTER TABLE bill_items ADD COLUMN stock_deducted INTEGER")

            conn.execute("""CREATE TABLE IF NOT EXISTS bill_sequences (
                date_key TEXT PRIMARY KEY, last_seq INTEGER NOT NULL
            )""")
            # Seed from surviving historical invoices; retain higher sequences
            # already allocated even after their invoices have been deleted.
            conn.execute("""INSERT INTO bill_sequences(date_key, last_seq)
                SELECT substr(bill_no, 5, 8), MAX(CAST(substr(bill_no, 14) AS INTEGER))
                FROM bills WHERE bill_no GLOB 'INV-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9]*'
                GROUP BY substr(bill_no, 5, 8)
                ON CONFLICT(date_key) DO UPDATE SET
                    last_seq = MAX(bill_sequences.last_seq, excluded.last_seq)
            """)
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

            # Bug fix (real root cause): the Expenses tab has always
            # displayed a "created_at" column (expenses_tab.py refresh()),
            # and an earlier version of this migration tried to add it
            # with `DEFAULT (datetime('now','localtime'))`. SQLite only
            # allows a non-constant ALTER TABLE ... ADD COLUMN default on
            # a table that has zero rows -- the moment the expenses table
            # already has a single row (i.e. any shop that had already
            # been using the app), that ALTER TABLE fails outright with
            # "Cannot add a column with non-constant default", which
            # left the column missing and caused sqlite3.Row to raise
            # "IndexError: No item with that key" the next time
            # get_expenses() ran (add-expense and every tab refresh).
            #
            # Fix: add the column with NO default at all (always legal,
            # regardless of SQLite version or existing row count), and
            # stamp new rows with a real timestamp explicitly in
            # add_expense()'s INSERT instead of relying on a column
            # default. Existing rows simply get NULL, which the UI
            # already handles by falling back to an empty string.
            if not self._column_exists(conn, "expenses", "created_at"):
                conn.execute(
                    """
                    ALTER TABLE expenses
                    ADD COLUMN created_at TEXT
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
            if legacy_payments:
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
            restored = conn.execute(
                "UPDATE categories SET active=1 WHERE name=? AND active=0", (name.strip(),)
            )
            if not restored.rowcount:
                conn.execute("INSERT INTO categories(name) VALUES (?)", (name.strip(),))

    def rename_category(self, cat_id: int, new_name: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE categories SET name=? WHERE id=?", (new_name.strip(), cat_id)
            )

    def delete_category(self, cat_id: int):
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM items WHERE category_id=? AND active=1", (cat_id,)).fetchone():
                raise ValueError("This category still has active items. Remove or move them first.")
            # Archive the category too: inactive items and sale links remain intact.
            conn.execute("UPDATE categories SET active=0 WHERE id=?", (cat_id,))

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

        sequence = conn.execute(
            "SELECT last_seq FROM bill_sequences WHERE date_key=?", (date_key,)
        ).fetchone()
        last_seq = max(row["max_seq"], sequence["last_seq"] if sequence else 0)
        return f"{prefix}{last_seq + 1:03d}"

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
                   WHERE items.active=1 AND items.id IN (SELECT id FROM items WHERE barcode=? UNION SELECT item_id FROM barcode_aliases WHERE code=?)""",
                (barcode.strip(), barcode.strip()),
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

    def find_item_by_name(self, category_id, subtype_id, name, size="", color="", barcode=""):
        """Match an active variant without treating missing optional fields as required."""
        with self._conn() as conn:
            q = """SELECT * FROM items WHERE active=1 AND category_id=?
                AND name = ? COLLATE NOCASE AND subtype_id IS ?
                AND COALESCE(size, '') = ? COLLATE NOCASE
                AND COALESCE(color, '') = ? COLLATE NOCASE"""
            params = [category_id, name.strip(), subtype_id, size.strip(), color.strip()]
            if barcode.strip():
                q += " AND barcode=?"
                params.append(barcode.strip())
            rows = conn.execute(q, params).fetchall()
            if len(rows) > 1:
                raise ValueError("Several items match. Select the specific variant or enter its barcode.")
            return rows[0] if rows else None

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
            conn.execute("BEGIN IMMEDIATE")
            self._check_barcode_alias(conn, barcode, None)
            if not conn.execute("SELECT 1 FROM categories WHERE id=? AND active=1", (category_id,)).fetchone():
                raise ValueError("Choose an active category. The previous category was removed.")
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
            self._catalog_stock(conn, cur.lastrowid, stock_qty)
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
            conn.execute("BEGIN IMMEDIATE")
            self._check_barcode_alias(conn, barcode, item_id)
            if not conn.execute("SELECT 1 FROM categories WHERE id=? AND active=1", (category_id,)).fetchone():
                raise ValueError("Choose an active category. The previous category was removed.")
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

            self._catalog_stock(conn, item_id, stock_qty)

    def delete_item(self, item_id):
        with self._conn() as conn:
            conn.execute("UPDATE items SET active=0 WHERE id=?", (item_id,))

    def adjust_stock(self, item_id, delta):
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._stock_delta(conn, item_id, 1, delta, None, 'Inventory adjustment')

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

    def get_customer_phone_suggestions(self):
        """Phone/name labels for billing in either role."""
        with self._conn() as conn:
            return conn.execute(
                """SELECT name, phone FROM customers
                   WHERE phone IS NOT NULL AND TRIM(phone) != ''
                   ORDER BY name, phone"""
            ).fetchall()

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

    def update_customer(self, cid, name, phone, address, notes=None):
        with self._conn() as conn:
            conn.execute(
                "UPDATE customers SET name=?, phone=?, address=?, notes=COALESCE(?,notes) WHERE id=?",
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
        # Performance fix: this used to return bare bill rows, and
        # customers_tab.py would then call get_bill(b["id"]) in a loop
        # (one extra full query per bill, every time a customer with a
        # long history was opened) purely to count pieces sold. Folding
        # the piece count into this single query removes that N+1 --
        # this is one of the things that made the app feel laggy once a
        # regular customer had dozens of bills.
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT bills.*,
                       COALESCE(
                           (SELECT SUM(quantity) FROM bill_items
                            WHERE bill_items.bill_id = bills.id),
                           0
                       ) AS piece_count
                FROM bills
                WHERE customer_id=?
                ORDER BY bill_date DESC
                """,
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
        employee_pricing=False,
        customer_details=None,
        godown_id=1,
        sale_channel="Retail",
    ):
        """
        Save a bill and optional customer details as one atomic transaction.

        customer_details supplies name, phone and address from the billing form.
        Employee sales reuse an existing phone without changing its profile.

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

        subtotal = money(subtotal)
        discount_amount = money(discount_amount)
        total = money(total)
        gst_amount = money(gst_amount)
        if taxable_amount is not None:
            taxable_amount = money(taxable_amount)

        if taxable_amount is None:
            taxable_amount = max(money(subtotal - discount_amount), 0.0)

        if gst_rate < 0:
            raise ValueError("GST rate cannot be negative.")

        if gst_amount < 0:
            raise ValueError("GST amount cannot be negative.")

        if initial_payment_amount is None:
            # Legacy callers that omit the payment argument represent fully paid sales.
            initial_payment_amount = float(total)

        initial_payment_amount = money(initial_payment_amount)
        total = float(total)

        if initial_payment_amount < 0:
            raise ValueError("Initial payment cannot be negative.")

        if initial_payment_amount > total:
            raise ValueError(
                f"Initial payment cannot exceed bill total of Rs. {total:.2f}."
            )

        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._location(conn, godown_id)
            if sale_channel not in ('Retail', 'Wholesale'):
                raise ValueError('Choose Retail or Wholesale.')
            if employee_pricing and (godown_id != 1 or sale_channel != 'Retail'):
                raise ValueError('Only admins can change the sale channel or godown.')

            if employee_pricing or any(i.get('offer_checked') or i.get('offer_id_snapshot') for i in items):
                self._validate_offer_bill(conn, locals())

            if customer_details is not None:
                name = customer_details['name'].strip()
                phone = (customer_details.get('phone') or '').strip() or None
                address = (customer_details.get('address') or '').strip()
                if not name:
                    raise ValueError("Customer name is required.")
                existing = conn.execute('SELECT id FROM customers WHERE phone=?', (phone,)).fetchone() if phone else None
                if existing:
                    customer_id = existing['id']
                    if not employee_pricing:
                        conn.execute('UPDATE customers SET name=?,address=? WHERE id=?',
                                     (name, address, customer_id))
                else:
                    customer_id = conn.execute(
                        "INSERT INTO customers(name,phone,address,notes) VALUES (?,?,?,'')",
                        (name, phone, address)).lastrowid

            bill_no = self._next_bill_no_conn(conn, bill_date)
            conn.execute("""INSERT INTO bill_sequences(date_key, last_seq) VALUES (?, ?)
                ON CONFLICT(date_key) DO UPDATE SET last_seq=excluded.last_seq""",
                (bill_date.replace("-", ""), int(bill_no.rsplit("-", 1)[1])))

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
            conn.execute('UPDATE bills SET godown_id=?,sale_channel=? WHERE id=?', (godown_id, sale_channel, bill_id))

            for it in items:
                quantity = it["quantity"]

                if isinstance(quantity, bool) or int(quantity) != quantity or quantity <= 0:
                    raise ValueError(f"Invalid quantity for item: {it['name']}")
                quantity = int(quantity)

                line_gst_rate = float(it.get("gst_rate", gst_rate or 0.0))
                line_gst_amount = money(it.get("gst_amount", 0.0))

                if line_gst_rate < 0:
                    raise ValueError("GST rate cannot be negative.")

                if line_gst_amount < 0:
                    raise ValueError("GST amount cannot be negative.")

                stock_deducted = 0
                cost_cents = None
                if it.get("item_id"):
                    stock = conn.execute("SELECT stock_qty FROM items WHERE id=?", (it["item_id"],)).fetchone()
                    if stock is None:
                        raise ValueError("Item no longer exists. Refresh the catalog and try again.")
                    stock_deducted, cost_cents = self._stock_out(conn, it['item_id'], godown_id, quantity, bill_no, bill_date)

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
                        gst_amount,
                        stock_deducted,
                        cost_cents,
                        offer_id_snapshot,
                        offer_name_snapshot,
                        offer_discount
                    )
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        bill_id,
                        it.get("item_id"),
                        it["name"],
                        it.get("category", ""),
                        quantity,
                        money(it["rate"]),
                        money(it["subtotal"]),
                        line_gst_rate,
                        line_gst_amount,
                        stock_deducted,
                        cost_cents,
                        it.get('offer_id_snapshot'),
                        it.get('offer_name_snapshot'),
                        money(it.get('offer_discount', 0)),
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
                """SELECT bills.*, customers.name AS customer_name, customers.phone AS customer_phone,
                          g.name AS godown_name, original.bill_no AS exchange_from_no, e.returned_cents AS exchange_returned_cents,
                          e.replacement_cents AS exchange_replacement_cents,
                          (SELECT COALESCE(sum_money_cents(amount), 0)/100.0 FROM payments WHERE bill_id=bills.id) AS paid_amount
                   FROM bills LEFT JOIN customers ON customers.id = bills.customer_id
                   LEFT JOIN godowns g ON g.id=bills.godown_id
                   LEFT JOIN exchanges e ON e.exchange_bill_id=bills.id
                   LEFT JOIN bills original ON original.id=e.source_bill_id
                   WHERE bills.id=?""",
                (bill_id,),
            ).fetchone()
            items = conn.execute(
                "SELECT * FROM bill_items WHERE bill_id=?", (bill_id,)
            ).fetchall()
            if bill is not None:
                bill = dict(bill)
                bill["total"] = money(bill["total"])
                bill["paid_amount"] = money(bill["paid_amount"])
                bill["balance"] = max(money(bill["total"] - bill["paid_amount"]), 0)
            return bill, items

    def delete_bill(self, bill_id):
        """Deletes a bill and its line items, and restores stock quantities
        that were deducted when the bill was made."""
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute('SELECT 1 FROM exchanges WHERE source_bill_id=? OR exchange_bill_id=? LIMIT 1',
                            (bill_id,bill_id)).fetchone():
                raise ValueError('Bills linked to exchanges cannot be deleted; their stock and payment history must remain intact.')
            bill = conn.execute('SELECT godown_id,bill_no FROM bills WHERE id=?', (bill_id,)).fetchone()
            items = conn.execute(
                "SELECT item_id, cost_cents, COALESCE(stock_deducted, quantity) AS stock_deducted FROM bill_items WHERE bill_id=?", (bill_id,)
            ).fetchall()
            for it in items:
                if it["item_id"]:
                    self._stock_delta(conn, it['item_id'], bill['godown_id'], it['stock_deducted'],
                                      it['cost_cents'], 'Deleted '+bill['bill_no'])
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

    def stat_monthly_sales(self, date_from=None, date_to=None):
        return self.report_statistics(date_from, date_to)['monthly']

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

    # ---------------------------------------------------------- backup / export

    def backup_database(self, dest_folder=None):
        """
        Copies the live database to a timestamped file, using SQLite's own
        online backup API (safe to call while the app is open and WAL is
        active -- unlike a plain file copy, this can't grab a half-written
        page). Returns the full path to the backup file.

        dest_folder defaults to ~/.cloth_shop_billing/backups, next to the
        live database.
        """
        if dest_folder is None:
            dest_folder = os.path.join(
                os.path.dirname(self.path), "backups"
            )
        os.makedirs(dest_folder, exist_ok=True)

        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Reserve a unique path even for two backups in the same clock tick.
        fd,dest_path = tempfile.mkstemp(prefix=f"cloth_shop_backup_{stamp}_",suffix=".db",dir=dest_folder)
        os.close(fd)
        try:
            src_conn = sqlite3.connect(self.path)
            try:
                dest_conn = sqlite3.connect(dest_path)
                try:
                    src_conn.backup(dest_conn)
                finally:
                    dest_conn.close()
            finally:
                src_conn.close()
        except Exception:
            os.unlink(dest_path)
            raise
        return dest_path

    def export_all_tables_csv(self, dest_folder):
        """
        Exports every table in the database to its own CSV file inside
        dest_folder (one file per table: bills.csv, bill_items.csv,
        customers.csv, items.csv, expenses.csv, payments.csv, etc).
        This is a read-only export -- the live database is never modified.
        Returns the list of file paths written.
        """
        os.makedirs(dest_folder, exist_ok=True)
        written = []

        with self._conn() as conn:
            # All CSVs describe one committed database state, even if billing
            # continues while files are being written.
            conn.execute('BEGIN')
            tables = [
                r["name"]
                for r in conn.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type='table' AND name NOT LIKE 'sqlite_%'
                    ORDER BY name
                    """
                ).fetchall()
            ]

            for table in tables:
                identifier = '"'+table.replace('"','""')+'"'
                cursor = conn.execute(f"SELECT * FROM {identifier}")
                out_path = os.path.join(dest_folder, f"{table}.csv")
                with open(out_path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([column[0] for column in cursor.description])
                    # Iterate rows instead of loading a whole sales table into RAM.
                    for row in cursor:
                        writer.writerow(["'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value for value in row])
                written.append(out_path)

        return written
