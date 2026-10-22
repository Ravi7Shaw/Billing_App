"""Admin lookup -> returned quantities -> new purchases and difference payment."""
import uuid
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,QComboBox,
    QPushButton,QTableWidget,QTableWidgetItem,QHeaderView,QGroupBox,QSplitter,QSpinBox,QDialog,QMessageBox,QFormLayout)
from billing_tab import BillingTab
from receipt import ReceiptDialog
from widgets import make_heading,rupees
from money import money
from paging import Pager
from theme import COLORS


class ExchangeCart(BillingTab):
    def __init__(self, db, source_id, selections, quote, on_saved):
        self.source_id = source_id
        self.selections = dict(selections)
        self.exchange_quote = quote
        self.request_key = uuid.uuid4().hex
        self.on_exchange_saved = on_saved
        self.completed = False
        super().__init__(db)
        for field in (self.phone_input,self.customer_name_input,self.customer_address_input):
            field.setReadOnly(True)
        self.phone_input.setText(quote['source']['customer_phone'] or '')
        self.customer_name_input.setText(quote['source']['customer_name'] or 'Walk-in')
        self.wishlist_input.hide()
        self.payment_amount_input.setEnabled(False)
        self.bill_date_input.setEnabled(False)
        self._recalculate_totals()

    def _build_ui(self):
        super()._build_ui()
        self.layout().insertWidget(0,make_heading('Exchange purchases','Add the replacement items and any extra purchases'))

    def _refresh_phone_completer(self):
        # Customer is fixed to the source bill; do not load every customer phone.
        pass

    def _build_customer_box(self):
        box = super()._build_customer_box()
        for button in box.findChildren(QPushButton):
            button.hide()
        return box

    def _build_totals_box(self):
        box = super()._build_totals_box()
        self.exchange_label = QLabel()
        self.exchange_label.setTextFormat(Qt.PlainText)
        self.exchange_label.setWordWrap(True)
        box.layout().insertWidget(0,self.exchange_label)
        self.balance_label.setWordWrap(True)
        return box

    def _apply_permissions(self):
        super()._apply_permissions()
        # Exchange settlement always uses today's date and the full difference.
        self.sale_options.hide()
        self.payment_form.setRowVisible(self.payment_amount_input, False)
        self.payment_form.labelForField(self.balance_label).hide()
        for index in range(self.date_row.count()):
            widget = self.date_row.itemAt(index).widget()
            if widget:
                widget.hide()

    def _recalculate_totals(self):
        super()._recalculate_totals()
        credit = self.exchange_quote['credit_cents']/100
        difference = money(self._grand_total()-credit)
        self.exchange_label.setText(f"Exchange against {self.exchange_quote['source']['bill_no']} · Returned value: {rupees(credit)}")
        self.total_label.setText(f'Difference to collect: {rupees(max(difference,0))}')
        self.balance_label.setText('Add new purchases worth more than the returned value.' if difference<=0 else 'Full difference required · No refund')
        self.payment_amount_input.blockSignals(True)
        self.payment_amount_input.setMaximum(max(difference,0))
        self.payment_amount_input.setValue(max(difference,0))
        self.payment_amount_input.blockSignals(False)

    def _complete_bill(self):
        if self.completed:
            return
        if not self.cart:
            QMessageBox.warning(self,'Add purchases','Add the replacement items or new purchases first.')
            return
        items = []
        for row,tax in zip(self.cart,self._line_taxes()):
            items.append(dict(item_id=row['item_id'],name=row['name'],category=row['category'],quantity=row['qty'],
                rate=row['rate'],subtotal=max(money(row['amount']-row['discount']-row.get('offer_discount',0)),0),
                gst_rate=row.get('gst_rate',5),gst_amount=tax,offer_checked=True,
                offer_id_snapshot=row.get('offer_id'),offer_name_snapshot=row.get('offer_name',''),
                offer_discount=row.get('offer_discount',0)))
        try:
            bill_id,bill_no = self.db.save_exchange(self.source_id,self.selections,items,
                self.exchange_quote['credit_cents']/100,self.payment_amount_input.value(),
                self.payment_mode_combo.currentText(),self.request_key)
        except (ValueError,PermissionError) as exc:
            QMessageBox.warning(self,'Exchange not saved',str(exc))
            return
        except Exception as exc:
            QMessageBox.critical(self,'Exchange not saved',str(exc))
            return
        # Invalidate before opening a receipt or invoking another callback.
        self.completed = True
        self.cart = []
        try:
            bill,lines = self.db.get_bill(bill_id)
            ReceiptDialog(bill,lines,self).exec()
        except Exception as exc:
            QMessageBox.warning(self,'Exchange saved',f'Bill {bill_no} was saved. Open it in Sales History to reprint.\n{exc}')
        self.on_exchange_saved()


class ExchangesTab(QWidget):
    def __init__(self, db, autoload=True):
        super().__init__()
        self.db = db
        self.source = None
        self.quote = None
        self._bills = []
        self._quantities = {}
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16,16,16,16)
        outer.addWidget(make_heading('Exchanges','Find the original bill, select returned pieces, then bill the new purchases'))
        search = QHBoxLayout()
        self.search_mode = QComboBox()
        self.search_mode.addItems(['Customer phone','Bill number'])
        search.addWidget(self.search_mode)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('Enter the full phone number or bill number')
        self.search_input.returnPressed.connect(self.refresh)
        search.addWidget(self.search_input,1)
        find = QPushButton('Find bill')
        find.clicked.connect(self.refresh)
        search.addWidget(find)
        outer.addLayout(search)
        split = QSplitter(Qt.Vertical)
        outer.addWidget(split,1)
        bills_box = QGroupBox('Matching bills')
        bills_layout = QVBoxLayout(bills_box)
        self.bill_table = QTableWidget(0,5)
        self.bill_table.setHorizontalHeaderLabels(['Bill number','Date','Customer','Total','Outstanding'])
        self.bill_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.bill_table.horizontalHeader().setSectionResizeMode(2,QHeaderView.Stretch)
        self.bill_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.bill_table.setSelectionMode(QTableWidget.SingleSelection)
        self.bill_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.bill_table.itemSelectionChanged.connect(self._select_bill)
        bills_layout.addWidget(self.bill_table)
        self.pager = Pager()
        self.pager.size = 100
        self.pager.changed.connect(self.refresh)
        bills_layout.addWidget(self.pager)
        split.addWidget(bills_box)
        returns_box = QGroupBox('Items being brought back')
        returns_layout = QVBoxLayout(returns_box)
        source_row = QHBoxLayout()
        self.source_label = QLabel('Select a bill')
        self.source_label.setTextFormat(Qt.PlainText)
        self.source_label.setWordWrap(True)
        source_row.addWidget(self.source_label,1)
        self.view_receipt = QPushButton('View original receipt')
        self.view_receipt.clicked.connect(self._view_receipt)
        self.view_receipt.setEnabled(False)
        source_row.addWidget(self.view_receipt)
        returns_layout.addLayout(source_row)
        self.return_table = QTableWidget(0,6)
        self.return_table.setHorizontalHeaderLabels(['Item','Bought','Already exchanged','Available','Remaining paid value','Return pieces'])
        self.return_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.return_table.horizontalHeader().setSectionResizeMode(0,QHeaderView.Stretch)
        self.return_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.return_table.verticalHeader().setDefaultSectionSize(40)
        returns_layout.addWidget(self.return_table)
        split.addWidget(returns_box)
        split.setSizes([250,350])
        self.credit_label = QLabel('Returned items retain their original paid price, including offers and GST.')
        self.credit_label.setWordWrap(True)
        outer.addWidget(self.credit_label)
        self.start_button = QPushButton('Continue to new purchases')
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._start_exchange)
        outer.addWidget(self.start_button)
        if autoload:
            self.refresh()

    def refresh(self):
        mode = self.search_mode.currentIndex()==0
        text = self.search_input.text().strip()
        self.pager.filter((mode,text))
        count,rows = self.db.find_exchange_bills(text,mode,self.pager.size,self.pager.offset)
        self.pager.set_total(count)
        self._bills = rows
        self.bill_table.blockSignals(True)
        self.bill_table.setRowCount(len(rows))
        self.bill_table.clearSelection()
        for index,bill in enumerate(rows):
            values = [bill['bill_no'],bill['bill_date'][:10],bill['customer_name'] or 'Walk-in',
                      rupees(bill['total']),rupees(max(money(bill['total']-bill['paid']),0))]
            for col,value in enumerate(values):
                self.bill_table.setItem(index,col,QTableWidgetItem(value))
        self.bill_table.blockSignals(False)
        self._clear_source()
        if rows:
            self.bill_table.selectRow(0)
        elif text:
            self.source_label.setText('No matching bill. Check the full phone number or bill number.')

    def _clear_source(self):
        self.source = self.quote = None
        self._quantities = {}
        self.return_table.setRowCount(0)
        self.view_receipt.setEnabled(False)
        self.start_button.setEnabled(False)
        self.source_label.setText('Select a bill')
        self.credit_label.setText('Select the pieces being returned. The new purchases must cost more; collect the full difference.')

    def _select_bill(self):
        rows = self.bill_table.selectionModel().selectedRows()
        self._clear_source()
        if not rows or rows[0].row()>=len(self._bills):
            return
        bill = self._bills[rows[0].row()]
        try:
            self.source = self.db.exchange_source(bill['id'])
        except (ValueError,PermissionError) as exc:
            self.source_label.setText(str(exc))
            return
        self.view_receipt.setEnabled(True)
        self.source_label.setText(f"{bill['bill_no']} · {bill['customer_name'] or 'Walk-in'} · {bill['customer_phone'] or 'No phone'}")
        self.return_table.setRowCount(len(self.source['lines']))
        for index,line in enumerate(self.source['lines']):
            qty = line['quantity']
            returned = line['returned_qty']
            total = line['net_cents']+line['tax_cents']
            remaining = total-total*returned//qty
            values = [line['item_name_snapshot'],str(qty),str(returned),str(line['available_qty']),rupees(remaining/100)]
            for col,value in enumerate(values):
                self.return_table.setItem(index,col,QTableWidgetItem(value))
            spin = QSpinBox()
            spin.setRange(0,min(int(line['available_qty']),2147483647))
            spin.setEnabled(self.source['outstanding']==0 and line['available_qty']>0 and int(qty)==qty)
            spin.valueChanged.connect(self._update_quote)
            self.return_table.setCellWidget(index,5,spin)
            self._quantities[line['id']] = spin
        if self.source['outstanding']:
            self.credit_label.setText(f"Outstanding: {rupees(self.source['outstanding'])}. Collect the original bill's payment in Balances before exchanging.")
        elif not any(r['available_qty']>0 for r in self.source['lines']):
            self.credit_label.setText('No pieces remain available for exchange on this bill.')

    def _selections(self):
        return {line:spin.value() for line,spin in self._quantities.items() if spin.value()>0}

    def _update_quote(self):
        self.quote = None
        self.start_button.setEnabled(False)
        if self.source is None or not self._selections():
            self.credit_label.setText('Select at least one piece to exchange.')
            return
        try:
            self.quote = self.db.quote_exchange(self.source['bill']['id'],self._selections())
        except (ValueError,PermissionError) as exc:
            self.credit_label.setText(str(exc))
            return
        self.credit_label.setText(f"Returned value including GST: {rupees(self.quote['credit_cents']/100)} · New purchases must cost more · No refund")
        self.start_button.setEnabled(True)

    def _view_receipt(self):
        if self.source:
            bill,items = self.db.get_bill(self.source['bill']['id'])
            ReceiptDialog(bill,items,self).exec()

    def _start_exchange(self):
        self._update_quote()
        if self.quote is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Exchange — new purchases and difference payment')
        dialog.setObjectName('exchangeDialog')
        dialog.setStyleSheet(f"QDialog#exchangeDialog {{ background: {COLORS['bg']}; }}")
        dialog.resize(1300,850)
        layout = QVBoxLayout(dialog)
        cart = ExchangeCart(self.db,self.source['bill']['id'],self._selections(),self.quote,dialog.accept)
        layout.addWidget(cart)
        dialog.exec()
        self.refresh()
