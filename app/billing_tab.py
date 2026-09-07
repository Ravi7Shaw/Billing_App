"""
billing_tab.py
---------------
The "New Bill" screen: customer lookup, barcode scan OR manual item entry
(with autocomplete recommendations pulled live from the catalog), a cart,
and a discount panel that stays hidden until the cashier explicitly opens it.
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QComboBox,
    QSpinBox,
    QDoubleSpinBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QFrame,
    QCompleter,
    QScrollArea,
    QDateEdit,
)
from PySide6.QtCore import Qt, QDate

from widgets import rupees, EditableSearchCombo, divider, make_heading
from receipt import ReceiptDialog

NO_SUBTYPE = -1  # sentinel stored as itemData for "no brand/style selected"


class BillingTab(QWidget):
    def __init__(self, db, on_bill_saved=None):
        super().__init__()
        self.db = db
        self.on_bill_saved = on_bill_saved  # callback so other tabs can refresh

        self.cart = []  # list of dicts: item_id, name, category, qty, rate, amount, discount
        self.matched_customer = None  # sqlite3.Row or None
        self.current_items_by_name = {}  # lower(name) -> item row, for the active category/subtype
        self.discount_visible = (
            False  # discount column/total stays hidden until toggled open
        )
        self._payment_user_edited = False
        self._last_total = 0.0

        self._build_ui()
        self._refresh_categories()
        self._refresh_phone_completer()

    # ------------------------------------------------------------- UI setup
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        outer.addWidget(
            make_heading("New Bill", "Scan a barcode or enter items manually")
        )

        content = QHBoxLayout()
        content.setSpacing(14)
        outer.addLayout(content, 1)

        # ---------------- LEFT column: customer + item entry ----------------
        # Wrapped in a scroll area: on a shorter window (or a screen where
        # the taskbar eats into the usable height), the Customer + Add Item
        # boxes together can be taller than the visible area. Without a
        # scroll area the bottom of the Add Item box -- including the
        # "+ Add to Bill" button -- becomes unreachable.
        left = QVBoxLayout()
        left.setSpacing(12)
        left_widget = QWidget()
        left_widget.setLayout(left)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setWidget(left_widget)
        left_scroll.setMaximumWidth(450)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setStyleSheet(
            "QScrollArea { border: none; background: transparent; }"
        )
        content.addWidget(left_scroll)

        left.addWidget(self._build_customer_box())
        left.addWidget(self._build_item_entry_box())
        left.addStretch()

        # ---------------- RIGHT column: cart + totals ----------------
        right = QVBoxLayout()
        right.setSpacing(12)
        content.addLayout(right, 1)

        right.addWidget(self._build_cart_box(), 1)
        right.addWidget(self._build_totals_box())

    def _build_customer_box(self):
        box = QGroupBox("Customer")
        form = QVBoxLayout(box)

        phone_row = QHBoxLayout()
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Phone number")
        self.phone_input.editingFinished.connect(self._lookup_customer)
        phone_row.addWidget(self.phone_input)
        find_btn = QPushButton("Find")
        find_btn.setProperty("role", "secondary")
        find_btn.clicked.connect(self._lookup_customer)
        phone_row.addWidget(find_btn)
        form.addLayout(phone_row)

        self.customer_name_input = QLineEdit()
        self.customer_name_input.setPlaceholderText("Customer name")
        form.addWidget(self.customer_name_input)

        self.customer_address_input = QLineEdit()
        self.customer_address_input.setPlaceholderText("Address (optional)")
        form.addWidget(self.customer_address_input)

        self.customer_info_label = QLabel("")
        self.customer_info_label.setWordWrap(True)
        self.customer_info_label.setStyleSheet(
            "color: #2f6f4f; font-size: 12px; padding: 4px; background: #e7f2ec; border-radius: 6px;"
        )
        self.customer_info_label.setVisible(False)
        form.addWidget(self.customer_info_label)

        self.wishlist_input = QLineEdit()
        self.wishlist_input.setPlaceholderText(
            "What do they want next time? (optional)"
        )
        form.addWidget(self.wishlist_input)

        clear_btn = QPushButton("New / Clear Customer")
        clear_btn.setProperty("role", "link")
        clear_btn.clicked.connect(self._clear_customer)
        form.addWidget(clear_btn)

        return box

    def _build_item_entry_box(self):
        box = QGroupBox("Add Item")
        v = QVBoxLayout(box)

        v.addWidget(QLabel("Scan barcode:"))
        self.barcode_input = QLineEdit()
        self.barcode_input.setPlaceholderText("Scan here, or type barcode + Enter")
        self.barcode_input.returnPressed.connect(self._on_barcode_scanned)
        v.addWidget(self.barcode_input)

        self.barcode_status = QLabel("")
        self.barcode_status.setVisible(False)
        v.addWidget(self.barcode_status)

        v.addWidget(divider())
        manual_lbl = QLabel("Or enter manually:")
        manual_lbl.setStyleSheet("font-weight: 600; margin-top: 4px;")
        v.addWidget(manual_lbl)

        form = QFormLayout()
        form.setSpacing(8)

        self.category_combo = QComboBox()
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        form.addRow("Category:", self.category_combo)

        self.subtype_combo = QComboBox()
        self.subtype_combo.currentIndexChanged.connect(self._on_subtype_changed)
        form.addRow("Brand / Style:", self.subtype_combo)

        self.item_name_combo = EditableSearchCombo()
        self.item_name_combo.setEditText("")
        self.item_name_combo.editTextChanged.connect(self._on_item_name_changed)
        form.addRow("Item name:", self.item_name_combo)

        size_color_row = QHBoxLayout()
        self.size_input = QLineEdit()
        self.size_input.setPlaceholderText("Size (e.g. M, 32)")
        self.color_input = QLineEdit()
        self.color_input.setPlaceholderText("Color")
        size_color_row.addWidget(self.size_input)
        size_color_row.addWidget(self.color_input)
        form.addRow("Size / Color:", size_color_row)

        self.new_barcode_input = QLineEdit()
        self.new_barcode_input.setPlaceholderText(
            "Assign a barcode for next time (optional)"
        )
        form.addRow("Barcode:", self.new_barcode_input)

        self.rate_input = QDoubleSpinBox()
        self.rate_input.setRange(0, 999999)
        self.rate_input.setDecimals(2)
        self.rate_input.setPrefix("Rs. ")
        form.addRow("Rate:", self.rate_input)

        self.qty_input = QSpinBox()
        self.qty_input.setRange(1, 9999)
        self.qty_input.setValue(1)
        form.addRow("Quantity:", self.qty_input)

        v.addLayout(form)

        add_btn = QPushButton("+ Add to Bill")
        add_btn.clicked.connect(self._add_manual_item)
        v.addWidget(add_btn)

        return box

    def _build_cart_box(self):
        box = QGroupBox("Bill Items")
        v = QVBoxLayout(box)

        toggle_row = QHBoxLayout()
        toggle_row.addStretch()
        self.discount_arrow_btn = QPushButton("\u25b8 Discount")
        self.discount_arrow_btn.setProperty("role", "secondary")
        self.discount_arrow_btn.setToolTip("Show/hide per-item discount")
        self.discount_arrow_btn.clicked.connect(self._toggle_discount_visibility)
        toggle_row.addWidget(self.discount_arrow_btn)
        v.addLayout(toggle_row)

        self.cart_table = QTableWidget(0, 6)
        self.cart_table.setHorizontalHeaderLabels(
            ["Item", "Category", "Qty", "Rate", "Amount", "Discount"]
        )
        header = self.cart_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Fixed)
        self.cart_table.setColumnWidth(5, 100)
        self.cart_table.setAlternatingRowColors(True)
        self.cart_table.setColumnHidden(5, True)  # Discount column hidden until toggled
        self.cart_table.itemChanged.connect(self._on_cart_item_changed)
        v.addWidget(self.cart_table)

        return box

    def _build_totals_box(self):
        box = QFrame()
        box.setProperty("role", "card")
        v = QVBoxLayout(box)

        self.subtotal_label = QLabel("Subtotal (before discount): Rs. 0.00")
        v.addWidget(self.subtotal_label)

        self.discount_total_row = QWidget()
        dt_row = QHBoxLayout(self.discount_total_row)
        dt_row.setContentsMargins(0, 0, 0, 0)
        self.discount_total_label = QLabel("Total discount given: -Rs. 0.00")
        self.discount_total_label.setStyleSheet("color: #c0392b;")
        dt_row.addWidget(self.discount_total_label)
        dt_row.addStretch()
        self.discount_total_row.setVisible(False)
        v.addWidget(self.discount_total_row)

        self.taxable_label = QLabel("Taxable amount: Rs. 0.00")
        v.addWidget(self.taxable_label)

        self.gst_label = QLabel("GST (5%): Rs. 0.00")
        v.addWidget(self.gst_label)

        v.addWidget(divider())

        row_total = QHBoxLayout()
        row_total.addStretch()
        self.total_label = QLabel("Total to Pay: Rs. 0.00")
        self.total_label.setProperty("role", "total")
        row_total.addWidget(self.total_label)
        v.addLayout(row_total)

        payment_form = QFormLayout()
        payment_form.setSpacing(8)

        self.payment_amount_input = QDoubleSpinBox()
        self.payment_amount_input.setRange(0, 99999999.99)
        self.payment_amount_input.setDecimals(2)
        self.payment_amount_input.setPrefix("Rs. ")
        self.payment_amount_input.setValue(0.0)
        self.payment_amount_input.valueChanged.connect(self._on_payment_amount_changed)
        payment_form.addRow("Paid now:", self.payment_amount_input)

        self.balance_label = QLabel("Balance: Rs. 0.00")
        self.balance_label.setStyleSheet("font-weight: 600;")
        payment_form.addRow("Outstanding:", self.balance_label)
        v.addLayout(payment_form)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Payment mode:"))
        self.payment_mode_combo = QComboBox()
        self.payment_mode_combo.addItems(["Cash", "Card", "UPI", "Other"])
        row3.addWidget(self.payment_mode_combo)
        row3.addStretch()
        v.addLayout(row3)

        date_row = QHBoxLayout()
        date_row.addWidget(QLabel("Bill date:"))
        self.bill_date_input = QDateEdit()
        self.bill_date_input.setCalendarPopup(True)
        self.bill_date_input.setDate(QDate.currentDate())
        self.bill_date_input.setMaximumDate(QDate.currentDate())
        self.bill_date_input.setDisplayFormat("dd/MM/yyyy")
        date_row.addWidget(self.bill_date_input)
        today_btn = QPushButton("Today")
        today_btn.setProperty("role", "secondary")
        today_btn.setToolTip("Set bill date to today")
        today_btn.clicked.connect(lambda: self.bill_date_input.setDate(QDate.currentDate()))
        date_row.addWidget(today_btn)
        date_row.addStretch()
        v.addLayout(date_row)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear Bill")
        clear_btn.setProperty("role", "danger")
        clear_btn.clicked.connect(self._clear_bill)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        complete_btn = QPushButton("Complete Bill  ✓")
        complete_btn.setMinimumWidth(180)
        complete_btn.clicked.connect(self._complete_bill)
        btn_row.addWidget(complete_btn)
        v.addLayout(btn_row)

        return box

    # ------------------------------------------------------ data refreshers
    def _refresh_categories(self):
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        for cat in self.db.get_categories():
            self.category_combo.addItem(cat["name"], cat["id"])
        self.category_combo.blockSignals(False)
        self._on_category_changed()

    def _refresh_phone_completer(self):
        phones = [c["phone"] for c in self.db.search_customers("") if c["phone"]]
        completer = QCompleter(phones, self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.phone_input.setCompleter(completer)

    def refresh_catalog(self):
        """Call after inventory changes elsewhere so combos/suggestions are current."""
        self._refresh_categories()
        self._refresh_phone_completer()

    # ------------------------------------------------------------ customer
    def _lookup_customer(self):
        phone = self.phone_input.text().strip()
        if not phone:
            return
        customer = self.db.get_customer_by_phone(phone)
        if customer:
            self.matched_customer = customer
            self.customer_name_input.setText(customer["name"])
            self.customer_address_input.setText(customer["address"] or "")

            total_spent, visit_count = self.db.get_customer_total_spent(customer["id"])
            wishlist = self.db.get_wishlist(customer["id"])
            info_lines = [
                f"Returning customer \u2022 {visit_count} visit(s) \u2022 {rupees(total_spent)} spent"
            ]
            open_wishes = [
                w["item_description"] for w in wishlist if not w["fulfilled"]
            ]
            if open_wishes:
                info_lines.append("Wants: " + "; ".join(open_wishes))
            self.customer_info_label.setText("\n".join(info_lines))
            self.customer_info_label.setVisible(True)
        else:
            self.matched_customer = None
            self.customer_info_label.setText(
                "New customer \u2014 will be added on save"
            )
            self.customer_info_label.setVisible(True)

    def _clear_customer(self):
        self.matched_customer = None
        self.phone_input.clear()
        self.customer_name_input.clear()
        self.customer_address_input.clear()
        self.wishlist_input.clear()
        self.customer_info_label.clear()
        self.customer_info_label.setVisible(False)

    # -------------------------------------------------------------- barcode
    def _on_barcode_scanned(self):
        code = self.barcode_input.text().strip()
        self.barcode_input.clear()
        if not code:
            return
        item = self.db.get_item_by_barcode(code)
        if item:
            self._add_to_cart(
                item_id=item["id"],
                name=item["name"],
                category=item["category_name"],
                qty=1,
                rate=item["rate"],
                gst_rate=item["gst_rate"] if "gst_rate" in item.keys() else 5.0,
            )
            self.barcode_status.setVisible(False)
        else:
            self.barcode_status.setText(
                f"No item found for barcode '{code}'. Enter it manually below \u2193"
            )
            self.barcode_status.setStyleSheet("color: #c0392b; font-size: 12px;")
            self.barcode_status.setVisible(True)
            self.new_barcode_input.setText(code)
            self.item_name_combo.setFocus()

    # ---------------------------------------------------- manual item entry
    def _on_category_changed(self):
        cat_id = self.category_combo.currentData()
        self.subtype_combo.blockSignals(True)
        self.subtype_combo.clear()
        if cat_id is not None:
            subtypes = self.db.get_subtypes(cat_id)
            if subtypes:
                self.subtype_combo.addItem("(No brand / style)", NO_SUBTYPE)
                for s in subtypes:
                    self.subtype_combo.addItem(s["name"], s["id"])
                self.subtype_combo.setEnabled(True)
            else:
                self.subtype_combo.addItem("N/A for this category", NO_SUBTYPE)
                self.subtype_combo.setEnabled(False)
        self.subtype_combo.blockSignals(False)
        self._refresh_item_suggestions()

    def _on_subtype_changed(self):
        self._refresh_item_suggestions()

    def _refresh_item_suggestions(self):
        cat_id = self.category_combo.currentData()
        subtype_id = self.subtype_combo.currentData()
        if subtype_id == NO_SUBTYPE:
            subtype_id = None
        if cat_id is None:
            return
        items = self.db.get_items(category_id=cat_id, subtype_id=subtype_id)
        self.current_items_by_name = {i["name"].lower(): i for i in items}
        self.item_name_combo.set_items(
            sorted(self.current_items_by_name_display(items))
        )

    def current_items_by_name_display(self, items):
        return [i["name"] for i in items]

    def _on_item_name_changed(self, text):
        match = self.current_items_by_name.get(text.strip().lower())
        if match:
            self.rate_input.setValue(match["rate"])
            self.size_input.setText(match["size"] or "")
            self.color_input.setText(match["color"] or "")
            if match["barcode"]:
                self.new_barcode_input.setText(match["barcode"])

    def _add_manual_item(self):
        cat_id = self.category_combo.currentData()
        cat_name = self.category_combo.currentText()
        subtype_id = self.subtype_combo.currentData()
        if subtype_id == NO_SUBTYPE:
            subtype_id = None
        name = self.item_name_combo.currentText().strip()
        rate = self.rate_input.value()
        qty = self.qty_input.value()

        if cat_id is None:
            QMessageBox.warning(
                self,
                "Missing info",
                "Please choose a category first.\n"
                "(Add one from the Inventory tab if the list is empty.)",
            )
            return
        if not name:
            QMessageBox.warning(self, "Missing info", "Please type an item name.")
            return
        if rate <= 0:
            QMessageBox.warning(
                self, "Missing info", "Please enter a rate greater than 0."
            )
            return

        existing = self.db.find_item_by_name(cat_id, subtype_id, name)
        if existing:
            item_id = existing["id"]
            # Keep the catalog rate/details current if the cashier changed them.
            self.db.update_item(
                item_id,
                name,
                cat_id,
                subtype_id,
                self.new_barcode_input.text() or existing["barcode"],
                self.size_input.text() or existing["size"],
                self.color_input.text() or existing["color"],
                rate,
                existing["stock_qty"],
            )
        else:
            # Not seen before -> save it to the catalog so it's recommended
            # automatically next time (this is how "don't hardcode anything"
            # is honoured: the catalog grows from real entries).
            item_id = self.db.add_item(
                name,
                cat_id,
                subtype_id,
                self.new_barcode_input.text(),
                self.size_input.text(),
                self.color_input.text(),
                rate,
                0,
            )

        self._add_to_cart(
            item_id=item_id,
            name=name,
            category=cat_name,
            qty=qty,
            rate=rate,
            gst_rate=(existing["gst_rate"] if existing and "gst_rate" in existing.keys() else 5.0),
        )

        # Reset just the item-specific fields; keep category/brand selected
        # for fast repeated entry of the same style.
        self.item_name_combo.setEditText("")
        self.size_input.clear()
        self.color_input.clear()
        self.new_barcode_input.clear()
        self.rate_input.setValue(0)
        self.qty_input.setValue(1)
        self._refresh_item_suggestions()
        self.barcode_input.setFocus()

    # ------------------------------------------------------------ the cart
    def _add_to_cart(self, item_id, name, category, qty, rate, gst_rate=5.0):
        for row in self.cart:
            if row["item_id"] == item_id and row["item_id"] is not None:
                row["qty"] += qty
                row["amount"] = row["qty"] * row["rate"]
                row["discount"] = min(row["discount"], row["amount"])
                self._render_cart()
                self._recalculate_totals()
                return
        self.cart.append(
            {
                "item_id": item_id,
                "name": name,
                "category": category,
                "qty": qty,
                "rate": rate,
                "amount": qty * rate,
                "discount": 0.0,
                "gst_rate": float(gst_rate or 0.0),
            }
        )
        self._render_cart()
        self._recalculate_totals()

    def _render_cart(self):
        self.cart_table.blockSignals(True)
        self.cart_table.setRowCount(len(self.cart))
        for row_idx, row in enumerate(self.cart):
            name_item = QTableWidgetItem(row["name"])
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.cart_table.setItem(row_idx, 0, name_item)

            cat_item = QTableWidgetItem(row["category"])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsEditable)
            self.cart_table.setItem(row_idx, 1, cat_item)

            qty_item = QTableWidgetItem(str(row["qty"]))
            self.cart_table.setItem(row_idx, 2, qty_item)

            rate_item = QTableWidgetItem(f"{row['rate']:.2f}")
            self.cart_table.setItem(row_idx, 3, rate_item)

            amt_item = QTableWidgetItem(rupees(row["amount"]))
            amt_item.setFlags(amt_item.flags() & ~Qt.ItemIsEditable)
            self.cart_table.setItem(row_idx, 4, amt_item)

            disc_item = QTableWidgetItem(f"{row['discount']:.2f}")
            self.cart_table.setItem(row_idx, 5, disc_item)
        self.cart_table.setColumnHidden(5, not self.discount_visible)
        self.cart_table.blockSignals(False)
        self._sync_cart_row_remove_buttons()

    def _sync_cart_row_remove_buttons(self):
        # A 7th, actions column with a remove button per row. ResizeToContents
        # can't reliably measure a composite cell widget's size before it's
        # been laid out, which was squeezing this column too narrow and
        # visually corrupting the button -- a fixed width avoids that.
        if self.cart_table.columnCount() < 7:
            self.cart_table.setColumnCount(7)
            self.cart_table.setHorizontalHeaderLabels(
                ["Item", "Category", "Qty", "Rate", "Amount", "Discount", ""]
            )
            self.cart_table.horizontalHeader().setSectionResizeMode(
                6, QHeaderView.Fixed
            )
            self.cart_table.setColumnWidth(6, 90)
            self.cart_table.verticalHeader().setDefaultSectionSize(40)
        for row_idx in range(self.cart_table.rowCount()):
            btn = QPushButton("Remove")
            btn.setProperty("role", "danger")
            btn.setProperty("compact", "true")
            btn.clicked.connect(lambda _, r=row_idx: self._remove_cart_row(r))
            self.cart_table.setCellWidget(row_idx, 6, btn)
        self.cart_table.resizeRowsToContents()

    def _remove_cart_row(self, row_idx):
        if 0 <= row_idx < len(self.cart):
            del self.cart[row_idx]
            self._render_cart()
            self._recalculate_totals()

    def _on_cart_item_changed(self, table_item):
        row_idx = table_item.row()
        col = table_item.column()
        if row_idx >= len(self.cart):
            return
        try:
            if col == 2:  # qty
                new_qty = max(1, int(float(table_item.text())))
                self.cart[row_idx]["qty"] = new_qty
                self.cart[row_idx]["amount"] = (
                    self.cart[row_idx]["qty"] * self.cart[row_idx]["rate"]
                )
            elif col == 3:  # rate
                new_rate = max(0.0, float(table_item.text()))
                self.cart[row_idx]["rate"] = new_rate
                self.cart[row_idx]["amount"] = (
                    self.cart[row_idx]["qty"] * self.cart[row_idx]["rate"]
                )
            elif col == 5:  # discount
                new_discount = max(0.0, float(table_item.text()))
                self.cart[row_idx]["discount"] = min(
                    new_discount, self.cart[row_idx]["amount"]
                )
        except ValueError:
            pass
        self.cart[row_idx]["discount"] = min(
            self.cart[row_idx]["discount"], self.cart[row_idx]["amount"]
        )
        self._render_cart()
        self._recalculate_totals()

    # --------------------------------------------------------------- totals
    def _toggle_discount_visibility(self):
        self.discount_visible = not self.discount_visible
        self.discount_arrow_btn.setText(
            "\u25be Discount" if self.discount_visible else "\u25b8 Discount"
        )
        self.cart_table.setColumnHidden(5, not self.discount_visible)
        self.discount_total_row.setVisible(self.discount_visible)

    def _subtotal(self):
        """Gross total before any discount and before GST."""
        return sum(r["amount"] for r in self.cart)

    def _total_discount(self):
        return sum(r["discount"] for r in self.cart)

    def _taxable_amount(self):
        return max(self._subtotal() - self._total_discount(), 0.0)

    def _gst_amount(self):
        total = 0.0
        for row in self.cart:
            net_line = max(row["amount"] - row["discount"], 0.0)
            total += net_line * float(row.get("gst_rate", 5.0)) / 100.0
        return total

    def _grand_total(self):
        return self._taxable_amount() + self._gst_amount()

    def _on_payment_amount_changed(self, value):
        self._payment_user_edited = True
        balance = max(self._grand_total() - value, 0.0)
        self.balance_label.setText(f"Balance: {rupees(balance)}")

    def _recalculate_totals(self):
        subtotal = self._subtotal()
        discount_total = self._total_discount()
        taxable = self._taxable_amount()
        gst = self._gst_amount()
        total = self._grand_total()

        self.subtotal_label.setText(f"Subtotal (before discount): {rupees(subtotal)}")
        self.discount_total_label.setText(
            f"Total discount given: -{rupees(discount_total)}"
        )
        self.taxable_label.setText(f"Taxable amount: {rupees(taxable)}")

        # All current clothing items use 5% GST. Keep the display simple.
        self.gst_label.setText(f"GST (5%): {rupees(gst)}")
        self.total_label.setText(f"Total to Pay: {rupees(total)}")

        old_total = self._last_total
        current_paid = self.payment_amount_input.value()
        if not self._payment_user_edited or abs(current_paid - old_total) < 0.01:
            new_paid = total
        else:
            new_paid = min(current_paid, total)

        self.payment_amount_input.blockSignals(True)
        self.payment_amount_input.setMaximum(total)
        self.payment_amount_input.setValue(new_paid)
        self.payment_amount_input.blockSignals(False)
        self.balance_label.setText(f"Balance: {rupees(max(total - new_paid, 0.0))}")
        self._last_total = total

    # ---------------------------------------------------------- completion
    def _complete_bill(self):
        if not self.cart:
            QMessageBox.warning(
                self, "Empty bill", "Add at least one item before completing the bill."
            )
            return

        phone = self.phone_input.text().strip()
        name = self.customer_name_input.text().strip()
        address = self.customer_address_input.text().strip()
        wishlist_note = self.wishlist_input.text().strip()

        customer_id = None
        if phone or name:
            if not name:
                QMessageBox.warning(
                    self,
                    "Missing info",
                    "Please enter the customer's name, or leave both name and phone blank for a walk-in sale.",
                )
                return
            existing = self.db.get_customer_by_phone(phone) if phone else None
            if existing:
                customer_id = existing["id"]
                self.db.update_customer(
                    customer_id, name, phone, address, existing["notes"] or ""
                )
            else:
                customer_id = self.db.add_customer(name, phone, address, "")

        subtotal = self._subtotal()
        discount_amount = self._total_discount()
        taxable_amount = self._taxable_amount()
        gst_amount = self._gst_amount()
        total = self._grand_total()
        percent = (discount_amount / subtotal * 100.0) if subtotal else 0.0
        payment_mode = self.payment_mode_combo.currentText()
        bill_date = self.bill_date_input.date().toString("yyyy-MM-dd")
        paid_now = self.payment_amount_input.value()

        if paid_now > total + 0.01:
            QMessageBox.warning(self, "Invalid payment", "Amount paid cannot exceed the bill total.")
            return

        # The current catalog uses 5% GST. Each line stores its own GST snapshot.
        bill_items = []
        for row in self.cart:
            net_line = max(row["amount"] - row["discount"], 0.0)
            line_gst_rate = float(row.get("gst_rate", 5.0))
            line_gst_amount = net_line * line_gst_rate / 100.0
            bill_items.append(
                {
                    "item_id": row["item_id"],
                    "name": row["name"],
                    "category": row["category"],
                    "quantity": row["qty"],
                    "rate": row["rate"],
                    "subtotal": net_line,
                    "gst_rate": line_gst_rate,
                    "gst_amount": line_gst_amount,
                }
            )

        # If every line has the same GST rate, save that rate as the bill rate.
        # For the current shop configuration this is 5%.
        rates = {round(float(row.get("gst_rate", 5.0)), 4) for row in self.cart}
        bill_gst_rate = next(iter(rates)) if len(rates) == 1 else 0.0

        try:
            bill_id, bill_no = self.db.save_bill(
                customer_id,
                bill_items,
                subtotal,
                percent,
                discount_amount,
                total,
                payment_mode,
                bill_date,
                taxable_amount,
                bill_gst_rate,
                gst_amount,
                paid_now,
                "Initial payment",
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not save bill", str(exc))
            return

        if wishlist_note and customer_id:
            self.db.add_wishlist(customer_id, wishlist_note)

        # Receipt intentionally remains unchanged for now, as requested.
        bill_row, saved_bill_items = self.db.get_bill(bill_id)
        dialog = ReceiptDialog(bill_row, saved_bill_items, self)
        dialog.exec()

        if self.on_bill_saved:
            self.on_bill_saved()

        self._clear_bill()
        self._clear_customer()
        self._refresh_phone_completer()

    def _clear_bill(self):
        self.cart = []
        self._payment_user_edited = False
        self._last_total = 0.0
        self._render_cart()
        self._recalculate_totals()
        self.barcode_input.setFocus()
