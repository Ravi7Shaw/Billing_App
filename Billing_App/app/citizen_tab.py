"""Simple, fixed Citizen ZPL label form. No canvas or drawing controls."""
import json
from pathlib import Path
import sqlite3
import uuid

from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QTabWidget,
    QLineEdit,QLabel,QCheckBox,QMessageBox,QFileDialog,QInputDialog,QDialog)
from commerce_ui import combo,fill,number,button,note,scroll,table,populate
from label_design import metadata_for
from label_tab import LabelPrinterDialog
from citizen_zpl import document,from_snapshot,ean13,price,COMPANY
from printing import submit_raw
from printer_setup import open_setup


class CitizenLabelTab(QWidget):
    def __init__(self,db,on_changed=None):
        super().__init__();self.db=db;self.on_changed=on_changed;self._loading=False
        layout=QVBoxLayout(self);layout.setContentsMargins(8,6,8,6)
        self.pages=QTabWidget();layout.addWidget(self.pages,1)
        page=QWidget();form=QFormLayout(page);form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        heading=QLabel(COMPANY);heading.setWordWrap(True);form.addRow(heading)
        info=QLabel('Citizen CL-S621II · fixed 4 × 2 inch label · 203 DPI · raw ZPL. Use the printer’s ZPL/Zebra emulation.');info.setWordWrap(True);form.addRow(info)
        self.item=combo();self.item.currentIndexChanged.connect(self.item_changed);form.addRow('Catalog item',self.item)
        self.location=combo();form.addRow('Stock godown',self.location)
        self.category=combo();self.category.currentIndexChanged.connect(self.category_changed)
        self.stock_brand=combo();self.stock_brand.currentIndexChanged.connect(self.brand_changed)
        form.addRow('Stock category',self.category);form.addRow('Stock brand / style',self.stock_brand)
        masters=QHBoxLayout();form.addRow(masters)
        button('Add category',lambda:self.guard(self.add_category),masters);button('Add brand / style',lambda:self.guard(self.add_brand),masters)
        self.brand=QLineEdit();self.product=QLineEdit();self.size=QLineEdit();self.mrp=number();self.selling_price=number();self.barcode=QLineEdit()
        for field in (self.brand,self.product,self.size):field.setMaxLength(20)
        self.barcode.setMaxLength(13);self.barcode.setPlaceholderText('13-digit EAN-13, e.g. 8905310168599')
        for title,field in [('Brand',self.brand),('Product',self.product),('Size',self.size),('MRP (Rs.)',self.mrp),('Selling price (Rs.)',self.selling_price),('Barcode (EAN-13)',self.barcode)]:form.addRow(title,field)
        self.quantity=number(True,10000);self.quantity.setMinimum(1);form.addRow('Number of labels / pieces',self.quantity)
        self.stock_mode=combo([('Reprint labels — no stock change',False),('Add new stock and print',True)]);form.addRow('Stock action',self.stock_mode)
        self.cost_known=QCheckBox('Record purchase cost per piece');self.cost=number();self.cost.setEnabled(False);self.cost_known.toggled.connect(self.cost.setEnabled)
        form.addRow(self.cost_known);form.addRow('Purchase cost per piece (Rs.)',self.cost)
        hint=QLabel('For an existing item, selling price comes from Inventory. Label text and MRP can be entered here. Exporting ZPL does not change stock.');hint.setWordWrap(True);form.addRow(hint)
        self.pages.addTab(scroll(page),'Label details')
        self._build_history()
        actions=QHBoxLayout();layout.addLayout(actions)
        self.setup_button=button('Install / setup',lambda:self.guard(lambda:open_setup(self)),actions)
        self.export_button=button('Export ZPL',lambda:self.guard(self.export_zpl),actions)
        self.print_button=button('Print labels',lambda:self.guard(self.start_print),actions)
        self.refresh()

    def guard(self,action):
        try:return action()
        except (ValueError,TypeError,KeyError,OSError,sqlite3.Error,PermissionError) as exc:
            QMessageBox.warning(self,'Citizen labels',str(exc))

    def _build_history(self):
        page=QWidget();layout=QVBoxLayout(page)
        note('Stock is posted once after spool submission. If output is uncertain, check the printer before resolving/reprinting. Reprints never add stock.',layout)
        self.history=table(['Batch','Created','Item','Godown','Labels','Stock added','State','Notes']);layout.addWidget(self.history,1)
        row=QHBoxLayout();layout.addLayout(row)
        button('Refresh',lambda:self.guard(self.refresh_history),row);button('Reprint',lambda:self.guard(self.reprint),row)
        row=QHBoxLayout();layout.addLayout(row)
        button('Confirm printed / post stock',lambda:self.guard(lambda:self.resolve(True)),row)
        button('Cancel unresolved',lambda:self.guard(lambda:self.resolve(False)),row)
        self.pages.addTab(page,'Print history')

    def refresh(self):
        self.db.get_label_templates()  # admin-only entry point
        self._loading=True
        fill(self.item,[('Create new catalog item',None)]+[(r['name']+' / '+r['category_name'],r['id']) for r in self.db.get_items()])
        fill(self.location,[(r['name'],r['id']) for r in self.db.get_godowns()])
        fill(self.category,[(r['name'],r['id']) for r in self.db.get_categories()])
        self._loading=False;self.category_changed();self.item_changed();self.refresh_history()

    def category_changed(self):
        if self._loading:return
        fill(self.stock_brand,[('No brand / style',None)]+[(r['name'],r['id']) for r in self.db.get_subtypes(self.category.currentData())] if self.category.currentData() else [('No brand / style',None)])
        self.brand_changed()

    def brand_changed(self):
        if not self._loading and hasattr(self,'brand') and self.stock_brand.currentData():self.brand.setText(self.stock_brand.currentText())

    def item_changed(self):
        if self._loading:return
        iid=self.item.currentData();is_new=iid is None
        self.category.setEnabled(is_new);self.stock_brand.setEnabled(is_new);self.selling_price.setEnabled(is_new)
        self.stock_mode.setCurrentIndex(1 if is_new else 0)
        if is_new:return
        row=dict(self.db.get_item_by_id(iid));self.category.setCurrentIndex(self.category.findData(row['category_id']))
        self.category_changed();self.stock_brand.setCurrentIndex(self.stock_brand.findData(row['subtype_id']))
        self.brand.setText(row.get('subtype_name') or '');self.product.setText(row['name']);self.size.setText(row['size'] or '')
        self.selling_price.setValue(row['rate']);self.mrp.setValue(row['rate'])
        code=row.get('barcode') or ''
        try:ean13(code)
        except ValueError:code=''
        self.barcode.setText(code)

    def add_category(self):
        self.db.get_label_templates()
        name,ok=QInputDialog.getText(self,'Category','Category name:')
        if ok and name.strip():
            self.db.add_category(name.strip());self.refresh();self.category.setCurrentIndex(self.category.findText(name.strip()))

    def add_brand(self):
        self.db.get_label_templates()
        if not self.category.currentData():raise ValueError('Choose a category first.')
        name,ok=QInputDialog.getText(self,'Brand / style','Brand / style name:')
        if ok and name.strip():
            self.db.add_subtype(self.category.currentData(),name.strip());self.category_changed();self.stock_brand.setCurrentIndex(self.stock_brand.findText(name.strip()))

    def snapshot(self):
        self.db.get_label_templates()
        iid=self.item.currentData();code=ean13(self.barcode.text())
        if iid is None:
            brand_name=self.brand.text().strip()
            match=next((r for r in self.db.get_subtypes(self.category.currentData()) if r['name'].casefold()==brand_name.casefold()),None)
            item=dict(name=self.product.text().strip(),category_id=self.category.currentData(),subtype_id=match['id'] if match else None,
                      new_subtype_name=None if match else brand_name,category_name=self.category.currentText(),subtype_name=match['name'] if match else brand_name,
                      size=self.size.text().strip(),color='',rate=self.selling_price.value(),barcode=code)
            if not self.stock_mode.currentData():raise ValueError('A new catalog item needs Add new stock and print.')
        else:item=dict(self.db.get_item_by_id(iid))
        metadata=metadata_for(item,self.location.currentText(),dict(label_barcode=code,label_brand=self.brand.text(),label_product=self.product.text(),label_size=self.size.text(),mrp=price(self.mrp.value())))
        layout=document();quantity=self.quantity.value();payload=from_snapshot(layout,metadata,quantity)
        return iid,item,layout,metadata,quantity,payload

    def export_zpl(self):
        *_,payload=self.snapshot()
        path,_=QFileDialog.getSaveFileName(self,'Save Citizen ZPL','citizen-label.zpl','ZPL files (*.zpl)')
        if path:
            self.db.get_label_templates();Path(path).write_bytes(payload)

    def start_print(self,reprint_batch=None):
        self.db.get_label_templates()
        if reprint_batch:
            layout=json.loads(reprint_batch['document']);metadata=json.loads(reprint_batch['metadata']);quantity=reprint_batch['quantity']
            payload=from_snapshot(layout,metadata,quantity);iid=reprint_batch['item_id'];item=None;add_stock=False;cost=None
            location=reprint_batch['godown_id']
        else:
            iid,item,layout,metadata,quantity,payload=self.snapshot()
            add_stock=bool(self.stock_mode.currentData());cost=self.cost.value() if self.cost_known.isChecked() else None;location=self.location.currentData()
        chooser=LabelPrinterDialog(self,raw=True)
        if chooser.exec()!=QDialog.Accepted:return
        queue=chooser.printer.currentText()
        summary=(f'{COMPANY}\nPrinter: {queue}\n4 × 2 inch / 203 DPI / RAW ZPL\n'
                 f'Brand: {metadata["label_brand"]}\nProduct: {metadata["label_product"]}\nSize: {metadata["label_size"]}\n'
                 f'MRP: Rs. {metadata["mrp"]}\nSelling price: Rs. {metadata["rate"]}\nBarcode: {metadata["label_barcode"]}\n'
                 f'Labels: {quantity}\nGodown: {metadata["godown"]}\nStock increase: {quantity if add_stock else 0} pieces')
        for title in ('Confirm label quantity','Final print confirmation'):
            if QMessageBox.question(self,title,summary,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.db.get_label_templates()
        if reprint_batch:batch=self.db.prepare_label_reprint(reprint_batch['id'],uuid.uuid4().hex,quantity,queue)
        else:batch=self.db.prepare_label_batch(uuid.uuid4().hex,iid,item if iid is None else None,location,quantity,add_stock,cost,layout,metadata,queue,203)
        self.db.claim_label_batch(batch)
        try:
            acknowledgement=submit_raw(queue,payload)
            self.db.settle_label_batch(batch,True,acknowledgement+'; check physical paper output.')
        except Exception as exc:
            self.db.mark_label_uncertain(batch,str(exc));self.refresh_history()
            QMessageBox.warning(self,'Check printer before retrying',f'Batch L-{batch}: {exc}\nStock was not confirmed. Check the queue and physical output before retrying.');return
        self.refresh_history();self.stock_mode.setCurrentIndex(0)
        if iid is None:
            saved=self.db.get_label_batch(batch);self.refresh();self.item.setCurrentIndex(self.item.findData(saved['item_id']))
        if self.on_changed:self.on_changed()
        QMessageBox.information(self,'Citizen labels submitted',acknowledgement+f'\nLabels: {quantity}\nStock added: {quantity if add_stock else 0}\nCheck the physical output.')

    def refresh_history(self):
        rows=self.db.get_label_batches()
        populate(self.history,[(r['id'],r['created_at'],r['name'],r['godown'],r['quantity'],r['quantity'] if r['stock_posted'] else 0,r['status'],r['notes']) for r in rows])

    def history_batch(self):
        self.db.get_label_templates();row=self.history.currentRow()
        if row<0:raise ValueError('Select a print batch first.')
        return self.db.get_label_batch(int(self.history.item(row,0).text()))

    def reprint(self):
        batch=self.history_batch()
        from_snapshot(json.loads(batch['document']),json.loads(batch['metadata']),1)
        if batch['status'] not in ('submitted','cancelled'):raise ValueError('Resolve this uncertain job before reprinting.')
        quantity,ok=QInputDialog.getInt(self,'Reprint','Replacement labels (no stock added):',batch['quantity'],1,10000)
        if ok:batch['quantity']=quantity;self.start_print(batch)

    def resolve(self,printed):
        batch=self.history_batch()
        if batch['status'] not in ('prepared','sending','uncertain'):raise ValueError('This batch is already resolved.')
        message=(f'Batch L-{batch["id"]}: {batch["quantity"]} labels.\n'+('Confirm all labels printed. This posts the recorded stock once.' if printed else 'Cancel without stock. Cancel queued printer output too. If only some printed, reconcile the actual stock manually.'))
        if QMessageBox.question(self,'Resolve printer output',message,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.db.settle_label_batch(batch['id'],printed,'Admin checked printer output.');self.refresh_history()
        if self.on_changed:self.on_changed()
