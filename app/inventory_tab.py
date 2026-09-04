"""
inventory_tab.py
-----------------
Catalog management: categories -> subtypes (brand/style) -> items.
Nothing here is hardcoded; every row shown originates from the database and
everything can be added, renamed, or removed from this screen.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox, QLabel,
    QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox, QPushButton, QListWidget,
    QListWidgetItem, QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
    QDialog, QDialogButtonBox, QInputDialog
)
from PySide6.QtCore import Qt

from widgets import rupees, make_heading

NO_SUBTYPE = -1


class ItemDialog(QDialog):
    """Add/Edit dialog for a single catalog item."""

    def __init__(self, db, category_id, item_row=None, parent=None):
        super().__init__(parent)
        self.db = db
        self.item_row = item_row
        self.setWindowTitle("Edit Item" if item_row else "Add Item")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.category_combo = QComboBox()
        cats = self.db.get_categories()
        for c in cats:
            self.category_combo.addItem(c["name"], c["id"])
        if category_id:
            idx = self.category_combo.findData(category_id)
            if idx >= 0:
                self.category_combo.setCurrentIndex(idx)
        self.category_combo.currentIndexChanged.connect(self._reload_subtypes)
        form.addRow("Category:", self.category_combo)

        self.subtype_combo = QComboBox()
        form.addRow("Brand / Style:", self.subtype_combo)
        self._reload_subtypes()

        self.name_input = QLineEdit()
        form.addRow("Name:", self.name_input)

        self.barcode_input = QLineEdit()
        form.addRow("Barcode (optional):", self.barcode_input)

        self.size_input = QLineEdit()
        form.addRow("Size:", self.size_input)

        self.color_input = QLineEdit()
        form.addRow("Color:", self.color_input)

        self.rate_input = QDoubleSpinBox()
        self.rate_input.setRange(0, 999999)
        self.rate_input.setDecimals(2)
        self.rate_input.setPrefix("Rs. ")
        form.addRow("Rate:", self.rate_input)

        self.stock_input = QSpinBox()
        self.stock_input.setRange(0, 999999)
        form.addRow("Stock qty:", self.stock_input)

        if item_row:
            self.name_input.setText(item_row["name"])
            self.barcode_input.setText(item_row["barcode"] or "")
            self.size_input.setText(item_row["size"] or "")
            self.color_input.setText(item_row["color"] or "")
            self.rate_input.setValue(item_row["rate"])
            self.stock_input.setValue(item_row["stock_qty"])
            if item_row["subtype_id"]:
                idx = self.subtype_combo.findData(item_row["subtype_id"])
                if idx >= 0:
                    self.subtype_combo.setCurrentIndex(idx)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _reload_subtypes(self):
        self.subtype_combo.clear()
        cat_id = self.category_combo.currentData()
        subtypes = self.db.get_subtypes(cat_id) if cat_id else []
        self.subtype_combo.addItem("(No brand / style)", NO_SUBTYPE)
        for s in subtypes:
            self.subtype_combo.addItem(s["name"], s["id"])

    def _on_save(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing info", "Item name is required.")
            return
        if self.rate_input.value() <= 0:
            QMessageBox.warning(self, "Missing info", "Rate must be greater than 0.")
            return
        self.result_data = {
            "name": name,
            "category_id": self.category_combo.currentData(),
            "subtype_id": None if self.subtype_combo.currentData() == NO_SUBTYPE else self.subtype_combo.currentData(),
            "barcode": self.barcode_input.text().strip(),
            "size": self.size_input.text().strip(),
            "color": self.color_input.text().strip(),
            "rate": self.rate_input.value(),
            "stock_qty": self.stock_input.value(),
        }
        self.accept()


class InventoryTab(QWidget):
    def __init__(self, db, on_catalog_changed=None):
        super().__init__()
        self.db = db
        self.on_catalog_changed = on_catalog_changed
        self.selected_category_id = None
        self._build_ui()
        self._refresh_categories()

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)
        outer.addWidget(make_heading("Inventory", "Manage categories, brands/styles, and items \u2014 nothing is fixed in code"))

        content = QHBoxLayout()
        content.setSpacing(14)
        outer.addLayout(content, 1)

        # --- categories column
        cat_box = QGroupBox("Categories")
        cat_box.setMinimumWidth(240)
        cat_box.setMaximumWidth(280)
        cat_v = QVBoxLayout(cat_box)
        cat_v.setSpacing(10)
        self.category_list = QListWidget()
        self.category_list.currentItemChanged.connect(self._on_category_selected)
        cat_v.addWidget(self.category_list)

        add_cat_btn = QPushButton("+ Add Category")
        add_cat_btn.clicked.connect(self._add_category)
        cat_v.addWidget(add_cat_btn)

        cat_btn_row = QHBoxLayout()
        cat_btn_row.setSpacing(8)
        rename_cat_btn = QPushButton("Rename")
        rename_cat_btn.setProperty("role", "secondary")
        rename_cat_btn.clicked.connect(self._rename_category)
        del_cat_btn = QPushButton("Delete")
        del_cat_btn.setProperty("role", "danger")
        del_cat_btn.clicked.connect(self._delete_category)
        cat_btn_row.addWidget(rename_cat_btn)
        cat_btn_row.addWidget(del_cat_btn)
        cat_v.addLayout(cat_btn_row)
        content.addWidget(cat_box)

        # --- subtypes column
        sub_box = QGroupBox("Brands / Styles")
        sub_box.setMinimumWidth(240)
        sub_box.setMaximumWidth(280)
        sub_v = QVBoxLayout(sub_box)
        sub_v.setSpacing(10)
        self.subtype_list = QListWidget()
        sub_v.addWidget(self.subtype_list)

        add_sub_btn = QPushButton("+ Add Brand/Style")
        add_sub_btn.clicked.connect(self._add_subtype)
        sub_v.addWidget(add_sub_btn)

        sub_btn_row = QHBoxLayout()
        sub_btn_row.setSpacing(8)
        rename_sub_btn = QPushButton("Rename")
        rename_sub_btn.setProperty("role", "secondary")
        rename_sub_btn.clicked.connect(self._rename_subtype)
        del_sub_btn = QPushButton("Delete")
        del_sub_btn.setProperty("role", "danger")
        del_sub_btn.clicked.connect(self._delete_subtype)
        sub_btn_row.addWidget(rename_sub_btn)
        sub_btn_row.addWidget(del_sub_btn)
        sub_v.addLayout(sub_btn_row)
        content.addWidget(sub_box)

        # --- items column
        items_box = QGroupBox("Items")
        items_v = QVBoxLayout(items_box)

        search_row = QHBoxLayout()
        self.item_search_input = QLineEdit()
        self.item_search_input.setPlaceholderText("Search items...")
        self.item_search_input.textChanged.connect(self._refresh_items)
        search_row.addWidget(self.item_search_input)
        items_v.addLayout(search_row)

        self.items_table = QTableWidget(0, 7)
        self.items_table.setHorizontalHeaderLabels(["Name", "Brand/Style", "Barcode", "Rate", "Stock", "", ""])
        items_header = self.items_table.horizontalHeader()
        items_header.setSectionResizeMode(0, QHeaderView.Stretch)
        items_header.setSectionResizeMode(5, QHeaderView.Fixed)
        items_header.setSectionResizeMode(6, QHeaderView.Fixed)
        self.items_table.setColumnWidth(5, 80)
        self.items_table.setColumnWidth(6, 80)
        self.items_table.verticalHeader().setDefaultSectionSize(40)
        self.items_table.setAlternatingRowColors(True)
        self.items_table.setEditTriggers(QTableWidget.NoEditTriggers)
        items_v.addWidget(self.items_table)

        item_btn_row = QHBoxLayout()
        add_item_btn = QPushButton("+ Add Item")
        add_item_btn.clicked.connect(self._add_item)
        item_btn_row.addWidget(add_item_btn)
        item_btn_row.addStretch()
        items_v.addLayout(item_btn_row)

        content.addWidget(items_box, 1)

    # ------------------------------------------------------------ refresh
    def _refresh_categories(self, select_id=None):
        self.category_list.blockSignals(True)
        self.category_list.clear()
        for c in self.db.get_categories():
            item = QListWidgetItem(c["name"])
            item.setData(Qt.UserRole, c["id"])
            self.category_list.addItem(item)
            if select_id and c["id"] == select_id:
                self.category_list.setCurrentItem(item)
        self.category_list.blockSignals(False)
        if self.category_list.count() and self.category_list.currentRow() < 0:
            self.category_list.setCurrentRow(0)
        self._on_category_selected()

    def _on_category_selected(self, *_):
        item = self.category_list.currentItem()
        self.selected_category_id = item.data(Qt.UserRole) if item else None
        self._refresh_subtypes()
        self._refresh_items()

    def _refresh_subtypes(self, select_id=None):
        self.subtype_list.blockSignals(True)
        self.subtype_list.clear()
        if self.selected_category_id:
            for s in self.db.get_subtypes(self.selected_category_id):
                item = QListWidgetItem(s["name"])
                item.setData(Qt.UserRole, s["id"])
                self.subtype_list.addItem(item)
                if select_id and s["id"] == select_id:
                    self.subtype_list.setCurrentItem(item)
        self.subtype_list.blockSignals(False)

    def _refresh_items(self):
        self.items_table.setRowCount(0)
        if not self.selected_category_id:
            return
        search = self.item_search_input.text().strip() or None
        items = self.db.get_items(category_id=self.selected_category_id, search=search)
        self.items_table.setRowCount(len(items))
        for row_idx, it in enumerate(items):
            self.items_table.setItem(row_idx, 0, QTableWidgetItem(it["name"]))
            self.items_table.setItem(row_idx, 1, QTableWidgetItem(it["subtype_name"] or "-"))
            self.items_table.setItem(row_idx, 2, QTableWidgetItem(it["barcode"] or "-"))
            self.items_table.setItem(row_idx, 3, QTableWidgetItem(rupees(it["rate"])))
            self.items_table.setItem(row_idx, 4, QTableWidgetItem(str(it["stock_qty"])))

            edit_btn = QPushButton("Edit")
            edit_btn.setProperty("role", "secondary")
            edit_btn.setProperty("compact", "true")
            edit_btn.clicked.connect(lambda _, i=it["id"]: self._edit_item(i))
            self.items_table.setCellWidget(row_idx, 5, edit_btn)

            del_btn = QPushButton("Delete")
            del_btn.setProperty("role", "danger")
            del_btn.setProperty("compact", "true")
            del_btn.clicked.connect(lambda _, i=it["id"]: self._delete_item(i))
            self.items_table.setCellWidget(row_idx, 6, del_btn)
        self.items_table.resizeRowsToContents()

    def _notify_catalog_changed(self):
        if self.on_catalog_changed:
            self.on_catalog_changed()

    # ---------------------------------------------------------- categories
    def _add_category(self):
        name, ok = QInputDialog.getText(self, "Add Category", "Category name:")
        if ok and name.strip():
            try:
                self.db.add_category(name.strip())
            except Exception as e:
                QMessageBox.warning(self, "Could not add", str(e))
                return
            self._refresh_categories()
            self._notify_catalog_changed()

    def _rename_category(self):
        item = self.category_list.currentItem()
        if not item:
            return
        name, ok = QInputDialog.getText(self, "Rename Category", "New name:", text=item.text())
        if ok and name.strip():
            self.db.rename_category(item.data(Qt.UserRole), name.strip())
            self._refresh_categories(select_id=item.data(Qt.UserRole))
            self._notify_catalog_changed()

    def _delete_category(self):
        item = self.category_list.currentItem()
        if not item:
            return
        confirm = QMessageBox.question(
            self, "Delete Category",
            f"Delete category '{item.text()}'? Items using it must be removed first."
        )
        if confirm == QMessageBox.Yes:
            try:
                self.db.delete_category(item.data(Qt.UserRole))
            except Exception as e:
                QMessageBox.warning(self, "Could not delete",
                                     "This category still has items in it. Delete or move those items first.")
                return
            self._refresh_categories()
            self._notify_catalog_changed()

    # ------------------------------------------------------------ subtypes
    def _add_subtype(self):
        if not self.selected_category_id:
            QMessageBox.information(self, "Select a category", "Choose a category first.")
            return
        name, ok = QInputDialog.getText(self, "Add Brand / Style", "Name:")
        if ok and name.strip():
            try:
                self.db.add_subtype(self.selected_category_id, name.strip())
            except Exception as e:
                QMessageBox.warning(self, "Could not add", str(e))
                return
            self._refresh_subtypes()
            self._notify_catalog_changed()

    def _rename_subtype(self):
        item = self.subtype_list.currentItem()
        if not item:
            return
        name, ok = QInputDialog.getText(self, "Rename", "New name:", text=item.text())
        if ok and name.strip():
            self.db.rename_subtype(item.data(Qt.UserRole), name.strip())
            self._refresh_subtypes(select_id=item.data(Qt.UserRole))
            self._notify_catalog_changed()

    def _delete_subtype(self):
        item = self.subtype_list.currentItem()
        if not item:
            return
        confirm = QMessageBox.question(self, "Delete", f"Delete '{item.text()}'?")
        if confirm == QMessageBox.Yes:
            self.db.delete_subtype(item.data(Qt.UserRole))
            self._refresh_subtypes()
            self._refresh_items()
            self._notify_catalog_changed()

    # ---------------------------------------------------------------- items
    def _add_item(self):
        if not self.selected_category_id:
            QMessageBox.information(self, "Select a category", "Choose a category first.")
            return
        dialog = ItemDialog(self.db, self.selected_category_id, parent=self)
        if dialog.exec() == QDialog.Accepted:
            d = dialog.result_data
            try:
                self.db.add_item(
                    d["name"], d["category_id"], d["subtype_id"], d["barcode"],
                    d["size"], d["color"], d["rate"], d["stock_qty"]
                )
            except Exception as e:
                QMessageBox.warning(self, "Could not add item", "That barcode may already be in use.")
                return
            self._refresh_items()
            self._notify_catalog_changed()

    def _edit_item(self, item_id):
        item_row = self.db.get_item_by_id(item_id)
        if not item_row:
            return
        dialog = ItemDialog(self.db, item_row["category_id"], item_row=item_row, parent=self)
        if dialog.exec() == QDialog.Accepted:
            d = dialog.result_data
            try:
                self.db.update_item(
                    item_id, d["name"], d["category_id"], d["subtype_id"], d["barcode"],
                    d["size"], d["color"], d["rate"], d["stock_qty"]
                )
            except Exception:
                QMessageBox.warning(self, "Could not update item", "That barcode may already be in use.")
                return
            self._refresh_items()
            self._notify_catalog_changed()

    def _delete_item(self, item_id):
        confirm = QMessageBox.question(self, "Delete Item", "Remove this item from the catalog?")
        if confirm == QMessageBox.Yes:
            self.db.delete_item(item_id)
            self._refresh_items()
            self._notify_catalog_changed()
