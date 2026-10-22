"""
customers_tab.py
-----------------
Search customers, see their purchase history, manage customer profiles,
and track what they want next time.

Customers can also be created without making a bill.
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
)

from PySide6.QtCore import Qt
from receipt import ReceiptDialog
from paging import Pager

from widgets import (
    rupees,
    make_heading,
    confirm_delete_password,
)
from receipt import ReceiptDialog
from paging import Pager


class CustomersTab(QWidget):
    def __init__(self, db, autoload=True):
        super().__init__()

        self.db = db
        self.selected_customer_id = None
        self.create_mode = False
        self._history_cache = []

        self._build_ui()
        if autoload:
            self._refresh_customer_list()

    # ============================================================
    # UI
    # ============================================================

    def _build_ui(self):

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        outer.addWidget(
            make_heading(
                "Customers",
                "Search by name or phone, view history, track what they want next",
            )
        )

        content = QHBoxLayout()
        content.setSpacing(14)

        outer.addLayout(content, 1)

        # ========================================================
        # LEFT - CUSTOMER LIST
        # ========================================================

        left_box = QGroupBox("All Customers")

        left_box.setMinimumWidth(360)
        left_box.setMaximumWidth(420)

        left_v = QVBoxLayout(left_box)
        left_v.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search name or phone...")

        self.search_input.textChanged.connect(self._refresh_customer_list)

        left_v.addWidget(self.search_input)

        self.customer_table = QTableWidget(0, 3)

        self.customer_table.setHorizontalHeaderLabels(
            [
                "Name",
                "Phone",
                "Spent",
            ]
        )

        header = self.customer_table.horizontalHeader()

        header.setSectionResizeMode(
            0,
            QHeaderView.Stretch,
        )

        header.setSectionResizeMode(
            1,
            QHeaderView.ResizeToContents,
        )

        header.setSectionResizeMode(
            2,
            QHeaderView.ResizeToContents,
        )

        self.customer_table.setEditTriggers(QTableWidget.NoEditTriggers)

        self.customer_table.setSelectionBehavior(QTableWidget.SelectRows)

        self.customer_table.itemSelectionChanged.connect(self._on_customer_selected)

        left_v.addWidget(self.customer_table)
        self.customer_pager = Pager()
        self.customer_pager.changed.connect(self._refresh_customer_list)
        left_v.addWidget(self.customer_pager)

        content.addWidget(left_box)

        # ========================================================
        # RIGHT SIDE
        # ========================================================

        right_v = QVBoxLayout()

        content.addLayout(
            right_v,
            1,
        )

        # ========================================================
        # DETAILS
        # ========================================================

        details_box = QGroupBox("Details")

        details_v = QVBoxLayout(details_box)

        # --------------------------------------------------------
        # Name + phone
        # --------------------------------------------------------

        info_row = QHBoxLayout()

        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Name")

        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Phone")

        info_row.addWidget(self.name_input)

        info_row.addWidget(self.phone_input)

        details_v.addLayout(info_row)

        # --------------------------------------------------------
        # Address
        # --------------------------------------------------------

        self.address_input = QLineEdit()

        self.address_input.setPlaceholderText("Address")

        details_v.addWidget(self.address_input)

        # --------------------------------------------------------
        # Buttons
        # --------------------------------------------------------

        save_row = QHBoxLayout()

        # NEW CUSTOMER
        new_customer_btn = QPushButton("+ New Customer")

        new_customer_btn.clicked.connect(self._start_new_customer)

        save_row.addWidget(new_customer_btn)

        save_row.addStretch()

        # REMOVE CUSTOMER
        self.remove_customer_btn = QPushButton("Remove Customer")

        self.remove_customer_btn.setProperty(
            "role",
            "danger",
        )

        self.remove_customer_btn.clicked.connect(self._delete_customer)

        save_row.addWidget(self.remove_customer_btn)

        # SAVE / CREATE
        self.save_customer_btn = QPushButton("Save Changes")

        self.save_customer_btn.clicked.connect(self._save_customer_details)

        save_row.addWidget(self.save_customer_btn)

        details_v.addLayout(save_row)

        # --------------------------------------------------------
        # Summary
        # --------------------------------------------------------

        self.summary_label = QLabel("")

        self.summary_label.setStyleSheet("color: #6c757d; font-size: 12px;")

        details_v.addWidget(self.summary_label)

        right_v.addWidget(details_box)

        # ========================================================
        # PURCHASE HISTORY
        # ========================================================

        history_box = QGroupBox("Purchase History")

        history_v = QVBoxLayout(history_box)

        self.history_table = QTableWidget(
            0,
            6,
        )

        self.history_table.verticalHeader().setDefaultSectionSize(40)

        self.history_table.setHorizontalHeaderLabels(
            [
                "Bill No",
                "Date",
                "Items",
                "Total",
                "",
                "",
            ]
        )

        self.history_table.horizontalHeader().setSectionResizeMode(
            1,
            QHeaderView.Stretch,
        )

        self.history_table.horizontalHeader().setSectionResizeMode(
            4,
            QHeaderView.Fixed,
        )
        self.history_table.horizontalHeader().setSectionResizeMode(
            5,
            QHeaderView.Fixed,
        )

        self.history_table.setColumnWidth(
            4,
            80,
        )
        self.history_table.setColumnWidth(
            5,
            90,
        )

        self.history_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.history_table.cellClicked.connect(self._open_history_receipt)
        self.history_table.setToolTip("Click a bill row to view or print its receipt.")
        self.history_table.setSelectionBehavior(QTableWidget.SelectRows)

        # Bug fix: the only way to open a bill from Purchase History was
        # to double-click the row, which most people never discover --
        # they single-click (which just selects the row) and conclude
        # nothing happens. There's now a dedicated "View" button per
        # row, in addition to keeping double-click for people who do
        # know the shortcut.
        self.history_table.doubleClicked.connect(self._view_bill)

        history_v.addWidget(self.history_table)
        self.history_pager = Pager()
        self.history_pager.changed.connect(lambda: self._load_customer(self.selected_customer_id) if self.selected_customer_id else None)
        history_v.addWidget(self.history_pager)

        history_hint = QLabel("Click \u201cView\u201d (or double-click a row) to open / reprint a bill.")
        history_hint.setStyleSheet("color: #6c757d; font-size: 11px;")
        history_v.addWidget(history_hint)

        right_v.addWidget(
            history_box,
            1,
        )

        # ========================================================
        # WISHLIST
        # ========================================================

        wishlist_box = QGroupBox("Wants Next")

        wishlist_v = QVBoxLayout(wishlist_box)

        add_row = QHBoxLayout()

        self.wishlist_input = QLineEdit()

        self.wishlist_input.setPlaceholderText("e.g. Wants LP jeans size 34 in black")

        add_row.addWidget(self.wishlist_input)

        add_wish_btn = QPushButton("+ Add")

        add_wish_btn.clicked.connect(self._add_wishlist)

        add_row.addWidget(add_wish_btn)

        wishlist_v.addLayout(add_row)

        self.wishlist_table = QTableWidget(
            0,
            3,
        )

        self.wishlist_table.verticalHeader().setDefaultSectionSize(40)

        self.wishlist_table.setHorizontalHeaderLabels(
            [
                "Wants",
                "Added",
                "",
            ]
        )

        self.wishlist_table.horizontalHeader().setSectionResizeMode(
            0,
            QHeaderView.Stretch,
        )

        self.wishlist_table.horizontalHeader().setSectionResizeMode(
            2,
            QHeaderView.Fixed,
        )

        self.wishlist_table.setColumnWidth(
            2,
            90,
        )

        self.wishlist_table.setEditTriggers(QTableWidget.NoEditTriggers)

        wishlist_v.addWidget(self.wishlist_table)

        right_v.addWidget(
            wishlist_box,
            1,
        )

    # ============================================================
    # CUSTOMER LIST
    # ============================================================

    def _refresh_customer_list(self):

        text = self.search_input.text().strip()

        self.customer_pager.filter(text)
        total, customers = self.db.customer_page(text,self.customer_pager.size,self.customer_pager.offset)
        self.customer_pager.set_total(total)
        self.customer_table.blockSignals(True)

        self.customer_table.setRowCount(len(customers))

        self._customer_ids = []

        for row_idx, c in enumerate(customers):
            self._customer_ids.append(c["id"])

            total_spent = c["total_spent"]

            self.customer_table.setItem(
                row_idx,
                0,
                QTableWidgetItem(c["name"]),
            )

            self.customer_table.setItem(
                row_idx,
                1,
                QTableWidgetItem(c["phone"] or "-"),
            )

            self.customer_table.setItem(
                row_idx,
                2,
                QTableWidgetItem(rupees(total_spent)),
            )

        self.customer_table.clearSelection()
        if self.selected_customer_id in self._customer_ids:
            self.customer_table.selectRow(self._customer_ids.index(self.selected_customer_id))
            self._load_customer(self.selected_customer_id)
        elif self.selected_customer_id is not None:
            self._start_new_customer()
        self.customer_table.blockSignals(False)

    # ============================================================
    # CUSTOMER SELECTION
    # ============================================================

    def _on_customer_selected(self):

        rows = self.customer_table.selectionModel().selectedRows()

        if not rows:
            return

        row_idx = rows[0].row()

        cid = self._customer_ids[row_idx]

        self.selected_customer_id = cid

        self._load_customer(cid)

    # ============================================================
    # LOAD CUSTOMER
    # ============================================================

    def _load_customer(self, cid):

        self.create_mode = False

        self.save_customer_btn.setText("Save Changes")

        customer = self.db.get_customer_by_id(cid)

        if not customer:
            return

        self.name_input.setText(customer["name"])

        self.phone_input.setText(customer["phone"] or "")

        self.address_input.setText(customer["address"] or "")

        self.history_pager.filter(cid)
        result = self.db.sales_page(limit=self.history_pager.size,offset=self.history_pager.offset,customer=cid)
        self.history_pager.set_total(result['summary']['count'])
        self.summary_label.setText(
            f"Customer since {customer['created_at'][:10]} • "
            f"{result['summary']['count']} visit(s) • "
            f"{rupees(result['summary']['revenue'])} total spent"
        )
        history = result['rows']
        self._history_cache = history

        self._history_bill_ids = [b["id"] for b in history]
        self.history_table.setRowCount(len(history))

        for row_idx, b in enumerate(history):
            self.history_table.setItem(
                row_idx,
                0,
                QTableWidgetItem(b["bill_no"]),
            )

            self.history_table.setItem(
                row_idx,
                1,
                QTableWidgetItem(b["bill_date"]),
            )

            # Performance fix: piece_count now comes straight from
            # get_customer_purchase_history()'s query instead of an
            # extra get_bill() call per row (see database.py).
            piece_count = b["piece_count"]

            self.history_table.setItem(
                row_idx,
                2,
                QTableWidgetItem(str(piece_count)),
            )

            self.history_table.setItem(
                row_idx,
                3,
                QTableWidgetItem(rupees(b["total"])),
            )

            bill_id = b["id"]

            view_btn = QPushButton("View")
            view_btn.setProperty("role", "secondary")
            view_btn.setProperty("compact", "true")
            view_btn.clicked.connect(
                lambda _, bid=bill_id: self._view_bill_by_id(bid)
            )
            self.history_table.setCellWidget(
                row_idx,
                4,
                view_btn,
            )

            remove_btn = QPushButton("Remove")

            remove_btn.setProperty(
                "role",
                "danger",
            )

            remove_btn.setProperty(
                "compact",
                "true",
            )

            remove_btn.clicked.connect(
                lambda _, bid=bill_id, no=b["bill_no"]: self._delete_bill(
                    bid,
                    no,
                )
            )

            self.history_table.setCellWidget(
                row_idx,
                5,
                remove_btn,
            )

        self.history_table.resizeRowsToContents()

        self._refresh_wishlist(cid)

    # ============================================================
    # WISHLIST
    # ============================================================

    def _refresh_wishlist(self, cid):

        wishlist = self.db.get_wishlist(cid)

        self.wishlist_table.setRowCount(len(wishlist))

        for row_idx, w in enumerate(wishlist):
            desc_item = QTableWidgetItem(w["item_description"])

            if w["fulfilled"]:
                desc_item.setForeground(Qt.gray)

                font = desc_item.font()

                font.setStrikeOut(True)

                desc_item.setFont(font)

            self.wishlist_table.setItem(
                row_idx,
                0,
                desc_item,
            )

            self.wishlist_table.setItem(
                row_idx,
                1,
                QTableWidgetItem(w["date_added"][:10]),
            )

            btn = QPushButton("Undo" if w["fulfilled"] else "Got it ✓")

            btn.setProperty(
                "role",
                "secondary",
            )

            btn.setProperty(
                "compact",
                "true",
            )

            wid = w["id"]

            new_state = 0 if w["fulfilled"] else 1

            btn.clicked.connect(
                lambda _, i=wid, s=new_state: self._toggle_wishlist(
                    i,
                    s,
                )
            )

            self.wishlist_table.setCellWidget(
                row_idx,
                2,
                btn,
            )

        self.wishlist_table.resizeRowsToContents()

    def _toggle_wishlist(
        self,
        wishlist_id,
        state,
    ):

        self.db.set_wishlist_fulfilled(
            wishlist_id,
            state,
        )

        if self.selected_customer_id:
            self._refresh_wishlist(self.selected_customer_id)

    # ============================================================
    # CREATE NEW CUSTOMER
    # ============================================================

    def _start_new_customer(self):

        self.create_mode = True

        self.selected_customer_id = None

        # Clear previous customer
        self.name_input.clear()
        self.phone_input.clear()
        self.address_input.clear()
        self.summary_label.clear()

        # Clear old history
        self.history_pager.set_total(0)
        self._history_bill_ids = []
        self.history_table.setRowCount(0)

        # Clear old wishlist
        self.wishlist_table.setRowCount(0)

        # Change button
        self.save_customer_btn.setText("Create Customer")

        self.name_input.setFocus()

    # ============================================================
    # SAVE / CREATE CUSTOMER
    # ============================================================

    def _save_customer_details(self):

        name = self.name_input.text().strip()
        phone = self.phone_input.text().strip()
        address = self.address_input.text().strip()

        if not name:
            QMessageBox.warning(
                self,
                "Missing info",
                "Name cannot be empty.",
            )

            return

        # --------------------------------------------------------
        # CREATE
        # --------------------------------------------------------

        if self.create_mode:
            try:
                customer_id = self.db.add_customer(
                    name,
                    phone,
                    address,
                    "",
                )

                self.create_mode = False

                self.selected_customer_id = customer_id

                self.save_customer_btn.setText("Save Changes")

                self._refresh_customer_list()

                self._load_customer(customer_id)

                QMessageBox.information(
                    self,
                    "Customer Created",
                    f"{name} has been added to your customers.",
                )

            except Exception as e:
                QMessageBox.warning(
                    self,
                    "Could not create customer",
                    str(e),
                )

            return

        # --------------------------------------------------------
        # UPDATE
        # --------------------------------------------------------

        if not self.selected_customer_id:
            QMessageBox.information(
                self,
                "No customer selected",
                "Click '+ New Customer' to create a customer.",
            )

            return

        try:
            self.db.update_customer(
                self.selected_customer_id,
                name,
                phone,
                address,
            )

            self._refresh_customer_list()

            QMessageBox.information(
                self,
                "Saved",
                "Customer details updated.",
            )

        except Exception as e:
            QMessageBox.warning(
                self,
                "Could not save",
                str(e),
            )

    # ============================================================
    # ADD WISHLIST
    # ============================================================

    def _add_wishlist(self):

        if not self.selected_customer_id:
            QMessageBox.information(
                self,
                "No customer selected",
                "Select a customer first.",
            )

            return

        text = self.wishlist_input.text().strip()

        if not text:
            return

        self.db.add_wishlist(
            self.selected_customer_id,
            text,
        )

        self.wishlist_input.clear()

        self._refresh_wishlist(self.selected_customer_id)

    # ============================================================
    # VIEW BILL (from purchase history)
    # ============================================================

    def _view_bill_by_id(self, bill_id):
        bill_row, bill_items = self.db.get_bill(bill_id)
        if bill_row is None:
            QMessageBox.warning(self, "Bill not found", "That bill could not be loaded.")
            return
        dialog = ReceiptDialog(bill_row, bill_items, self)
        dialog.exec()

    def _view_bill(self):
        rows = self.history_table.selectionModel().selectedRows()
        if not rows:
            return
        row_idx = rows[0].row()
        if row_idx >= len(self._history_cache):
            return
        bill_id = self._history_cache[row_idx]["id"]
        bill_row, bill_items = self.db.get_bill(bill_id)
        if bill_row is None:
            QMessageBox.warning(self, "Bill not found", "That bill could not be loaded.")
            return
        dialog = ReceiptDialog(bill_row, bill_items, self)
        dialog.exec()

    # ============================================================
    # DELETE BILL
    # ============================================================

    def _delete_bill(
        self,
        bill_id,
        bill_no,
    ):

        confirm = QMessageBox.question(
            self,
            "Remove Bill",
            f"Remove bill '{bill_no}'? This cannot be undone.",
        )

        if confirm != QMessageBox.Yes:
            return

        if not confirm_delete_password(
            self,
            f"remove bill '{bill_no}'",
        ):
            return

        try:
            self.db.delete_bill(bill_id)
        except ValueError as exc:
            QMessageBox.warning(self, 'Cannot remove bill', str(exc))
            return

        self._refresh_customer_list()

        if self.selected_customer_id:
            self._load_customer(self.selected_customer_id)

    # ============================================================
    # DELETE CUSTOMER
    # ============================================================

    def _delete_customer(self):

        if not self.selected_customer_id:
            QMessageBox.information(
                self,
                "No customer selected",
                "Select a customer from the list first.",
            )

            return

        name = self.name_input.text().strip() or "this customer"

        confirm = QMessageBox.question(
            self,
            "Remove Customer",
            f"Remove {name} from your customer list?\n\n"
            "Their past bills stay in your sales records "
            "as walk-in sales, but their profile and "
            "wishlist will be deleted.",
        )

        if confirm != QMessageBox.Yes:
            return

        if not confirm_delete_password(
            self,
            f"remove {name}",
        ):
            return

        self.db.delete_customer(self.selected_customer_id)

        self.selected_customer_id = None
        self.create_mode = False

        self.name_input.clear()
        self.phone_input.clear()
        self.address_input.clear()

        self.summary_label.clear()

        self.history_pager.set_total(0)
        self._history_bill_ids = []
        self.history_table.setRowCount(0)

        self.wishlist_table.setRowCount(0)

        self.save_customer_btn.setText("Save Changes")

        self._refresh_customer_list()

    def _open_history_receipt(self, row, column):
        if column == 4:
            return
        ids = getattr(self, '_history_bill_ids', [])
        if 0 <= row < len(ids):
            bill, items = self.db.get_bill(ids[row])
            if bill:
                ReceiptDialog(bill, items, self).exec()
