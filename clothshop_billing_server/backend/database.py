"""
database.py
-----------
Shared SQLite database layer for the Cloth Shop Billing Server.

IMPORTANT
---------
The FastAPI server and the desktop application use the SAME database:

    ~/.cloth_shop_billing/cloth_shop.db

The server never creates a second live database.

Design goals:
- One source of truth for desktop + mobile/browser.
- WAL mode for better read/write concurrency.
- Busy timeout so short write contention is handled gracefully.
- Durable SQLite settings for crash/power-loss recovery.
- Atomic bill creation.
- Atomic bill-number generation.
- Automatic database backups.
- No partial bills.
"""

import os
import sqlite3
import shutil
from contextlib import contextmanager
from datetime import datetime


DB_FILENAME = "cloth_shop.db"

# ---------------------------------------------------------------------------
# Shared database location
# ---------------------------------------------------------------------------


def _default_db_path() -> str:
    """
    Use the same database location as the desktop application.

    Linux:
        ~/.cloth_shop_billing/cloth_shop.db

    Windows:
        C:\\Users\\<user>\\.cloth_shop_billing\\cloth_shop.db
    """
    base = os.path.join(
        os.path.expanduser("~"),
        ".cloth_shop_billing",
    )

    os.makedirs(base, exist_ok=True)

    return os.path.join(base, DB_FILENAME)


# ---------------------------------------------------------------------------
# Backup location
# ---------------------------------------------------------------------------


def _default_backup_dir() -> str:
    base = os.path.join(
        os.path.expanduser("~"),
        ".cloth_shop_billing",
        "backups",
    )

    os.makedirs(base, exist_ok=True)

    return base


MAX_BACKUPS = 200


# ---------------------------------------------------------------------------
# Database schema
# ---------------------------------------------------------------------------

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS categories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT UNIQUE NOT NULL
);

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
    subtotal             REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_items_category
    ON items(category_id);

CREATE INDEX IF NOT EXISTS idx_items_barcode
    ON items(barcode);

CREATE INDEX IF NOT EXISTS idx_bills_date
    ON bills(bill_date);

CREATE INDEX IF NOT EXISTS idx_bill_items_bill
    ON bill_items(bill_id);

CREATE INDEX IF NOT EXISTS idx_customers_phone
    ON customers(phone);
"""


# ---------------------------------------------------------------------------
# Starter data
# ---------------------------------------------------------------------------

STARTER_CATEGORIES = [
    "Shirts",
    "T-Shirts",
    "Jeans",
    "Cotton Pants",
    "Ladies",
]

STARTER_SUBTYPES = {
    "T-Shirts": [
        "Round Neck",
        "Collar",
    ],
    "Jeans": [
        "LP",
        "Mufti",
        "US Polo",
    ],
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class Database:
    def __init__(
        self,
        path: str = None,
        backup_dir: str = None,
    ):
        self.path = path or _default_db_path()
        self.backup_dir = backup_dir or _default_backup_dir()

        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

        self._init_schema()

    # ------------------------------------------------------------------
    # SQLite connection
    # ------------------------------------------------------------------

    @contextmanager
    def _conn(self, write: bool = False):
        """
        Open a short-lived SQLite connection.

        Every request gets its own connection.

        SQLite settings:
        - timeout=10:
            Wait for another writer instead of immediately throwing
            'database is locked'.

        - WAL:
            Allows readers while another connection is writing.

        - synchronous=FULL:
            Prefer durability over raw write speed.

        - foreign_keys=ON:
            Preserve relational integrity.
        """

        conn = sqlite3.connect(
            self.path,
            timeout=10,
        )

        conn.row_factory = sqlite3.Row

        try:
            # Foreign key enforcement.
            conn.execute("PRAGMA foreign_keys = ON")

            # WAL is persistent for the database.
            conn.execute("PRAGMA journal_mode = WAL")

            # Strong durability.
            conn.execute("PRAGMA synchronous = FULL")

            # Extra protection against long lock waits.
            conn.execute("PRAGMA busy_timeout = 10000")

            yield conn

            # Normal operations commit here.
            conn.commit()

        except Exception:
            # IMPORTANT:
            # If anything fails, never leave a partial transaction behind.
            conn.rollback()
            raise

        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def _init_schema(self):
        with self._conn(write=True) as conn:
            conn.executescript(SCHEMA)

            # Only insert starter data if the database is completely empty
            # of categories.
            row = conn.execute("SELECT COUNT(*) AS c FROM categories").fetchone()

            if row["c"] == 0:
                for category in STARTER_CATEGORIES:
                    conn.execute(
                        "INSERT INTO categories(name) VALUES (?)",
                        (category,),
                    )

                for category_name, subtypes in STARTER_SUBTYPES.items():
                    category_row = conn.execute(
                        "SELECT id FROM categories WHERE name=?",
                        (category_name,),
                    ).fetchone()

                    if category_row:
                        for subtype in subtypes:
                            conn.execute(
                                """
                                INSERT INTO subtypes(category_id, name)
                                VALUES (?,?)
                                """,
                                (
                                    category_row["id"],
                                    subtype,
                                ),
                            )

    # ------------------------------------------------------------------
    # BACKUPS
    # ------------------------------------------------------------------

    def backup(self, reason: str = "manual"):
        """
        Create a consistent SQLite backup.

        Uses SQLite's backup API rather than blindly copying the live
        database file.

        Returns the backup path.
        """

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        safe_reason = "".join(c if c.isalnum() or c in "-_" else "_" for c in reason)

        backup_name = f"cloth_shop_{timestamp}_{safe_reason}.db"

        backup_path = os.path.join(
            self.backup_dir,
            backup_name,
        )

        source = None
        destination = None

        try:
            source = sqlite3.connect(
                self.path,
                timeout=10,
            )

            destination = sqlite3.connect(
                backup_path,
                timeout=10,
            )

            source.backup(destination)

            destination.commit()

        finally:
            if destination is not None:
                destination.close()

            if source is not None:
                source.close()

        self._cleanup_old_backups()

        return backup_path

    def _cleanup_old_backups(self):
        """
        Keep only the newest MAX_BACKUPS files.
        """

        try:
            files = [
                os.path.join(self.backup_dir, filename)
                for filename in os.listdir(self.backup_dir)
                if filename.startswith("cloth_shop_") and filename.endswith(".db")
            ]

            files.sort(
                key=lambda path: os.path.getmtime(path),
                reverse=True,
            )

            for old_file in files[MAX_BACKUPS:]:
                try:
                    os.remove(old_file)
                except OSError:
                    pass

        except OSError:
            pass

    # ------------------------------------------------------------------
    # CATEGORIES
    # ------------------------------------------------------------------

    def get_categories(self):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT *
                FROM categories
                ORDER BY name
                """
            ).fetchall()

    def add_category(self, name: str):
        with self._conn(write=True) as conn:
            conn.execute(
                "INSERT INTO categories(name) VALUES (?)",
                (name.strip(),),
            )

    def rename_category(self, cat_id: int, new_name: str):
        with self._conn(write=True) as conn:
            conn.execute(
                """
                UPDATE categories
                SET name=?
                WHERE id=?
                """,
                (
                    new_name.strip(),
                    cat_id,
                ),
            )

    def delete_category(self, cat_id: int):
        with self._conn(write=True) as conn:
            conn.execute(
                "DELETE FROM categories WHERE id=?",
                (cat_id,),
            )

    # ------------------------------------------------------------------
    # SUBTYPES
    # ------------------------------------------------------------------

    def get_subtypes(self, category_id: int):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT *
                FROM subtypes
                WHERE category_id=?
                ORDER BY name
                """,
                (category_id,),
            ).fetchall()

    def add_subtype(self, category_id: int, name: str):
        with self._conn(write=True) as conn:
            conn.execute(
                """
                INSERT INTO subtypes(category_id, name)
                VALUES (?,?)
                """,
                (
                    category_id,
                    name.strip(),
                ),
            )

    def rename_subtype(self, subtype_id: int, new_name: str):
        with self._conn(write=True) as conn:
            conn.execute(
                """
                UPDATE subtypes
                SET name=?
                WHERE id=?
                """,
                (
                    new_name.strip(),
                    subtype_id,
                ),
            )

    def delete_subtype(self, subtype_id: int):
        with self._conn(write=True) as conn:
            conn.execute(
                "DELETE FROM subtypes WHERE id=?",
                (subtype_id,),
            )

    # ------------------------------------------------------------------
    # ITEMS
    # ------------------------------------------------------------------

    def get_items(
        self,
        category_id=None,
        subtype_id=None,
        active_only=True,
        search=None,
    ):
        q = """
            SELECT items.*,
                   categories.name AS category_name,
                   subtypes.name AS subtype_name
            FROM items
            JOIN categories
                ON categories.id = items.category_id
            LEFT JOIN subtypes
                ON subtypes.id = items.subtype_id
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
                """
                SELECT items.*,
                       categories.name AS category_name,
                       subtypes.name AS subtype_name
                FROM items
                JOIN categories
                    ON categories.id = items.category_id
                LEFT JOIN subtypes
                    ON subtypes.id = items.subtype_id
                WHERE items.barcode=?
                  AND items.active=1
                """,
                (barcode.strip(),),
            ).fetchone()

    def get_item_by_id(self, item_id: int):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT items.*,
                       categories.name AS category_name,
                       subtypes.name AS subtype_name
                FROM items
                JOIN categories
                    ON categories.id = items.category_id
                LEFT JOIN subtypes
                    ON subtypes.id = items.subtype_id
                WHERE items.id=?
                """,
                (item_id,),
            ).fetchone()

    def find_item_by_name(
        self,
        category_id,
        subtype_id,
        name,
    ):
        with self._conn() as conn:
            q = """
                SELECT *
                FROM items
                WHERE category_id=?
                  AND name=?
            """

            params = [
                category_id,
                name,
            ]

            if subtype_id:
                q += " AND subtype_id=?"
                params.append(subtype_id)
            else:
                q += " AND subtype_id IS NULL"

            return conn.execute(
                q,
                params,
            ).fetchone()

    def add_item(
        self,
        name,
        category_id,
        subtype_id,
        barcode,
        size,
        color,
        rate,
        stock_qty=0,
    ):
        with self._conn(write=True) as conn:
            cur = conn.execute(
                """
                INSERT INTO items(
                    name,
                    category_id,
                    subtype_id,
                    barcode,
                    size,
                    color,
                    rate,
                    stock_qty
                )
                VALUES (?,?,?,?,?,?,?,?)
                """,
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
        with self._conn(write=True) as conn:
            conn.execute(
                """
                UPDATE items
                SET name=?,
                    category_id=?,
                    subtype_id=?,
                    barcode=?,
                    size=?,
                    color=?,
                    rate=?,
                    stock_qty=?,
                    active=?
                WHERE id=?
                """,
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
        with self._conn(write=True) as conn:
            conn.execute(
                """
                UPDATE items
                SET active=0
                WHERE id=?
                """,
                (item_id,),
            )

    def adjust_stock(self, item_id, delta):
        with self._conn(write=True) as conn:
            conn.execute(
                """
                UPDATE items
                SET stock_qty = stock_qty + ?
                WHERE id=?
                """,
                (
                    delta,
                    item_id,
                ),
            )

    # ------------------------------------------------------------------
    # CUSTOMERS
    # ------------------------------------------------------------------

    def search_customers(self, text=""):
        with self._conn() as conn:
            if text:
                return conn.execute(
                    """
                    SELECT *
                    FROM customers
                    WHERE name LIKE ?
                       OR phone LIKE ?
                    ORDER BY name
                    """,
                    (
                        f"%{text}%",
                        f"%{text}%",
                    ),
                ).fetchall()

            return conn.execute(
                """
                SELECT *
                FROM customers
                ORDER BY name
                """
            ).fetchall()

    def get_customer_by_phone(self, phone):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT *
                FROM customers
                WHERE phone=?
                """,
                (phone.strip(),),
            ).fetchone()

    def get_customer_by_id(self, cid):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT *
                FROM customers
                WHERE id=?
                """,
                (cid,),
            ).fetchone()

    def add_customer(
        self,
        name,
        phone,
        address="",
        notes="",
    ):
        with self._conn(write=True) as conn:
            cur = conn.execute(
                """
                INSERT INTO customers(
                    name,
                    phone,
                    address,
                    notes
                )
                VALUES (?,?,?,?)
                """,
                (
                    name.strip(),
                    (phone.strip() if phone else None) or None,
                    address,
                    notes,
                ),
            )

            return cur.lastrowid

    def update_customer(
        self,
        cid,
        name,
        phone,
        address,
        notes,
    ):
        with self._conn(write=True) as conn:
            conn.execute(
                """
                UPDATE customers
                SET name=?,
                    phone=?,
                    address=?,
                    notes=?
                WHERE id=?
                """,
                (
                    name.strip(),
                    (phone.strip() if phone else None) or None,
                    address,
                    notes,
                    cid,
                ),
            )

    def delete_customer(self, cid):
        with self._conn(write=True) as conn:
            conn.execute(
                "DELETE FROM customers WHERE id=?",
                (cid,),
            )

    def get_customer_purchase_history(self, cid):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT *
                FROM bills
                WHERE customer_id=?
                ORDER BY bill_date DESC
                """,
                (cid,),
            ).fetchall()

    def get_customer_total_spent(self, cid):
        with self._conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(total),0) AS s,
                    COUNT(*) AS c
                FROM bills
                WHERE customer_id=?
                """,
                (cid,),
            ).fetchone()

            return row["s"], row["c"]

    # ------------------------------------------------------------------
    # WISHLIST
    # ------------------------------------------------------------------

    def add_wishlist(
        self,
        customer_id,
        description,
    ):
        with self._conn(write=True) as conn:
            conn.execute(
                """
                INSERT INTO customer_wishlist(
                    customer_id,
                    item_description
                )
                VALUES (?,?)
                """,
                (
                    customer_id,
                    description.strip(),
                ),
            )

    def get_wishlist(self, customer_id):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT *
                FROM customer_wishlist
                WHERE customer_id=?
                ORDER BY fulfilled ASC,
                         date_added DESC
                """,
                (customer_id,),
            ).fetchall()

    def set_wishlist_fulfilled(
        self,
        wishlist_id,
        fulfilled=1,
    ):
        with self._conn(write=True) as conn:
            conn.execute(
                """
                UPDATE customer_wishlist
                SET fulfilled=?
                WHERE id=?
                """,
                (
                    fulfilled,
                    wishlist_id,
                ),
            )

    def delete_wishlist(self, wishlist_id):
        with self._conn(write=True) as conn:
            conn.execute(
                """
                DELETE FROM customer_wishlist
                WHERE id=?
                """,
                (wishlist_id,),
            )

    def all_open_wishlist(self):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT
                    cw.item_description,
                    cw.date_added,
                    c.name AS customer_name,
                    c.phone AS customer_phone
                FROM customer_wishlist cw
                JOIN customers c
                    ON c.id = cw.customer_id
                WHERE cw.fulfilled=0
                ORDER BY cw.date_added DESC
                """
            ).fetchall()

    # ------------------------------------------------------------------
    # BILL NUMBER
    # -----------------------------------------------------------------

    # ------------------------------------------------------------------
    # BILLS
    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # BILL NUMBER
    # ------------------------------------------------------------------

    def _next_bill_no_conn(self, conn, bill_date=None):
        """
        Generate the next bill number for the selected bill date.

        Example:
            2026-09-06 -> INV-20260906-001
            2026-09-06 -> INV-20260906-002
            2026-09-05 -> INV-20260905-001

        IMPORTANT:
        This method must not open another connection.

        save_bill() calls BEGIN IMMEDIATE before calling this method,
        so bill-number generation is atomic.
        """

        if bill_date is None:
            bill_date = datetime.now().strftime("%Y-%m-%d")

        date_key = bill_date.replace("-", "")
        prefix = f"INV-{date_key}-"

        row = conn.execute(
            """
            SELECT COALESCE(
                MAX(
                    CAST(
                        substr(bill_no, ?) AS INTEGER
                    )
                ),
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

        sequence = row["max_seq"] + 1

        return f"{prefix}{sequence:03d}"

    def next_bill_no(self, bill_date=None):
        """
        Preview the next bill number for a selected date.

        WARNING:
        This does NOT reserve the number.

        The actual bill number is generated atomically
        inside save_bill().
        """

        with self._conn() as conn:
            return self._next_bill_no_conn(conn, bill_date)

    # ------------------------------------------------------------------
    # ATOMIC BILL CREATION
    # ------------------------------------------------------------------

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
    ):
        """
        Save an entire bill as ONE atomic transaction.

        Bill date can be selected by the user.

        If no date is supplied, today's date is used.

        The following operations either ALL succeed or ALL rollback:

        1. Bill creation
        2. Bill item creation
        3. Stock deduction
        4. Bill-number generation

        IMPORTANT:
        Stock availability is NOT enforced.

        The recorded stock may be wrong because physical stock
        has not necessarily been counted yet. Therefore a bill
        is allowed even when recorded stock is low or zero.
        """

        if not items:
            raise ValueError("Cannot save an empty bill.")

        # --------------------------------------------------------------
        # Default to today's date
        # --------------------------------------------------------------

        if bill_date is None:
            bill_date = datetime.now().strftime("%Y-%m-%d")

        # --------------------------------------------------------------
        # Validate date format
        # --------------------------------------------------------------

        try:
            selected_date = datetime.strptime(
                bill_date,
                "%Y-%m-%d",
            ).date()
        except ValueError:
            raise ValueError("Invalid bill date. Expected YYYY-MM-DD.")

        # --------------------------------------------------------------
        # Do not allow future bills
        # --------------------------------------------------------------

        if selected_date > datetime.now().date():
            raise ValueError("Bill date cannot be in the future.")

        # --------------------------------------------------------------
        # ATOMIC TRANSACTION
        # --------------------------------------------------------------

        with self._conn(write=True) as conn:
            # Get SQLite write lock before generating bill number.
            conn.execute("BEGIN IMMEDIATE")

            # ----------------------------------------------------------
            # Generate bill number based on SELECTED date
            # ----------------------------------------------------------

            bill_no = self._next_bill_no_conn(
                conn,
                bill_date,
            )

            # ----------------------------------------------------------
            # Insert bill
            # ----------------------------------------------------------

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
                    payment_mode
                )
                VALUES (?,?,?,?,?,?,?,?)
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
                ),
            )

            bill_id = cur.lastrowid

            # ----------------------------------------------------------
            # Insert bill items + deduct stock
            # ----------------------------------------------------------

            for item in items:
                item_id = item.get("item_id")
                quantity = item["quantity"]

                # Basic quantity validation.
                if quantity <= 0:
                    raise ValueError(f"Invalid quantity for item: {item['name']}")

                # ------------------------------------------------------
                # Save historical item snapshot
                # ------------------------------------------------------

                conn.execute(
                    """
                    INSERT INTO bill_items(
                        bill_id,
                        item_id,
                        item_name_snapshot,
                        category_snapshot,
                        quantity,
                        rate,
                        subtotal
                    )
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (
                        bill_id,
                        item_id,
                        item["name"],
                        item.get("category"),
                        quantity,
                        item["rate"],
                        item["subtotal"],
                    ),
                )

                # ------------------------------------------------------
                # Deduct recorded stock.
                #
                # IMPORTANT:
                # We DO NOT check whether enough stock exists.
                #
                # Example:
                # recorded stock = 2
                # customer buys = 5
                #
                # Bill is still allowed.
                # Stock becomes 0.
                # ------------------------------------------------------

                if item_id:
                    updated = conn.execute(
                        """
                        UPDATE items
                        SET stock_qty = MAX(stock_qty - ?, 0)
                        WHERE id = ?
                        """,
                        (
                            quantity,
                            item_id,
                        ),
                    )

                    # The item ID should exist if it came from inventory.
                    # This is NOT a stock-availability check.
                    if updated.rowcount != 1:
                        raise RuntimeError(
                            f"Inventory item {item_id} "
                            f"was not found. Bill was rolled back."
                        )

            # ----------------------------------------------------------
            # The context manager commits here.
            # ----------------------------------------------------------

            return bill_id, bill_no

    def get_bill(self, bill_id):
        with self._conn() as conn:
            bill = conn.execute(
                """
                SELECT
                    bills.*,
                    customers.name AS customer_name,
                    customers.phone AS customer_phone
                FROM bills
                LEFT JOIN customers
                    ON customers.id = bills.customer_id
                WHERE bills.id=?
                """,
                (bill_id,),
            ).fetchone()

            items = conn.execute(
                """
                SELECT *
                FROM bill_items
                WHERE bill_id=?
                """,
                (bill_id,),
            ).fetchall()

            return bill, items

    def delete_bill(self, bill_id):
        """
        Delete a bill and restore its stock.

        This is also atomic.
        """

        with self._conn(write=True) as conn:
            conn.execute("BEGIN IMMEDIATE")

            items = conn.execute(
                """
                SELECT item_id, quantity
                FROM bill_items
                WHERE bill_id=?
                """,
                (bill_id,),
            ).fetchall()

            for item in items:
                if item["item_id"]:
                    conn.execute(
                        """
                        UPDATE items
                        SET stock_qty = stock_qty + ?
                        WHERE id=?
                        """,
                        (
                            item["quantity"],
                            item["item_id"],
                        ),
                    )

            conn.execute(
                "DELETE FROM bills WHERE id=?",
                (bill_id,),
            )

    def search_bills(
        self,
        date_from=None,
        date_to=None,
        search_text=None,
    ):
        q = """
            SELECT
                bills.*,
                customers.name AS customer_name,
                customers.phone AS customer_phone,

                (
                    SELECT COUNT(*)
                    FROM bill_items
                    WHERE bill_items.bill_id = bills.id
                ) AS item_count,

                (
                    SELECT COALESCE(SUM(quantity),0)
                    FROM bill_items
                    WHERE bill_items.bill_id = bills.id
                ) AS piece_count

            FROM bills
            LEFT JOIN customers
                ON customers.id = bills.customer_id
            WHERE 1=1
        """

        params = []

        if date_from:
            q += " AND date(bills.bill_date) >= date(?)"
            params.append(date_from)

        if date_to:
            q += " AND date(bills.bill_date) <= date(?)"
            params.append(date_to)

        if search_text:
            q += """
                AND (
                    bills.bill_no LIKE ?
                    OR customers.name LIKE ?
                    OR customers.phone LIKE ?
                )
            """

            like = f"%{search_text}%"

            params.extend(
                [
                    like,
                    like,
                    like,
                ]
            )

        q += """
            ORDER BY bills.bill_date DESC
        """

        with self._conn() as conn:
            return conn.execute(
                q,
                params,
            ).fetchall()

    # ------------------------------------------------------------------
    # STATISTICS
    # ------------------------------------------------------------------

    def stat_totals(
        self,
        date_from=None,
        date_to=None,
    ):
        q = """
            SELECT
                COALESCE(SUM(total),0) AS revenue,
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
            row = conn.execute(
                q,
                params,
            ).fetchone()

            qp = """
                SELECT
                    COALESCE(SUM(bi.quantity),0) AS pieces
                FROM bill_items bi
                JOIN bills b
                    ON b.id = bi.bill_id
                WHERE 1=1
            """

            pparams = []

            if date_from:
                qp += " AND date(b.bill_date) >= date(?)"
                pparams.append(date_from)

            if date_to:
                qp += " AND date(b.bill_date) <= date(?)"
                pparams.append(date_to)

            pieces = conn.execute(
                qp,
                pparams,
            ).fetchone()["pieces"]

            return {
                "revenue": row["revenue"],
                "bill_count": row["bill_count"],
                "pieces": pieces,
                "avg_bill": (
                    row["revenue"] / row["bill_count"] if row["bill_count"] else 0
                ),
            }

    def stat_daily_sales(
        self,
        date_from=None,
        date_to=None,
    ):
        q = """
            SELECT
                date(bill_date) AS d,
                SUM(total) AS revenue
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

        q += """
            GROUP BY date(bill_date)
            ORDER BY d
        """

        with self._conn() as conn:
            return conn.execute(
                q,
                params,
            ).fetchall()

    def stat_monthly_sales(self):
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT
                    strftime('%Y-%m', bill_date) AS month,
                    SUM(total) AS revenue,
                    COUNT(*) AS bill_count
                FROM bills
                GROUP BY month
                ORDER BY month
                """
            ).fetchall()

    def stat_top_items(
        self,
        date_from=None,
        date_to=None,
        limit=10,
        by="quantity",
    ):
        order_col = "total_qty" if by == "quantity" else "total_revenue"

        q = f"""
            SELECT
                COALESCE(
                    items.name,
                    bi.item_name_snapshot
                ) AS name,

                COALESCE(
                    categories.name,
                    bi.category_snapshot
                ) AS category,

                SUM(bi.quantity) AS total_qty,
                SUM(bi.subtotal) AS total_revenue

            FROM bill_items bi

            JOIN bills b
                ON b.id = bi.bill_id

            LEFT JOIN items
                ON items.id = bi.item_id

            LEFT JOIN categories
                ON categories.id = items.category_id

            WHERE 1=1
        """

        params = []

        if date_from:
            q += " AND date(b.bill_date) >= date(?)"
            params.append(date_from)

        if date_to:
            q += " AND date(b.bill_date) <= date(?)"
            params.append(date_to)

        q += f"""
            GROUP BY
                COALESCE(items.name, bi.item_name_snapshot),
                COALESCE(categories.name, bi.category_snapshot)

            ORDER BY {order_col} DESC

            LIMIT ?
        """

        params.append(limit)

        with self._conn() as conn:
            return conn.execute(
                q,
                params,
            ).fetchall()

    def stat_category_sales(
        self,
        date_from=None,
        date_to=None,
    ):
        q = """
            SELECT
                COALESCE(
                    categories.name,
                    bi.category_snapshot
                ) AS category,

                SUM(bi.quantity) AS qty,
                SUM(bi.subtotal) AS revenue

            FROM bill_items bi

            JOIN bills b
                ON b.id = bi.bill_id

            LEFT JOIN items
                ON items.id = bi.item_id

            LEFT JOIN categories
                ON categories.id = items.category_id

            WHERE 1=1
        """

        params = []

        if date_from:
            q += " AND date(b.bill_date) >= date(?)"
            params.append(date_from)

        if date_to:
            q += " AND date(b.bill_date) <= date(?)"
            params.append(date_to)

        q += """
            GROUP BY
                COALESCE(
                    categories.name,
                    bi.category_snapshot
                )

            ORDER BY revenue DESC
        """

        with self._conn() as conn:
            return conn.execute(
                q,
                params,
            ).fetchall()

    def stat_top_customers(
        self,
        date_from=None,
        date_to=None,
        limit=10,
    ):
        q = """
            SELECT
                c.name AS name,
                c.phone AS phone,
                COUNT(b.id) AS visits,
                SUM(b.total) AS total_spent

            FROM bills b

            JOIN customers c
                ON c.id = b.customer_id

            WHERE 1=1
        """

        params = []

        if date_from:
            q += " AND date(b.bill_date) >= date(?)"
            params.append(date_from)

        if date_to:
            q += " AND date(b.bill_date) <= date(?)"
            params.append(date_to)

        q += """
            GROUP BY c.id
            ORDER BY total_spent DESC
            LIMIT ?
        """

        params.append(limit)

        with self._conn() as conn:
            return conn.execute(
                q,
                params,
            ).fetchall()
