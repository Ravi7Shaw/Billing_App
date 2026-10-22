"""Admin purchase and accounting workspace; forms scroll, actions stay visible."""
from html import escape
import json
import sqlite3

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QScrollArea,
    QTabWidget, QLabel, QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QDialog, QDialogButtonBox,
    QDateEdit, QInputDialog, QAbstractItemView)
from commerce import cents
from reporting import money_cents
from receipt import ReceiptDialog, SHOP_NAME, SHOP_PHONE, SHOP_GSTIN, SHOP_ADDRESS
from report_export import export_csv
from period_filter import PeriodFilter
from window_utils import fit_window


def amount(value):
    return 'Unknown' if value is None else f'{value / 100:,.2f}'


def combo(rows=()):
    widget=QComboBox()
    widget.setMinimumWidth(0)
    widget.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
    widget.setMinimumContentsLength(12)
    fill(widget,rows)
    return widget


def fill(widget,rows):
    current=widget.currentData()
    widget.blockSignals(True)
    widget.clear()
    for label,key in rows:
        widget.addItem(label,key)
    index=widget.findData(current)
    if index>=0:
        widget.setCurrentIndex(index)
    widget.blockSignals(False)


def number(integer=False,maximum=1e9):
    widget=QSpinBox() if integer else QDoubleSpinBox()
    widget.setRange(0,int(maximum) if integer else maximum)
    if not integer:
        widget.setDecimals(2)
    return widget


def date_field():
    widget=QDateEdit(QDate.currentDate())
    widget.setCalendarPopup(True)
    widget.setDisplayFormat('dd MMM yyyy')
    return widget


def iso(widget):
    return widget.date().toString('yyyy-MM-dd')


def button(text,callback,layout):
    widget=QPushButton(text)
    widget.clicked.connect(callback)
    layout.addWidget(widget)
    return widget


def note(text,layout):
    widget=QLabel(text)
    widget.setWordWrap(True)
    layout.addWidget(widget)
    return widget


def table(headers):
    widget=QTableWidget(0,len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SingleSelection)
    widget.setAlternatingRowColors(True)
    widget.verticalHeader().hide()
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.setMinimumSize(0,100)
    return widget


def populate(widget,rows):
    widget.setRowCount(len(rows))
    for i,row in enumerate(rows):
        for j,value in enumerate(row):
            widget.setItem(i,j,QTableWidgetItem('' if value is None else str(value)))


def scroll(content):
    area=QScrollArea()
    area.setWidgetResizable(True)
    area.setMinimumWidth(0)
    area.setWidget(content)
    return area


def form_dialog(parent,title,fields,save):
    dialog=QDialog(parent)
    dialog.setWindowTitle(title)
    layout=QVBoxLayout(dialog)
    body=QWidget(); form=QFormLayout(body)
    form.setRowWrapPolicy(QFormLayout.WrapLongRows)
    for label,widget in fields:
        form.addRow(label,widget)
    layout.addWidget(scroll(body),1)
    controls=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
    layout.addWidget(controls)
    controls.rejected.connect(dialog.reject)
    def submit():
        try:
            save()
        except (ValueError,sqlite3.Error,PermissionError) as exc:
            QMessageBox.warning(dialog,'Could not save',str(exc))
            return
        dialog.accept()
    controls.accepted.connect(submit)
    fit_window(dialog,560,620)
    return dialog.exec()==QDialog.Accepted


def purchase_html(p,lines):
    def party(key):
        data=json.loads(p[key])
        return '<br>'.join(escape(data[k]) for k in ('name','phone','gst','address') if data.get(k) and (k!='gst' or p['gst_enabled']))
    body=''.join('<tr>'+''.join('<td>'+escape(str(v))+'</td>' for v in (
        r['name'],f"{r['quantity']} {r['unit_name']} ({r['unit_pieces']} pcs/unit)",amount(r['rate_cents']),
        amount(r['discount_cents']),amount(r['net_cents']),f"{r['gst_rate']:g}%",amount(r['gst_cents']),amount(r['net_cents']+r['gst_cents'])))+'</tr>' for r in lines)
    return f'''<html><head><style>body {{font-family:Arial;font-size:9pt}} td,th {{padding:3px}} .items {{font-size:8pt}} h1 {{color:#214a3b}}</style></head><body>
        <h1>Purchase receipt P-{p['id']}</h1><p>{'With GST' if p['gst_enabled'] else 'Without GST'} · {escape(p['purchase_date'])}<br>
        Supplier invoice: {escape(p['invoice_no'])}<br>Receiving godown: {escape(p['godown'])}</p>
        <table width="100%"><tr><td><b>Seller</b><br>{party('seller_json')}</td><td><b>Buyer</b><br>{party('buyer_json')}</td></tr></table>
        <table class="items" border="1" cellspacing="0" width="100%"><tr><th>Item</th><th>Quantity</th><th>Rate/unit</th><th>Discount</th><th>Net</th><th>GST %</th><th>GST</th><th>Total</th></tr>{body}</table>
        <p>Net: Rs. {amount(p['net_cents'])}<br>GST: Rs. {amount(p['gst_cents'])}<br><b>Total: Rs. {amount(p['total_cents'])}</b><br>
        Paid: Rs. {amount(p['paid_cents'])}<br>Balance payable: Rs. {amount(p['total_cents']-p['paid_cents'])}</p></body></html>'''


def show_purchase(db,pid,parent):
    p,lines=db.get_purchase(pid)
    dialog=ReceiptDialog(dict(bill_no=f'P-{pid}',total=0),[],parent,html=purchase_html(p,lines))
    dialog.setWindowTitle(f'Purchase receipt P-{pid}')
    dialog.exec()


class PurchasesTab(QWidget):
    def __init__(self,db,on_saved=None):
        super().__init__()
        self.db=db; self.on_saved=on_saved; self.lines=[]; self.saved_id=None
        layout=QVBoxLayout(self)
        self.pages=QTabWidget(); layout.addWidget(self.pages,1)
        details=QWidget(); form=QFormLayout(details)
        form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.supplier=combo(); self.supplier.currentIndexChanged.connect(self._supplier_changed)
        self.invoice=QLineEdit(); self.date=date_field(); self.godown=combo()
        self.mode=combo([('Without GST',False),('With GST',True)])
        self.mode.currentIndexChanged.connect(self._mode_changed)
        for label,field in [('Supplier ledger',self.supplier),('Supplier invoice no.',self.invoice),('Purchase date',self.date),('Receiving godown',self.godown),('GST mode',self.mode)]:
            form.addRow(label,field)
        add=QPushButton('Add supplier ledger');add.clicked.connect(self._new_supplier);form.addRow(add)
        self.parties={}
        for title,defaults in [('Seller',{}),('Buyer',dict(name=SHOP_NAME,phone=SHOP_PHONE,gst=SHOP_GSTIN,address=SHOP_ADDRESS))]:
            form.addRow(QLabel('<b>'+title+'</b>'))
            fields={}
            for key in ('name','phone','gst','address'):
                field=QLineEdit(defaults.get(key,''));fields[key]=field
                field.setPlaceholderText('Optional' if key=='address' else ('Required only with GST' if key=='gst' else 'Required'))
                form.addRow(key.upper() if key=='gst' else key.title(),field)
            self.parties[title]=fields
        self.pages.addTab(scroll(details),'Seller / Buyer')
        items=QWidget(); item_layout=QVBoxLayout(items)
        line_controls=QWidget(); line_form=QFormLayout(line_controls);line_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.item=combo();self.unit=combo();self.qty=number(True,1000000);self.qty.setMinimum(1)
        self.rate=number();self.discount=number();self.gst=number(maximum=100)
        self.item.currentIndexChanged.connect(self._item_changed)
        for label,field in [('Item',self.item),('Purchase unit',self.unit),('Quantity (units)',self.qty),('Price per unit, before GST',self.rate),('Line discount (Rs.)',self.discount),('GST %',self.gst)]:
            line_form.addRow(label,field)
        add=QPushButton('Add item');add.clicked.connect(self._add);line_form.addRow(add)
        self.pages.addTab(scroll(line_controls),'Add items')
        self.cart=table(['Item','Units','Pieces/unit','Price/unit','Discount','GST %','Total'])
        item_layout.addWidget(self.cart,1)
        controls=QHBoxLayout();item_layout.addLayout(controls)
        button('Remove selected',self._remove,controls)
        button('Edit selected',self._edit,controls)
        self.total_label=note('Add items to begin.',item_layout)
        payment=QFormLayout();item_layout.addLayout(payment)
        self.paid=number();self.payment_mode=combo([(s,s) for s in ('Cash','Card','UPI','Bank','Other')])
        payment.addRow('Paid now (Rs.)',self.paid);payment.addRow('Payment method',self.payment_mode)
        self.paid.valueChanged.connect(self._balance)
        self.balance=note('',item_layout)
        self.pages.addTab(items,'Purchase bill')
        footer=QHBoxLayout();layout.addLayout(footer)
        self.save_button=button('Save purchase',self._save,footer)
        self.receipt_button=button('Last receipt',self._receipt,footer);self.receipt_button.setEnabled(False)
        self.clear_button=button('Clear draft',self._clear,footer)
        self.total=0
        self.refresh();self._mode_changed()

    def refresh(self):
        fill(self.supplier,[(r['name'],r['id']) for r in self.db.get_suppliers()])
        fill(self.godown,[(r['name'],r['id']) for r in self.db.get_godowns()])
        fill(self.item,[(r['name']+' / '+r['category_name'],r['id']) for r in self.db.get_items()])
        fill(self.unit,[(f"{r['name']} = {r['pieces']} pieces",r['id']) for r in self.db.get_stock_units()])
        if not self.parties['Seller']['name'].text():
            self._supplier_changed()

    def _item_changed(self):
        row=next((r for r in self.db.stock_report() if r['id']==self.item.currentData()),None)
        if row:
            units=self.db.get_stock_units()
            unit=next((r for r in units if r['name']==row['unit']),None)
            if unit:self.unit.setCurrentIndex(self.unit.findData(unit['id']))

    def _supplier_changed(self):
        row=next((r for r in self.db.get_suppliers() if r['id']==self.supplier.currentData()),None)
        if row:
            for key,field in self.parties['Seller'].items():field.setText(row[key] or '')

    def _new_supplier(self):
        if ledger_dialog(self,self.db):self.refresh();self._supplier_changed()

    def _mode_changed(self):
        enabled=bool(self.mode.currentData())
        self.gst.setEnabled(enabled)
        for fields in self.parties.values():fields['gst'].setEnabled(enabled)
        self._render()

    def _add(self):
        line=dict(item_id=self.item.currentData(),unit_id=self.unit.currentData(),quantity=self.qty.value(),rate=self.rate.value(),discount=self.discount.value(),gst_rate=self.gst.value())
        try:self.db.quote_purchase([line],self.mode.currentData())
        except (ValueError,sqlite3.Error,PermissionError) as exc:
            QMessageBox.warning(self,'Invalid item',str(exc));return
        self.lines.append(line);self._render();self.pages.setCurrentIndex(2)

    def _remove(self):
        row=self.cart.currentRow()
        if row>=0:self.lines.pop(row);self._render()

    def _edit(self):
        row=self.cart.currentRow()
        if row<0:return
        line=self.lines.pop(row)
        self.item.setCurrentIndex(self.item.findData(line['item_id']));self.unit.setCurrentIndex(self.unit.findData(line['unit_id']))
        for key,field in [('quantity',self.qty),('rate',self.rate),('discount',self.discount),('gst_rate',self.gst)]:field.setValue(line[key])
        self._render();self.pages.setCurrentIndex(1)

    def _render(self):
        if not hasattr(self,'cart'):return
        try:rows=self.db.quote_purchase(self.lines,self.mode.currentData()) if self.lines else []
        except (ValueError,sqlite3.Error,PermissionError) as exc:
            self.total_label.setText(str(exc));self.save_button.setEnabled(False);return
        self.save_button.setEnabled(True)
        populate(self.cart,[(r['name'],f"{r['quantity']} {r['unit_name']}",r['unit_pieces'],amount(r['rate_cents']),amount(r['discount_cents']),r['gst_rate'],amount(r['net_cents']+r['gst_cents'])) for r in rows])
        net=sum(r['net_cents'] for r in rows);gst=sum(r['gst_cents'] for r in rows);self.total=net+gst
        self.total_label.setText(f'Net Rs. {amount(net)}  |  GST Rs. {amount(gst)}  |  Total Rs. {amount(self.total)}')
        self._balance()

    def _balance(self):
        self.balance.setText('Balance payable: Rs. '+amount(self.total-cents(self.paid.value())))

    def _save(self):
        try:
            seller={k:f.text() for k,f in self.parties['Seller'].items()};buyer={k:f.text() for k,f in self.parties['Buyer'].items()}
            pid=self.db.save_purchase(self.supplier.currentData(),seller,buyer,self.invoice.text(),self.lines,
                self.mode.currentData(),self.godown.currentData(),self.paid.value(),self.payment_mode.currentText(),iso(self.date))
        except (ValueError,sqlite3.Error,PermissionError) as exc:
            QMessageBox.warning(self,'Purchase not saved',str(exc));return
        self.saved_id=pid;self.lines=[];self.invoice.clear();self.paid.setValue(0);self._render()
        self.receipt_button.setEnabled(True)
        QMessageBox.information(self,'Purchase saved',f'Purchase P-{pid} saved. Stock and supplier balance updated.')
        if self.on_saved:self.on_saved()
        self._receipt()

    def _receipt(self):
        if self.saved_id:
            try:show_purchase(self.db,self.saved_id,self)
            except (ValueError,sqlite3.Error,PermissionError) as exc:QMessageBox.warning(self,'Receipt unavailable',str(exc))

    def _clear(self):
        if self.lines and QMessageBox.question(self,'Clear draft','Discard the unfinished purchase?',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.lines=[];self.invoice.clear();self.paid.setValue(0);self._render()


def ledger_dialog(parent,db,record=None):
    name=QLineEdit(record['name'] if record else '')
    groups=combo([(r['name'],r['id']) for r in db.get_account_groups()])
    kind=combo([(s,s) for s in ('Supplier','General')])
    phone=QLineEdit();gst=QLineEdit();address=QLineEdit()
    if record:
        groups.setCurrentIndex(groups.findData(record['group_id']));kind.setCurrentText(record['kind']);kind.setEnabled(False)
        for field,key in [(phone,'phone'),(gst,'gst'),(address,'address')]:field.setText(record[key])
    else:
        index=groups.findText('Suppliers');groups.setCurrentIndex(index)
    return form_dialog(parent,'Ledger', [('Name',name),('Group',groups),('Type',kind),('Phone',phone),('GSTIN',gst),('Address (optional)',address)],
        lambda:db.save_account_ledger(name.text(),groups.currentData(),kind.currentText(),phone.text(),gst.text(),address.text(),record['id'] if record else None))


class ChartAccountsTab(QWidget):
    def __init__(self,db,on_changed=None,on_purchase=None):
        super().__init__();self.db=db;self.on_changed=on_changed;self.on_purchase=on_purchase
        layout=QVBoxLayout(self)
        nav=QHBoxLayout();layout.addLayout(nav)
        self.section=combo([(s,i) for i,s in enumerate(('Account master','Inventory master','Godowns','Reports'))])
        nav.addWidget(self.section,1);button('New purchase',lambda:on_purchase() if on_purchase else None,nav)
        self.pages=QTabWidget();self.pages.tabBar().hide();layout.addWidget(self.pages,1)
        self.section.currentIndexChanged.connect(self.pages.setCurrentIndex)
        self._accounts();self._inventory();self._godowns();self._reports()
        self.refresh()

    def _guard(self,action):
        try:return action()
        except (ValueError,sqlite3.Error,PermissionError) as exc:
            QMessageBox.warning(self,'Could not complete action',str(exc))

    def _accounts(self):
        page=QWidget();layout=QVBoxLayout(page)
        controls=QHBoxLayout();layout.addLayout(controls)
        button('Groups',lambda:self._master('Account group'),controls)
        button('Add ledger',lambda:self._ledger(),controls)
        button('Edit ledger',lambda:self._ledger(True),controls)
        button('Voucher',lambda:self._guard(self._voucher),controls)
        self.ledger=combo();self.ledger.currentIndexChanged.connect(lambda:self._guard(self._statement))
        layout.addWidget(self.ledger)
        self.account_period=PeriodFilter('All time');layout.addWidget(self.account_period)
        self.account_period.changed.connect(lambda:self._guard(self._statement))
        self.ledger_summary=note('',layout)
        self.statement_table=table(['Date','Reference','Debit account','Credit account','Amount','Running balance (Dr + / Cr −)','Narration'])
        layout.addWidget(self.statement_table,1)
        actions=QHBoxLayout();layout.addLayout(actions)
        button('Export statement',lambda:self._export(self.statement_table,'ledger'),actions)
        button('Apply advance',lambda:self._guard(self._advance),actions)
        note('Payments and receipts allocated to an invoice update its outstanding balance. Unallocated vouchers hold advances. Journal / credit / debit notes adjust ledger balances; they do not change invoice totals.',layout)
        self.pages.addTab(page,'Accounts')

    def _statement(self):
        if not hasattr(self,'statement_table'):return
        start,end=self.account_period.bounds()
        data=self.db.account_statement(self.ledger.currentData(),start or '0001-01-01',end or '9999-12-31')
        self.ledger_summary.setText(f"Opening Rs. {amount(data['opening_cents'])} | Closing Rs. {amount(data['balance_cents'])} (positive = debit, negative = credit)")
        populate(self.statement_table,[(r['date'],r['reference'],r['debit_name'],r['credit_name'],amount(r['amount_cents']),amount(r['balance_cents']),r['notes']) for r in data['rows']])

    def _ledger(self,edit=False):
        record=None
        if edit:
            key=self.ledger.currentData()
            record=next((r for r in self.db.get_account_ledgers() if r['key']==key and r['kind'] in ('Supplier','General')),None)
            if not record:
                QMessageBox.information(self,'Select ledger','Select a supplier or general ledger. Customer profiles are edited in Customers.');return
        if ledger_dialog(self,self.db,record):self.refresh()

    def _voucher(self):
        kind=combo([(s,s) for s in ('Payment','Receipt','Journal','Credit note','Debit note','Opening balance')])
        ledgers=[(r['name']+' / '+r['group'],r['key']) for r in self.db.get_account_ledgers()]
        debit=combo(ledgers);credit=combo(ledgers);value=number();notes=QLineEdit();when=date_field()
        location=combo([(r['name'],r['id']) for r in self.db.get_godowns()])
        allocation=combo([('Unallocated / advance',None)]+[(f"Purchase P-{r['id']} / {r['supplier']} / due {amount(r['total_cents']-r['paid_cents'])}",('Purchase',r['id'])) for r in self.db.commerce_report('0001-01-01','9999-12-31')['purchases'] if r['total_cents']>r['paid_cents']]+[(f"Sale {r['bill_no']} / {r['customer'] or 'Walk-in'} / due {amount(cents(r['total'])-r['paid_cents'])}",('Sale',r['id'])) for r in self.db.commerce_report('0001-01-01','9999-12-31')['sales'] if cents(r['total'])>r['paid_cents']])
        def changed():
            k=kind.currentText()
            if k=='Payment':credit.setCurrentIndex(credit.findData('cash'))
            if k=='Receipt':debit.setCurrentIndex(debit.findData('cash'))
            allocated=allocation.currentData() is not None
            debit.setEnabled(not allocated and k!='Receipt');credit.setEnabled(not allocated and k!='Payment')
            location.setEnabled(not allocated)
        kind.currentIndexChanged.connect(changed);allocation.currentIndexChanged.connect(changed);changed()
        def save():
            selected=allocation.currentData()
            if selected:
                doc,ident=selected
                if (doc=='Purchase' and kind.currentText()!='Payment') or (doc=='Sale' and kind.currentText()!='Receipt'):
                    raise ValueError('Use Payment for purchases and Receipt for sales.')
                if doc=='Purchase':self.db.add_purchase_payment(ident,value.value(),'Cash / bank',iso(when))
                else:self.db.add_payment(ident,value.value(),'Cash / bank',iso(when),notes.text())
            else:self.db.save_voucher(kind.currentText(),debit.currentData(),credit.currentData(),value.value(),notes.text(),location.currentData(),iso(when))
        if form_dialog(self,'Post voucher', [('Type',kind),('Date',when),('Invoice allocation',allocation),('Debit ledger',debit),('Credit ledger',credit),('Amount (Rs.)',value),('Godown',location),('Narration',notes)],save):self.refresh()

    def _advance(self):
        advances=self.db.get_advances()
        ident=combo([(f"V-{r['id']} / {r['party']} / available {amount(r['remaining_cents'])}",r['id']) for r in advances])
        data=self.db.commerce_report('0001-01-01','9999-12-31')
        docs=[(f"Purchase P-{r['id']} / {r['supplier']} / due {amount(r['total_cents']-r['paid_cents'])}",('Purchase',r['id'])) for r in data['purchases'] if r['total_cents']>r['paid_cents']]
        docs += [(f"Sale {r['bill_no']} / {r['customer'] or 'Walk-in'} / due {amount(cents(r['total'])-r['paid_cents'])}",('Sale',r['id'])) for r in data['sales'] if cents(r['total'])>r['paid_cents']]
        document=combo(docs);value=number();when=date_field()
        def save():
            if ident.currentData() is None or document.currentData() is None:
                raise ValueError('An available advance and an outstanding invoice are required.')
            kind,doc=document.currentData()
            self.db.apply_advance(ident.currentData(),kind,doc,value.value(),iso(when))
        if form_dialog(self,'Apply existing advance', [('Advance',ident),('Outstanding invoice',document),('Amount (Rs.)',value),('Allocation date',when)],save):self.refresh()

    def _master(self,kind):
        sources={'Godown':self.db.get_godowns,'Unit':self.db.get_stock_units,'Stock group':self.db.get_stock_groups,'Account group':self.db.get_account_groups}
        rows=sources[kind]()
        select=combo([('Create new',None)]+[(r['name'],r['id']) for r in rows]);name=QLineEdit()
        extra=number(True,1000000) if kind=='Unit' else combo([(n,n) for n in ('Asset','Liability','Income','Expense','Equity')]) if kind=='Account group' else QLineEdit()
        if kind=='Unit':extra.setMinimum(1)
        def chosen():
            r=next((r for r in rows if r['id']==select.currentData()),None)
            name.setText(r['name'] if r else '')
            if kind=='Unit':extra.setValue(r['pieces'] if r else 1)
            elif kind=='Account group':extra.setCurrentText(r['nature'] if r else 'Asset')
            elif kind=='Stock group':extra.setText(r['specification'] if r else '')
        select.currentIndexChanged.connect(chosen)
        fields=[('Record',select),('Name',name)]
        if kind!='Godown':fields.append(('Pieces per unit' if kind=='Unit' else 'Nature' if kind=='Account group' else 'Specification',extra))
        def save():
            val=extra.value() if kind=='Unit' else extra.currentText() if kind=='Account group' else extra.text()
            self.db.save_master(kind,name.text(),val,select.currentData())
        if form_dialog(self,kind,fields,save):self.refresh()

    def _inventory(self):
        page=QWidget();layout=QVBoxLayout(page)
        controls=QHBoxLayout();layout.addLayout(controls)
        button('Stock groups',lambda:self._master('Stock group'),controls)
        button('Units',lambda:self._master('Unit'),controls)
        button('Stock categories',self._category,controls)
        button('Add item',self._new_item,controls)
        self.stock_table=table(['ID','Stock item','Category','Stock group','Specification','Unit','Pieces/unit','Total pieces','Stock cost'])
        layout.addWidget(self.stock_table,1)
        actions=QHBoxLayout();layout.addLayout(actions)
        button('Configure selected item',self._configure_item,actions)
        button('Export inventory',lambda:self._export(self.stock_table,'inventory'),actions)
        note('Stock categories and items share the existing Inventory catalog. Rates in New Bill are per piece; purchase units convert into pieces. Changing a unit never changes posted purchases.',layout)
        self.pages.addTab(page,'Inventory')

    def _category(self):
        rows=self.db.get_categories();select=combo([('Create new',None)]+[(r['name'],r['id']) for r in rows]);name=QLineEdit()
        select.currentIndexChanged.connect(lambda:name.setText(select.currentText() if select.currentData() else ''))
        def save():
            if not name.text().strip():raise ValueError('Enter a category name.')
            if select.currentData():self.db.rename_category(select.currentData(),name.text())
            else:self.db.add_category(name.text())
        if form_dialog(self,'Stock category',[('Record',select),('Name',name)],save):self._changed()

    def _new_item(self):
        name=QLineEdit();category=combo([(r['name'],r['id']) for r in self.db.get_categories()]);barcode=QLineEdit();rate=number()
        if form_dialog(self,'Stock item',[('Item name',name),('Category',category),('Barcode (optional)',barcode),('Sale price per piece',rate)],
            lambda:self._save_item(name.text(),category.currentData(),barcode.text(),rate.value())):self._changed()

    def _save_item(self,name,category,barcode,rate):
        if not name.strip():raise ValueError('Enter an item name.')
        return self.db.add_item(name,category,None,barcode,'','',rate,0)

    def _configure_item(self):
        row=self.stock_table.currentRow()
        if row<0:return
        ident=int(self.stock_table.item(row,0).text())
        groups=combo([('No group',None)]+[(r['name'],r['id']) for r in self.db.get_stock_groups()])
        units=combo([(f"{r['name']} = {r['pieces']} pieces",r['id']) for r in self.db.get_stock_units()])
        current=next(r for r in self.db.stock_report() if r['id']==ident)
        index=groups.findText(current['stock_group'] or 'No group')
        groups.setCurrentIndex(max(index,0))
        index=next((i for i in range(units.count()) if units.itemText(i).startswith(current['unit']+' = ')),0)
        units.setCurrentIndex(index)
        if form_dialog(self,'Configure stock item',[('Stock group',groups),('Unit',units)],lambda:self.db.configure_stock_item(ident,groups.currentData(),units.currentData())):self.refresh()

    def _godowns(self):
        page=QWidget();layout=QVBoxLayout(page)
        controls=QHBoxLayout();layout.addLayout(controls)
        self.stock_location=combo();controls.addWidget(self.stock_location,1)
        self.stock_location.currentIndexChanged.connect(lambda:self._guard(self._stock))
        button('Manage godowns',lambda:self._master('Godown'),controls)
        actions=QHBoxLayout();layout.addLayout(actions)
        button('Transfer stock',self._transfer,actions);button('Opening stock / cost',self._opening,actions)
        self.godown_table=table(['Item','Category','Pieces','Value (Rs.)'])
        layout.addWidget(self.godown_table,1)
        note('Main Godown totals all shops. Default shop holds migrated stock and employee sales. Opening costs must be entered before the first transaction for that item and shop. Unknown historical costs stay marked Unknown.',layout)
        self.pages.addTab(page,'Godowns')

    def _stock(self):
        if not hasattr(self,'godown_table'):return
        populate(self.godown_table,[(r['name'],r['category'],r['quantity'],amount(r['value_cents'])) for r in self.db.stock_report(self.stock_location.currentData())])

    def _transfer(self):
        item=combo([(r['name'],r['id']) for r in self.db.get_items()]);locations=[(r['name'],r['id']) for r in self.db.get_godowns()]
        source=combo(locations);destination=combo(locations);qty=number(True);qty.setMinimum(1)
        if form_dialog(self,'Transfer stock',[('Item',item),('From',source),('To',destination),('Quantity in pieces',qty)],
            lambda:self.db.transfer_stock(item.currentData(),source.currentData(),destination.currentData(),qty.value())):self._changed()

    def _opening(self):
        item=combo([(r['name'],r['id']) for r in self.db.get_items()]);location=combo([(r['name'],r['id']) for r in self.db.get_godowns()]);qty=number(True);cost=number()
        if form_dialog(self,'Set opening stock and cost',[('Item',item),('Godown',location),('Total opening pieces',qty),('Purchase cost per piece (excluding GST)',cost)],
            lambda:self.db.set_opening_stock(item.currentData(),location.currentData(),qty.value(),cost.value())):self._changed()

    def _reports(self):
        page=QWidget();layout=QVBoxLayout(page)
        self.period=PeriodFilter();layout.addWidget(self.period)
        controls=QHBoxLayout();layout.addLayout(controls)
        self.report_location=combo();self.channel=combo([('All sales',None),('Retail','Retail'),('Wholesale','Wholesale')])
        controls.addWidget(self.report_location,1);controls.addWidget(self.channel,1)
        self.report_pages=QTabWidget();layout.addWidget(self.report_pages,1)
        self.summary_table=table(['Metric','Amount / count'])
        self.summary_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.report_pages.addTab(self.summary_table,'Master / Profit & loss')
        self.sales_table=table(['ID','Date','Invoice','Customer','Godown','Channel','Net','GST','Total','Paid','Balance','Cost'])
        self.report_pages.addTab(self.sales_table,'Sales')
        self.purchase_table=table(['ID','Date','Invoice','Supplier','Godown','Net','GST','Total','Paid','Balance'])
        self.report_pages.addTab(self.purchase_table,'Purchases')
        self.movement_table=table(['Date','Godown','Item','In (pcs)','Out (pcs)','Reference'])
        self.report_pages.addTab(self.movement_table,'Stock in / out')
        controls=QHBoxLayout();layout.addLayout(controls)
        button('Export current report',lambda:self._export(self.report_pages.currentWidget(),'report'),controls)
        button('Open purchase receipt',self._report_receipt,controls)
        note('Profit excludes GST and deducts item costs and expenses. Unknown costs are flagged. Backdated entries do not revise earlier sale costs. The channel filter applies to sales; paid and balance figures include all payments to date.',layout)
        for signal in (self.period.changed,self.report_location.currentIndexChanged,self.channel.currentIndexChanged):signal.connect(lambda:self._guard(self._report))
        self.pages.addTab(page,'Reports')

    def _report(self):
        start,end=self.period.bounds();r=self.db.commerce_report(start or '0001-01-01',end or '9999-12-31',self.report_location.currentData(),self.channel.currentData())
        summary=[('Net sales (GST excluded)',amount(r['net_sales_cents'])),('Net purchases (GST excluded)',amount(r['purchases_cents'])),('Known cost of sold items',amount(r['cost_cents'])),('Expenses',amount(r['expenses_cents'])),('Journal income/expense adjustments',amount(r['adjustment_cents'])),('Net profit / loss',amount(r['profit_cents']) if not r['missing_cost'] else 'Incomplete — missing historical / opening costs'),('Sale lines with unknown cost',r['missing_cost']),('Stock received / transferred in (pieces)',sum(max(m['quantity'],0) for m in r['movements'])),('Stock sold / transferred out (pieces)',sum(max(-m['quantity'],0) for m in r['movements']))]
        for channel in ('Retail','Wholesale'):
            summary.append((channel+' net sales',amount(sum(money_cents(s['total'])-money_cents(s['gst_amount']) for s in r['sales'] if s['sale_channel']==channel))))
        populate(self.summary_table,summary)
        self.summary_table.resizeRowsToContents()
        populate(self.sales_table,[(s['id'],s['bill_date'],s['bill_no'],s['customer'] or 'Walk-in',s['godown'],s['sale_channel'],amount(money_cents(s['total'])-money_cents(s['gst_amount'])),amount(money_cents(s['gst_amount'])),amount(cents(s['total'])),amount(s['paid_cents']),amount(cents(s['total'])-s['paid_cents']),amount(s['cost_cents']) if not s['missing_cost'] else 'Unknown') for s in r['sales']])
        populate(self.purchase_table,[(p['id'],p['purchase_date'],p['invoice_no'],p['supplier'],p['godown'],amount(p['net_cents']),amount(p['gst_cents']),amount(p['total_cents']),amount(p['paid_cents']),amount(p['total_cents']-p['paid_cents'])) for p in r['purchases']])
        populate(self.movement_table,[(m['movement_date'],m['godown'],m['name'],max(m['quantity'],0),max(-m['quantity'],0),m['reference']) for m in r['movements']])

    def _report_receipt(self):
        row=self.purchase_table.currentRow()
        if row<0:
            QMessageBox.information(self,'Select purchase','Select a row in the Purchases report first.');return
        self._guard(lambda:show_purchase(self.db,int(self.purchase_table.item(row,0).text()),self))

    def _export(self,widget,name):
        export_csv(self,'Export '+name,name+'.csv',[widget.horizontalHeaderItem(i).text() for i in range(widget.columnCount())],
            ([widget.item(r,c).text() if widget.item(r,c) else '' for c in range(widget.columnCount())] for r in range(widget.rowCount())))

    def _changed(self):
        self.refresh()
        if self.on_changed:self.on_changed()

    def refresh(self):
        fill(self.ledger,[('All journal entries',None)]+[(r['name']+' / '+r['group'],r['key']) for r in self.db.get_account_ledgers()])
        locations=[('Main Godown — all shops',None)]+[(r['name'],r['id']) for r in self.db.get_godowns()]
        fill(self.stock_location,locations);fill(self.report_location,locations)
        populate(self.stock_table,[(r['id'],r['name'],r['category'],r['stock_group'],r['specification'],r['unit'],r['unit_pieces'],r['quantity'],amount(r['value_cents'])) for r in self.db.stock_report()])
        self._statement();self._stock();self._report()
