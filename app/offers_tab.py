"""Offer management for admins; category/brand combinations can share a bundle."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QSplitter, QMessageBox, QScrollArea)
from paging import Pager
from widgets import make_heading, rupees


class OffersTab(QWidget):
    def __init__(self, db, on_changed=None, autoload=True):
        super().__init__()
        self.db = db
        self.on_changed = on_changed
        self.offer_id = None
        self.scopes = []
        self._ids = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16,16,16,16)
        outer.addWidget(make_heading('Offers', 'Create bundle prices that apply automatically while billing'))
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        outer.addWidget(split,1)
        left_box = QGroupBox('Your offers')
        left = QVBoxLayout(left_box)
        split.addWidget(left_box)
        filters = QHBoxLayout()
        self.status = QComboBox()
        self.status.addItems(['Active','Disabled'])
        self.status.currentIndexChanged.connect(self.refresh)
        filters.addWidget(self.status)
        self.search = QLineEdit()
        self.search.setPlaceholderText('Offer name starts with…')
        self.search.returnPressed.connect(self.refresh)
        filters.addWidget(self.search,1)
        search_button = QPushButton('Search')
        search_button.clicked.connect(self.refresh)
        filters.addWidget(search_button)
        left.addLayout(filters)
        self.table = QTableWidget(0,3)
        self.table.setHorizontalHeaderLabels(['Offer','Pieces','Bundle price + GST'])
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._select)
        left.addWidget(self.table,1)
        self.pager = Pager()
        self.pager.changed.connect(self.refresh)
        left.addWidget(self.pager)
        new = QPushButton('New offer')
        new.clicked.connect(self._new)
        left.addWidget(new)
        right_box = QGroupBox('Offer details')
        right = QVBoxLayout(right_box)
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setMinimumWidth(380)
        detail_scroll.setWidget(right_box)
        detail_panel = QWidget()
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(0,0,0,0)
        detail_layout.addWidget(detail_scroll,1)
        split.addWidget(detail_panel)
        split.setSizes([480,650])
        self.editing_label = QLabel('New offer')
        right.addWidget(self.editing_label)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(120)
        self.name.setPlaceholderText('e.g. Free Soul — any 3 for ₹2,000')
        form.addRow('Offer name:',self.name)
        self.quantity = QSpinBox()
        self.quantity.setRange(2,999999)
        self.quantity.setValue(3)
        form.addRow('Pieces per bundle:',self.quantity)
        self.price = QDoubleSpinBox()
        self.price.setRange(0,999999999)
        self.price.setDecimals(2)
        self.price.setPrefix('₹ ')
        form.addRow('Total bundle price before GST:',self.price)
        self.active = QCheckBox('Active — apply automatically at billing')
        self.active.setChecked(True)
        form.addRow(self.active)
        right.addLayout(form)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        right.addWidget(self.preview)
        self.quantity.valueChanged.connect(self._preview)
        self.price.valueChanged.connect(self._preview)
        self._preview()
        scope_box = QGroupBox('Eligible categories and brands — mix any of these')
        scope_layout = QVBoxLayout(scope_box)
        scope_form = QFormLayout()
        self.category = QComboBox()
        self.category.currentIndexChanged.connect(self._brands)
        self.brand = QComboBox()
        scope_form.addRow('Category:',self.category)
        scope_form.addRow('Brand / style:',self.brand)
        scope_layout.addLayout(scope_form)
        add = QPushButton('Add combination')
        add.clicked.connect(self._add_scope)
        scope_layout.addWidget(add)
        self.scope_table = QTableWidget(0,2)
        self.scope_table.setHorizontalHeaderLabels(['Category','Brand / style'])
        self.scope_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.scope_table.setMinimumHeight(110)
        self.scope_table.setMaximumHeight(180)
        self.scope_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.scope_table.setSelectionMode(QTableWidget.SingleSelection)
        self.scope_table.setEditTriggers(QTableWidget.NoEditTriggers)
        scope_layout.addWidget(self.scope_table)
        remove = QPushButton('Remove selected combination')
        remove.setProperty('role','secondary')
        remove.clicked.connect(self._remove_scope)
        scope_layout.addWidget(remove)
        right.addWidget(scope_box,1)
        rules = QLabel('Each complete bundle gets the offer price; remaining pieces keep their regular price. '
                       'GST is added at each item’s rate. A brand-specific offer takes priority over an all-brands offer. '
                       'Only one active offer is allowed per category / brand combination.')
        rules.setWordWrap(True)
        right.addWidget(rules)
        self.save = QPushButton('Save offer')
        self.save.clicked.connect(self._save)
        detail_layout.addWidget(self.save)
        if autoload:
            self.refresh()

    def _preview(self):
        self.preview.setText(f"Any {self.quantity.value()} eligible pieces for {rupees(self.price.value())} total + GST.")

    def refresh(self):
        current = self.category.currentData()
        self.category.blockSignals(True)
        self.category.clear()
        for row in self.db.get_categories():
            self.category.addItem(row['name'],row['id'])
        index = self.category.findData(current)
        if index>=0:
            self.category.setCurrentIndex(index)
        self.category.blockSignals(False)
        self._brands()
        active = self.status.currentIndex()==0
        search = self.search.text().strip()
        self.pager.filter((active,search))
        total,rows = self.db.offer_page(active,self.pager.size,self.pager.offset,search)
        self.pager.set_total(total)
        self._ids = [r['id'] for r in rows]
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        self.table.clearSelection()
        for index,row in enumerate(rows):
            for col,value in enumerate((row['name'],str(row['bundle_quantity']),rupees(row['bundle_cents']/100))):
                cell = QTableWidgetItem(value)
                cell.setToolTip(value)
                self.table.setItem(index,col,cell)
        self.table.blockSignals(False)

    def _brands(self):
        self.brand.clear()
        self.brand.addItem('All brands / styles',0)
        if self.category.currentData():
            for row in self.db.get_subtypes(self.category.currentData()):
                self.brand.addItem(row['name'],row['id'])

    def _add_scope(self):
        category,brand = self.category.currentData(),self.brand.currentData()
        if category is None:
            return
        if (category,brand) not in [(c,b) for c,b,_,_ in self.scopes]:
            self.scopes.append((category,brand,self.category.currentText(),self.brand.currentText()))
            self._render_scopes()

    def _render_scopes(self):
        self.scope_table.setRowCount(len(self.scopes))
        for index,(_,_,category,brand) in enumerate(self.scopes):
            self.scope_table.setItem(index,0,QTableWidgetItem(category))
            self.scope_table.setItem(index,1,QTableWidgetItem(brand))

    def _remove_scope(self):
        row = self.scope_table.currentRow()
        if 0<=row<len(self.scopes):
            del self.scopes[row]
            self._render_scopes()

    def _new(self):
        self.offer_id = None
        self.editing_label.setText('New offer')
        self.name.clear()
        self.quantity.setValue(3)
        self.price.setValue(0)
        self.active.setChecked(True)
        self.scopes = []
        self._render_scopes()
        self.table.clearSelection()

    def _select(self):
        row = self.table.currentRow()
        if not self.table.selectionModel().selectedRows() or not 0<=row<len(self._ids):
            return
        offer = self.db.get_offer(self._ids[row])
        if offer is None:
            self.refresh()
            return
        self.offer_id = offer['id']
        self.editing_label.setText(f"Editing offer #{offer['id']}")
        self.name.setText(offer['name'])
        self.quantity.setValue(offer['bundle_quantity'])
        self.price.setValue(offer['bundle_cents']/100)
        self.active.setChecked(bool(offer['active']))
        categories = {r['id']:r['name'] for r in self.db.get_categories()}
        self.scopes = []
        for category,brand in offer['scopes']:
            brands = {r['id']:r['name'] for r in self.db.get_subtypes(category)}
            self.scopes.append((category,brand,categories.get(category,'Archived category'),
                                brands.get(brand,'All brands / styles' if not brand else 'Removed brand')))
        self._render_scopes()

    def _save(self):
        try:
            self.offer_id = self.db.save_offer(self.name.text(),self.quantity.value(),self.price.value(),
                [(c,b) for c,b,_,_ in self.scopes],self.offer_id,self.active.isChecked())
        except (ValueError,PermissionError) as exc:
            QMessageBox.warning(self,'Could not save offer',str(exc))
            return
        self.editing_label.setText(f'Editing offer #{self.offer_id}')
        self.refresh()
        if self.on_changed:
            self.on_changed()
        QMessageBox.information(self,'Offer saved','The offer has been saved. New bills use the active offer price automatically.')
