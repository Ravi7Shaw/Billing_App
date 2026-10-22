"""
billing_tab.py
---------------
The "New Bill" screen: customer lookup, barcode scan OR manual item entry
(with autocomplete recommendations pulled live from the catalog), a cart,
and a discount panel that stays hidden until the cashier explicitly opens it.
"""

from decimal import Decimal, InvalidOperation

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
    QTabWidget,
    QSizePolicy,
)
from PySide6.QtCore import Qt, QDate
from PySide6.QtGui import QStandardItem, QStandardItemModel

from widgets import rupees, EditableSearchCombo, divider, make_heading
from receipt import ReceiptDialog
from money import money, line_amount, tax_amount, sum_money
from access import AccessSession, RoleDatabase
from offers import offer_taxes

NO_SUBTYPE = -1  # sentinel stored as itemData for "no brand/style selected"


class BillingTab(QWidget):
    def __init__(self, db, on_bill_saved=None, session=None):
        super().__init__()
        self.session = db.session if isinstance(db, RoleDatabase) else (session or AccessSession())
        self.db = db if isinstance(db, RoleDatabase) else RoleDatabase(db, self.session)
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
        self._apply_permissions()
        self._refresh_categories()
        self._refresh_phone_completer()
        self._refresh_sale_location()

    def _apply_permissions(self):
        admin = self.session.is_admin
        self.sale_options.setVisible(admin)
        self.discount_arrow_btn.setVisible(admin)
        self.payment_amount_input.setEnabled(admin)
        self.rate_input.setReadOnly(not admin)
        self.bill_date_input.setEnabled(admin)
        self.wishlist_input.setVisible(admin)
        self.payment_form.setRowVisible(self.payment_amount_input, admin)
        self.payment_form.setRowVisible(self.balance_label, admin)
        for index in range(self.date_row.count()):
            widget = self.date_row.itemAt(index).widget()
            if widget:
                widget.setVisible(admin)
        for field in (self.size_input, self.color_input, self.new_barcode_input):
            field.setReadOnly(not admin)
        if not admin:
            self.payment_amount_input.setToolTip("Full payment is required for employee bills.")
            self.rate_input.setToolTip("Catalog price. Price changes require an admin.")

    # ------------------------------------------------------------- UI setup
    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)
        self.content = QHBoxLayout()
        self.content.setSpacing(8)
        outer.addLayout(self.content, 1)
        self.entry_scroll = QScrollArea()
        self.entry_scroll.setWidgetResizable(True)
        self.entry_scroll.setMinimumWidth(0)
        self.entry_scroll.setStyleSheet("QScrollArea { border: none; }")
        entry = QWidget()
        left = QVBoxLayout(entry)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(6)
        self.sale_options = QWidget()
        sale_form = QFormLayout(self.sale_options)
        sale_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.sale_godown = QComboBox()
        self.sale_godown.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.sale_channel = QComboBox()
        self.sale_channel.addItems(['Retail', 'Wholesale'])
        sale_form.addRow('Selling godown', self.sale_godown)
        sale_form.addRow('Sale type', self.sale_channel)
        left.addWidget(self.sale_options)
        left.addWidget(self._build_customer_box())
        left.addWidget(self._build_item_entry_box())
        left.addStretch()
        self.entry_scroll.setWidget(entry)
        self.bill_panel = QWidget()
        self.bill_panel.setMinimumWidth(0)
        right = QVBoxLayout(self.bill_panel)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(6)
        right.addWidget(self._build_cart_box(), 1)
        right.addWidget(self._build_totals_box())
        self.compact_tabs = QTabWidget()
        self.compact_tabs.setDocumentMode(True)
        self.compact_tabs.hide()
        self.content.addWidget(self.entry_scroll, 2)
        self.content.addWidget(self.bill_panel, 3)
        self.content.addWidget(self.compact_tabs)
        self._compact = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        budget = max(60, min(170, self.height() // 4))
        self.payment_scroll.setMaximumHeight(min(max(38, self.payment_fields.sizeHint().height()+2), budget))
        compact = self.width() < 1050
        if compact == self._compact:
            return
        self._compact = compact
        if compact:
            self.content.removeWidget(self.entry_scroll)
            self.content.removeWidget(self.bill_panel)
            self.compact_tabs.addTab(self.entry_scroll, "Customer / Add items")
            self.compact_tabs.addTab(self.bill_panel, "Bill")
            self.compact_tabs.setCurrentIndex(1 if self.cart else 0)
            self.compact_tabs.show()
        else:
            self.compact_tabs.removeTab(1)
            self.compact_tabs.removeTab(0)
            self.compact_tabs.hide()
            self.content.insertWidget(0, self.entry_scroll, 2)
            self.content.insertWidget(1, self.bill_panel, 3)
            self.entry_scroll.show()
            self.bill_panel.show()

    def _build_customer_box(self):
        box = QGroupBox("Customer")
        form = QVBoxLayout(box)

        phone_row = QHBoxLayout()
        self.phone_input = QLineEdit()
        self.phone_input.setPlaceholderText("Phone number")
        self.phone_input.editingFinished.connect(self._lookup_customer)
        self.phone_input.textEdited.connect(self._discard_previous_customer)
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
        self.quick_barcode_input = QLineEdit()
        self.quick_barcode_input.setPlaceholderText("Scan barcode + Enter")
        self.quick_barcode_input.returnPressed.connect(self._scan_from_bill)
        toggle_row.addWidget(self.quick_barcode_input, 1)
        self.discount_arrow_btn = QPushButton("\u25b8 Discount")
        self.discount_arrow_btn.setProperty("role", "secondary")
        self.discount_arrow_btn.setToolTip("Show/hide per-item discount")
        self.discount_arrow_btn.clicked.connect(self._toggle_discount_visibility)
        toggle_row.addWidget(self.discount_arrow_btn)
        v.addLayout(toggle_row)

        self.cart_table = QTableWidget(0, 6)
        self.cart_table.setMinimumSize(0, 100)
        self.cart_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
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
        v.addWidget(self.cart_table, 1)

        return box

    def _build_totals_box(self):
        box = QFrame()
        box.setProperty("role", "card")
        v = QVBoxLayout(box)

        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(4)
        summary = QHBoxLayout()
        self.subtotal_label = QLabel("Subtotal: Rs. 0.00")
        self.gst_label = QLabel("GST: Rs. 0.00")
        summary.addWidget(self.subtotal_label)
        summary.addStretch()
        summary.addWidget(self.gst_label)
        v.addLayout(summary)
        self.offer_savings_label = QLabel()
        self.offer_savings_label.setWordWrap(True)
        self.offer_savings_label.setTextFormat(Qt.PlainText)
        v.addWidget(self.offer_savings_label)
        self.discount_total_row = QWidget()
        dt_row = QHBoxLayout(self.discount_total_row)
        dt_row.setContentsMargins(0, 0, 0, 0)
        self.discount_total_label = QLabel("Total discount given: -Rs. 0.00")
        self.discount_total_label.setStyleSheet("color: #c0392b;")
        dt_row.addWidget(self.discount_total_label)
        self.taxable_label = QLabel("Taxable amount: Rs. 0.00")
        dt_row.addWidget(self.taxable_label)
        self.discount_total_row.hide()
        v.addWidget(self.discount_total_row)
        total_row = QHBoxLayout()
        self.total_label = QLabel("Total to Pay: Rs. 0.00")
        self.total_label.setProperty("role", "total")
        self.total_label.setWordWrap(True)
        total_row.addWidget(self.total_label, 1)
        refresh_offers = QPushButton('Refresh offers')
        refresh_offers.setProperty('compact', 'true')
        refresh_offers.setProperty('role', 'secondary')
        refresh_offers.clicked.connect(self._refresh_offers)
        total_row.addWidget(refresh_offers)
        v.addLayout(total_row)

        self.payment_fields = QWidget()
        payment_layout = QVBoxLayout(self.payment_fields)
        payment_layout.setContentsMargins(0, 0, 0, 0)
        payment_layout.setSpacing(4)
        self.payment_scroll = QScrollArea()
        self.payment_scroll.setWidgetResizable(True)
        self.payment_scroll.setWidget(self.payment_fields)
        self.payment_scroll.setStyleSheet("QScrollArea { border: none; }")
        self.payment_scroll.setMinimumHeight(38)
        self.payment_scroll.setMaximumHeight(170)
        v.addWidget(self.payment_scroll)
        payment_form = self.payment_form = QFormLayout()
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
        payment_layout.addLayout(payment_form)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Payment mode:"))
        self.payment_mode_combo = QComboBox()
        self.payment_mode_combo.addItems(["Cash", "Card", "UPI", "Other"])
        row3.addWidget(self.payment_mode_combo)
        row3.addStretch()
        payment_layout.addLayout(row3)

        date_row = self.date_row = QHBoxLayout()
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
        payment_layout.addLayout(date_row)

        btn_row = QHBoxLayout()
        clear_btn = QPushButton("Clear Bill")
        clear_btn.setProperty("role", "danger")
        clear_btn.clicked.connect(self._clear_bill)
        btn_row.addWidget(clear_btn)
        btn_row.addStretch()
        complete_btn = self.complete_btn = QPushButton("Complete Bill  ✓")
        complete_btn.setMinimumWidth(140)
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
        model = QStandardItemModel()
        for customer in self.db.get_customer_phone_suggestions():
            if customer["phone"]:
                item = QStandardItem(f'{customer["phone"]} — {customer["name"]}')
                item.setData(customer["phone"], Qt.UserRole)
                model.appendRow(item)
        completer = QCompleter(model, self)
        model.setParent(completer)
        completer.setCompletionRole(Qt.UserRole)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.activated[str].connect(self._select_customer_phone)
        self.phone_input.setCompleter(completer)

    def _select_customer_phone(self, phone):
        self.phone_input.setText(phone)
        self._lookup_customer()

    def _refresh_sale_location(self):
        if not self.session.is_admin:
            return
        current = self.sale_godown.currentData()
        self.sale_godown.clear()
        for row in self.db.get_godowns():
            self.sale_godown.addItem(row['name'], row['id'])
        index = self.sale_godown.findData(current)
        if index >= 0:
            self.sale_godown.setCurrentIndex(index)

    def refresh_catalog(self):
        """Call after inventory changes elsewhere so combos/suggestions are current."""
        self._refresh_sale_location()
        self._refresh_categories()
        self._refresh_phone_completer()

    # ------------------------------------------------------------ customer
    def _discard_previous_customer(self, phone):
        if self.matched_customer is not None and phone.strip() != (self.matched_customer["phone"] or ""):
            self.matched_customer = None
            self.customer_name_input.clear()
            self.customer_address_input.clear()
            self.wishlist_input.clear()
            self.customer_info_label.clear()
            self.customer_info_label.setVisible(False)

    def _lookup_customer(self):
        phone = self.phone_input.text().strip()
        self._discard_previous_customer(phone)
        if not phone:
            return
        customer = self.db.get_customer_by_phone(phone)
        if customer:
            self.matched_customer = customer
            self.customer_name_input.setText(customer["name"])
            self.customer_address_input.setText(customer["address"] or "")

            if not self.session.is_admin:
                self.customer_info_label.setText("Returning customer")
                self.customer_info_label.setVisible(True)
                return
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
    def _focus_barcode(self):
        target = self.quick_barcode_input if self._compact and self.compact_tabs.currentWidget() is self.bill_panel else self.barcode_input
        target.setFocus()

    def _scan_from_bill(self):
        self.barcode_input.setText(self.quick_barcode_input.text())
        self.quick_barcode_input.clear()
        self._on_barcode_scanned()

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
            self._focus_barcode()
        else:
            self.barcode_status.setText(
                f"No item found for barcode '{code}'. Enter it manually below \u2193"
            )
            self.barcode_status.setStyleSheet("color: #c0392b; font-size: 12px;")
            self.barcode_status.setVisible(True)
            if self._compact:
                self.compact_tabs.setCurrentWidget(self.entry_scroll)
            self._focus_barcode()
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
        items = [item for item in self.db.get_items(category_id=cat_id, subtype_id=subtype_id)
                 if item["subtype_id"] == subtype_id]
        counts = {}
        for item in items:
            key = item["name"].lower()
            counts[key] = counts.get(key, 0) + 1
        self.current_items_by_name = {}
        labels = []
        for item in items:
            label = item["name"]
            if counts[label.lower()] > 1:
                details = " / ".join(str(item[key]) for key in ("size", "color", "barcode") if item[key])
                label = f"{label} — {details or 'Variant'} (#{item['id']})"
            labels.append(label)
            self.current_items_by_name[label.lower()] = item
        self.item_name_combo.set_items(sorted(labels))

    def _on_item_name_changed(self, text):
        match = self.current_items_by_name.get(text.strip().lower())
        self.rate_input.setValue(match["rate"] if match else 0)
        self.size_input.setText((match["size"] or "") if match else "")
        self.color_input.setText((match["color"] or "") if match else "")
        self.new_barcode_input.setText((match["barcode"] or "") if match else "")

    def _add_manual_item(self):
        cat_id = self.category_combo.currentData()
        cat_name = self.category_combo.currentText()
        subtype_id = self.subtype_combo.currentData()
        if subtype_id == NO_SUBTYPE:
            subtype_id = None
        label = self.item_name_combo.currentText().strip()
        selected = self.current_items_by_name.get(label.lower())
        name = selected["name"] if selected else label
        if not self.session.is_admin and selected is None:
            QMessageBox.warning(self, "Admin required", "Choose an existing catalog item. Only admins can add items.")
            return
        rate = selected["rate"] if not self.session.is_admin else self.rate_input.value()
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

        try:
            size = self.size_input.text().strip()
            color = self.color_input.text().strip()
            barcode = self.new_barcode_input.text().strip()
            existing = self.db.get_item_by_id(selected["id"]) if selected else None
            if not (existing and existing["active"]
                    and existing["category_id"] == cat_id
                    and existing["subtype_id"] == subtype_id
                    and existing["name"].casefold() == name.casefold()
                    and (existing["size"] or "").casefold() == size.casefold()
                    and (existing["color"] or "").casefold() == color.casefold()
                    and (existing["barcode"] or "") == barcode):
                existing = self.db.find_item_by_name(
                    cat_id, subtype_id, name, size, color, barcode,
                )
            if existing:
                # Billing prices belong to the sale; catalogue edits are made
                # explicitly in Inventory, never by overwriting another variant.
                item_id = existing["id"]
            else:
                if not self.session.is_admin:
                    raise PermissionError("Choose an existing catalog item without changing its details.")
                item_id = self.db.add_item(
                    name, cat_id, subtype_id, self.new_barcode_input.text(),
                    self.size_input.text().strip(), self.color_input.text().strip(), rate, 0,
                )
        except Exception as exc:
            QMessageBox.warning(self, "Could not add item", str(exc))
            return

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
        self._focus_barcode()

    # ------------------------------------------------------------ the cart
    def _add_to_cart(self, item_id, name, category, qty, rate, gst_rate=5.0):
        if self._compact:
            self.compact_tabs.setCurrentWidget(self.bill_panel)
        if not self.session.is_admin:
            rate = money(rate)
        for row in self.cart:
            if (row["item_id"] == item_id and item_id is not None
                    and row["rate"] == money(rate)
                    and row["gst_rate"] == float(gst_rate or 0.0)):
                row["qty"] += qty
                row["amount"] = line_amount(row["qty"], row["rate"])
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
                "rate": money(rate),
                "amount": line_amount(qty, rate),
                "discount": 0.0,
                "gst_rate": float(gst_rate or 0.0),
            }
        )
        self._render_cart()
        self._recalculate_totals()

    def _render_cart(self):
        quotes = self.db.quote_offers(self.cart)
        for row, quote in zip(self.cart, quotes):
            row.update(quote)
            row['discount'] = min(row['discount'], max(money(row['amount']-row['offer_discount']),0))
        self.cart_table.blockSignals(True)
        self.cart_table.setRowCount(len(self.cart))
        for row_idx, row in enumerate(self.cart):
            name_item = QTableWidgetItem(row["name"])
            if row.get('offer_name'):
                name_item.setToolTip(f"{row['offer_name']}: save {rupees(row['offer_discount'])} before GST")
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.cart_table.setItem(row_idx, 0, name_item)

            cat_item = QTableWidgetItem(row["category"])
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsEditable)
            self.cart_table.setItem(row_idx, 1, cat_item)

            qty_item = QTableWidgetItem(str(row["qty"]))
            self.cart_table.setItem(row_idx, 2, qty_item)

            rate_item = QTableWidgetItem(f"{row['rate']:.2f}")
            if not self.session.is_admin:
                rate_item.setFlags(rate_item.flags() & ~Qt.ItemIsEditable)
            self.cart_table.setItem(row_idx, 3, rate_item)

            amt_item = QTableWidgetItem(rupees(row["amount"]))
            amt_item.setFlags(amt_item.flags() & ~Qt.ItemIsEditable)
            self.cart_table.setItem(row_idx, 4, amt_item)

            disc_item = QTableWidgetItem(f"{row['discount']:.2f}")
            if not self.session.is_admin:
                disc_item.setFlags(disc_item.flags() & ~Qt.ItemIsEditable)
            self.cart_table.setItem(row_idx, 5, disc_item)
        self.cart_table.setColumnHidden(5, not self.discount_visible)
        self.cart_table.blockSignals(False)
        self._sync_cart_row_remove_buttons()
        if self._compact:
            self.compact_tabs.setTabText(1, f"Bill ({len(self.cart)})")

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
        if not self.session.is_admin and col in (3, 5):
            self._render_cart()
            return
        # Validate into a draft; a failed edit must not partly change the cart.
        updated = dict(self.cart[row_idx])
        try:
            if col == 2:
                quantity = Decimal(table_item.text())
                if not quantity.is_finite() or quantity != quantity.to_integral_value() or not 1 <= quantity <= 999999:
                    raise ValueError('Enter a whole quantity from 1 to 999999.')
                updated['qty'] = int(quantity)
                updated['amount'] = line_amount(updated['qty'],updated['rate'])
            elif col == 3:
                rate = money(table_item.text())
                if not 0 <= rate <= self.rate_input.maximum():
                    raise ValueError(f'Enter a price from 0 to {self.rate_input.maximum():g}.')
                updated['rate'] = rate
                updated['amount'] = line_amount(updated['qty'],rate)
            elif col == 5:
                updated['discount'] = max(0,money(table_item.text()))
            updated['discount'] = min(updated['discount'],updated['amount'])
        except (ValueError,InvalidOperation,OverflowError):
            QMessageBox.warning(self,'Invalid bill entry','Enter a valid whole quantity (1–999999) or a price within the allowed range. The previous value was kept.')
        else:
            self.cart[row_idx].update(updated)
        self._render_cart()
        self._recalculate_totals()

    # --------------------------------------------------------------- totals
    def _toggle_discount_visibility(self):
        if not self.session.is_admin:
            return
        self.discount_visible = not self.discount_visible
        self.discount_arrow_btn.setText(
            "\u25be Discount" if self.discount_visible else "\u25b8 Discount"
        )
        self.cart_table.setColumnHidden(5, not self.discount_visible)
        self.discount_total_row.setVisible(self.discount_visible)

    def _refresh_offers(self):
        self._render_cart()
        self._recalculate_totals()

    def _subtotal(self):
        """Gross total before any discount and before GST."""
        return sum_money(r["amount"] for r in self.cart)

    def _total_discount(self):
        return sum_money(r["discount"] + r.get("offer_discount", 0) for r in self.cart)

    def _taxable_amount(self):
        return max(money(self._subtotal() - self._total_discount()), 0.0)

    def _line_taxes(self):
        return offer_taxes([dict(net=max(money(r['amount']-r['discount']-r.get('offer_discount',0)),0),
                                gst_rate=r.get('gst_rate',5.0),offer_id=r.get('offer_id')) for r in self.cart])

    def _gst_amount(self):
        return sum_money(self._line_taxes())

    def _grand_total(self):
        return money(self._taxable_amount() + self._gst_amount())

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

        savings = sum_money(r.get('offer_discount',0) for r in self.cart)
        names = sorted({r['offer_name'] for r in self.cart if r.get('offer_name')})
        description = ', '.join(names[:3])
        if len(names)>3:
            description += f" (+{len(names)-3} more)"
        self.offer_savings_label.setText(f"Offers: {description} · Saved {rupees(savings)} before GST" if savings else '')
        self.offer_savings_label.setVisible(bool(savings))
        self.subtotal_label.setText(f"Subtotal: {rupees(subtotal)}")
        self.discount_total_label.setText(
            f"Total discount given: -{rupees(discount_total)}"
        )
        self.taxable_label.setText(f"Taxable amount: {rupees(taxable)}")

        self.gst_label.setText(f"GST (included in total): {rupees(gst)}")
        self.total_label.setText(f"Total to Pay: {rupees(total)}")

        old_total = self._last_total
        current_paid = self.payment_amount_input.value()
        if not self.session.is_admin or not self._payment_user_edited or abs(current_paid - old_total) < 0.01:
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

        try:
            self.db.validate_employee_cart(
                self.cart, self.payment_amount_input.value(),
                self.bill_date_input.date().toString("yyyy-MM-dd"),
            )
        except (PermissionError, ValueError) as exc:
            QMessageBox.warning(self, "Admin required", str(exc))
            return

        phone = self.phone_input.text().strip()
        name = self.customer_name_input.text().strip()
        address = self.customer_address_input.text().strip()
        wishlist_note = self.wishlist_input.text().strip()

        customer_id = None
        customer_details = None
        if phone or name:
            if not name:
                QMessageBox.warning(
                    self,
                    "Missing info",
                    "Please enter the customer's name, or leave both name and phone blank for a walk-in sale.",
                )
                return
            customer_details = dict(name=name, phone=phone, address=address)

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
        for row, line_gst_amount in zip(self.cart, self._line_taxes()):
            net_line = max(row["amount"] - row["discount"] - row.get("offer_discount", 0), 0.0)
            line_gst_rate = float(row.get("gst_rate", 5.0))
            net_line = money(net_line)
            bill_items.append(
                {
                    "offer_checked": True,
                    "offer_id_snapshot": row.get('offer_id'),
                    "offer_name_snapshot": row.get('offer_name', ''),
                    "offer_discount": row.get('offer_discount', 0),
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
                customer_details=customer_details,
                godown_id=(self.sale_godown.currentData() or 1) if self.session.is_admin else 1,
                sale_channel=self.sale_channel.currentText() if self.session.is_admin else 'Retail',
            )
        except Exception as exc:
            QMessageBox.critical(self, "Could not save bill", str(exc))
            return

        # Invalidate the cart before any fallible work after the commit.
        self.cart = []
        self.matched_customer = None
        failures = []
        actions = [
            ("reset the bill form", self._clear_bill),
            ("clear customer details", self._clear_customer),
        ]
        if self.session.is_admin and wishlist_note and customer_details:
            actions.append(("save the customer request", lambda: self.db.add_wishlist(self.db.get_bill(bill_id)[0]['customer_id'], wishlist_note)))
        if self.on_bill_saved:
            actions.append(("refresh the screens", self.on_bill_saved))
        actions.append(("refresh customer suggestions", self._refresh_phone_completer))
        for description, action in actions:
            try:
                action()
            except Exception as exc:
                failures.append(f"Could not {description}: {exc}")

        try:
            bill_row, saved_bill_items = self.db.get_bill(bill_id)
            dialog = ReceiptDialog(bill_row, saved_bill_items, self)
            dialog.exec()
        except Exception as exc:
            failures.append(f"Could not open the receipt: {exc}")
        if failures:
            QMessageBox.warning(
                self, "Bill saved",
                f"Bill {bill_no} was saved. Ask an admin to open Sales History to view or reprint it.\n\n"
                + "\n".join(failures),
            )

    def _clear_bill(self):
        self.cart = []
        self._payment_user_edited = False
        self._last_total = 0.0
        self._render_cart()
        self._recalculate_totals()
        self._focus_barcode()
