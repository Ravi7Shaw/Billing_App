"""Admin label workbench: item metadata, movable canvas, templates and print history."""
import base64
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import uuid

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QImage
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QTabWidget,
    QGraphicsView, QGraphicsScene, QGraphicsItem, QGraphicsDropShadowEffect, QLabel,
    QComboBox, QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QCheckBox, QPushButton,
    QFontComboBox, QFileDialog, QMessageBox, QInputDialog, QColorDialog, QDialog,
    QDialogButtonBox, QSplitter, QFrame)
from PySide6.QtPrintSupport import QPrinter

from commerce_ui import combo, fill, number, button, note, table, populate, scroll, form_dialog
from label_design import (PX_PER_MM, default_design, metadata_design, validate_design, draw_element, expand,
    metadata_for, custom_metadata, configure_label_printer, print_labels, calibration_design)
from printing import printing_backend, available_printers, printer_setup_help, printer_setup_hint, check_cups_queue, submit_pdf, queue_status
from window_utils import fit_window


class LabelElement(QGraphicsItem):
    def __init__(self, owner, data):
        super().__init__()
        self.owner = owner
        self.data = data
        self.resizing = False
        self.setFlags(QGraphicsItem.ItemIsSelectable | QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemSendsGeometryChanges)
        self.setPos((data['x']+owner.offset_x.value())*PX_PER_MM, (data['y']+owner.offset_y.value())*PX_PER_MM)
        self.setRotation(data.get('rotation', 0))
        self.setToolTip('Double-click to edit text. Drag to move. Drag the lower-right handle to resize. Use properties for exact mm.')

    def boundingRect(self):
        return QRectF(0,0,self.data['w']*PX_PER_MM,self.data['h']*PX_PER_MM)

    def paint(self, painter, option, widget=None):
        try:
            # Use actual printer-dot geometry before scaling to the on-screen paper.
            # Rounding modules at screen DPI would give a different barcode width.
            scale=self.owner.dpi.currentData()/25.4
            painter.save()
            try:
                painter.scale(PX_PER_MM/scale,PX_PER_MM/scale)
                draw_element(painter,self.data,self.owner.context,scale)
            finally:
                painter.restore()
        except (ValueError,KeyError,TypeError) as exc:
            painter.setPen(Qt.red)
            painter.drawText(self.boundingRect(),Qt.TextWordWrap,str(exc))
        if self.isSelected():
            painter.setPen(QPen(QColor('#2f6f4f'),1,Qt.DashLine))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(self.boundingRect())
            painter.fillRect(QRectF(self.boundingRect().right()-6,self.boundingRect().bottom()-6,6,6),QColor('#2f6f4f'))

    def mousePressEvent(self,event):
        self.owner.remember()
        rect=self.boundingRect()
        self.resizing=abs(event.pos().x()-rect.right())<10 and abs(event.pos().y()-rect.bottom())<10
        if self.resizing:
            self.setSelected(True);event.accept()
        else:super().mousePressEvent(event)

    def mouseDoubleClickEvent(self,event):
        if self.data['kind'] in ('text','barcode','qr','rectangle','ellipse','triangle','line'):
            self.owner.scene.clearSelection()
            self.setSelected(True)
            self.owner.guard(self.owner.edit_selected)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self,event):
        if self.resizing:
            self.prepareGeometryChange()
            self.data['w']=max(1,round(event.pos().x()/PX_PER_MM,1))
            self.data['h']=max(1,round(event.pos().y()/PX_PER_MM,1))
            self.update()
        else:super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if not self.resizing:super().mouseReleaseEvent(event)
        self.data['x']=round(self.pos().x()/PX_PER_MM-self.owner.offset_x.value(),1)
        self.data['y']=round(self.pos().y()/PX_PER_MM-self.owner.offset_y.value(),1)
        self.setPos((self.data['x']+self.owner.offset_x.value())*PX_PER_MM,(self.data['y']+self.owner.offset_y.value())*PX_PER_MM)
        self.resizing=False
        self.owner.selected()
        self.owner.check_preview()


class PaperView(QGraphicsView):
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(),Qt.KeepAspectRatio)


class LabelPrinterDialog(QDialog):
    def __init__(self,parent):
        super().__init__(parent)
        self.setWindowTitle('Select label printer')
        layout=QVBoxLayout(self)
        self.transport=printing_backend()
        self.status=note('',layout)
        self.printer=combo();layout.addWidget(self.printer)
        actions=QHBoxLayout();layout.addLayout(actions)
        button('Refresh printers',self.refresh_printers,actions)
        button('Setup / help',lambda:QMessageBox.information(self,'Printer setup',printer_setup_help()),actions)
        button('Queue status',self.show_queue_status,actions)
        self.buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText('Continue')
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.refresh_printers()
        fit_window(self,480,280)

    def show_queue_status(self):
        try:message=queue_status(self.printer.currentText())
        except ValueError as exc:message=str(exc)
        QMessageBox.information(self,'Printer queue',message)

    def refresh_printers(self):
        previous=self.printer.currentText();self.transport=printing_backend()
        try:
            names=available_printers();message='Choose the installed label printer. Paper size is taken from this design; scaling is 100%.'
        except ValueError as exc:
            names=[];message=str(exc)
        self.printer.clear();self.printer.addItems(names)
        index=self.printer.findText(previous)
        if index<0:index=next((i for i,n in enumerate(names) if 'citizen' in n.lower()),0)
        self.printer.setCurrentIndex(index)
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(bool(names))
        self.status.setText(message if names else message.splitlines()[0]+'\n'+printer_setup_hint())



class LabelTab(QWidget):
    def __init__(self,db,on_changed=None):
        super().__init__()
        self.db=db;self.on_changed=on_changed
        self.document=default_design();self.context={};self.undo_stack=[];self.redo_stack=[]
        self._loading=False;self.new_code='CS-'+uuid.uuid4().hex[:10].upper()
        layout=QVBoxLayout(self);layout.setContentsMargins(8,6,8,6)
        self.pages=QTabWidget();layout.addWidget(self.pages,1)
        self._build_item();self._build_designer();self._build_history()
        footer=QHBoxLayout();layout.addLayout(footer)
        self.pdf_button=button('PDF preview',lambda:self.guard(self.export_pdf),footer)
        self.calibration_button=button('Calibration',lambda:self.guard(lambda:self.start_print(True)),footer)
        self.print_button=button('Print labels',lambda:self.guard(self.start_print),footer)
        self.refresh();self.rebuild()

    def guard(self,action):
        try:return action()
        except (ValueError,TypeError,KeyError,sqlite3.Error,PermissionError,OSError) as exc:
            QMessageBox.warning(self,'Label action not completed',str(exc))

    def _build_item(self):
        page=QWidget();form=QFormLayout(page);form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.item=combo();self.item.currentIndexChanged.connect(self.item_changed)
        self.location=combo();self.location.currentIndexChanged.connect(self.refresh_preview)
        self.stock_mode=combo([('Reprint labels — no stock change',False),('Add new stock and print',True)])
        self.quantity=number(True,10000);self.quantity.setMinimum(1)
        self.cost_known=QCheckBox('Record purchase cost per piece');self.cost=number()
        self.cost.setEnabled(False);self.cost_known.toggled.connect(self.cost.setEnabled)
        for label,field in [('Catalog item',self.item),('Stock godown',self.location),('Mode',self.stock_mode),('Number of labels / new pieces',self.quantity),('',self.cost_known),('Cost per piece (Rs., excluding GST)',self.cost)]:form.addRow(label,field)
        self.new_fields=QWidget();new_form=QFormLayout(self.new_fields);new_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.name=QLineEdit();self.category=combo();self.brand=combo();self.size=QLineEdit();self.color=QLineEdit();self.rate=number();self.code=QLineEdit(self.new_code)
        self.category.currentIndexChanged.connect(self.category_changed)
        for label,field in [('New item name',self.name),('Category',self.category),('Brand / style',self.brand),('Size',self.size),('Colour',self.color),('Selling price per piece',self.rate),('Catalog barcode',self.code)]:new_form.addRow(label,field)
        master_buttons=QHBoxLayout();new_form.addRow(master_buttons)
        button('Add category',lambda:self.guard(self.new_category),master_buttons)
        button('Add brand/style',lambda:self.guard(self.new_brand),master_buttons)
        form.addRow(self.new_fields)
        self.extra=QPlainTextEdit();self.extra.setPlaceholderText('Custom metadata, one field=value per line\nmaterial=Denim\ncollection=Summer');self.extra.setMaximumHeight(110)
        form.addRow('Additional metadata',self.extra)
        self.fields=QLabel();self.fields.setWordWrap(True);form.addRow('Available label fields',self.fields)
        self.item_summary=QLabel();self.item_summary.setWordWrap(True);form.addRow(self.item_summary)
        for field in (self.name,self.size,self.color,self.code):field.textChanged.connect(self.refresh_preview)
        self.rate.valueChanged.connect(self.refresh_preview);self.brand.currentIndexChanged.connect(self.refresh_preview)
        self.extra.textChanged.connect(self.refresh_preview)
        self.pages.addTab(scroll(page),'Item / Stock')

    def _build_designer(self):
        designer=QWidget();layout=QVBoxLayout(designer);layout.setContentsMargins(0,0,0,0)
        sizing=QHBoxLayout();layout.addLayout(sizing)
        self.width=number(maximum=500);self.width.setMinimum(10);self.width.setValue(100)
        self.height=number(maximum=500);self.height.setMinimum(10);self.height.setValue(50)
        for text,field in [('Width mm',self.width),('Height mm',self.height)]:sizing.addWidget(QLabel(text));sizing.addWidget(field)
        self.dpi=combo([('203 DPI',203),('300 DPI',300),('600 DPI',600)]);sizing.addWidget(self.dpi)
        self.width.editingFinished.connect(self.resize_paper);self.height.editingFinished.connect(self.resize_paper)
        self.dpi.currentIndexChanged.connect(self.check_preview)
        self.editor_tabs=QTabWidget();layout.addWidget(self.editor_tabs,1)
        canvas=QWidget();canvas_layout=QVBoxLayout(canvas);canvas_layout.setContentsMargins(0,0,0,0)
        tools=QHBoxLayout();canvas_layout.addLayout(tools)
        self.kind=combo([(s.title(),s) for s in ('text','barcode','qr','rectangle','ellipse','triangle','line','image')]);tools.addWidget(self.kind)
        button('Add',lambda:self.guard(self.add_element),tools)
        self.edit_text_button=button('Edit / style',lambda:self.guard(self.edit_selected),tools)
        button('Undo',self.undo,tools);button('Redo',self.redo,tools)
        self.scene=QGraphicsScene(self);self.view=PaperView(self.scene)
        self.view.setBackgroundBrush(QColor('#dce2e6'))
        self.view.setRenderHint(QPainter.Antialiasing,True)
        self.view.setDragMode(QGraphicsView.RubberBandDrag)
        canvas_layout.addWidget(self.view,1)
        self.scene.selectionChanged.connect(self.selected)
        self.preview_status=note('',canvas_layout)
        self.view.setToolTip('Double-click text on the label to type, or select it and click Edit / style.')
        self.editor_tabs.addTab(canvas,'Live paper preview')
        props=QWidget();form=QFormLayout(props);form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.text=QPlainTextEdit();self.text.setPlaceholderText('Type your label text here. Click Apply properties to save.');self.text.setMaximumHeight(90);form.addRow('Text / barcode data template',self.text)
        self.font=QFontComboBox();form.addRow('Font (installed fonts)',self.font)
        self.font_size=number(maximum=150);self.font_size.setMinimum(1);self.font_size.setValue(10);form.addRow('Font size (pt)',self.font_size)
        self.bold=QCheckBox('Bold');self.italic=QCheckBox('Italic');self.underline=QCheckBox('Underline')
        font_styles=QHBoxLayout()
        for field in (self.bold,self.italic,self.underline):font_styles.addWidget(field)
        form.addRow(font_styles)
        self.align=combo([(s,s) for s in ('left','center','right')]);form.addRow('Alignment',self.align)
        self.shape=combo([(s.title(),s) for s in ('rectangle','ellipse','triangle','line')]);form.addRow('Shape',self.shape)
        self.coords={}
        for key,title in [('x','X mm'),('y','Y mm'),('w','Width mm'),('h','Height mm'),('rotation','Rotation °')]:
            field=number(maximum=500 if key!='rotation' else 359);field.setMinimum(-500 if key in ('x','y') else 0)
            self.coords[key]=field;form.addRow(title,field)
        self.ink='#000000';self.fill_color='#ffffff';self.filled=QCheckBox('Fill shape');form.addRow(self.filled)
        colors=QHBoxLayout();form.addRow(colors)
        button('Text / border colour',lambda:self.choose_color(False),colors)
        button('Fill colour',lambda:self.choose_color(True),colors)
        actions=QHBoxLayout();form.addRow(actions)
        button('Apply properties',lambda:self.guard(self.apply_properties),actions)
        button('Duplicate',self.duplicate,actions);button('Delete',self.delete,actions)
        layer=QHBoxLayout();form.addRow(layer)
        button('Bring forward',lambda:self.layer(1),layer);button('Send backward',lambda:self.layer(-1),layer)
        self.editor_tabs.addTab(scroll(props),'Selected element properties')
        templates=QWidget();form=QFormLayout(templates);form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        self.preset=combo([('Code 128 — compact item code','barcode'),('QR — full metadata string','qr')])
        form.addRow('100 × 50 mm starting layouts',self.preset)
        preset_button=QPushButton('Use starting layout');preset_button.clicked.connect(self.use_preset);form.addRow(preset_button)
        self.template=combo();form.addRow('Saved template',self.template)
        row=QHBoxLayout();form.addRow(row)
        button('Load',lambda:self.guard(self.load_template),row);button('Save as…',lambda:self.guard(self.save_template),row)
        row=QHBoxLayout();form.addRow(row)
        button('Import JSON',lambda:self.guard(self.import_template),row);button('Export JSON',lambda:self.guard(self.export_template),row)
        self.offset_x=number(maximum=20);self.offset_x.setMinimum(-20)
        self.offset_y=number(maximum=20);self.offset_y.setMinimum(-20)
        form.addRow('Printer X correction (mm)',self.offset_x);form.addRow('Printer Y correction (mm)',self.offset_y)
        label=QLabel('Print a calibration label and measure its 20 mm square. Use offsets only for position errors. Set the driver to the same paper dimensions, 100% scale, correct DPI and label gap. A driver/spool success does not prove that paper printed.');label.setWordWrap(True);form.addRow(label)
        for signal in (self.dpi.currentIndexChanged,self.offset_x.valueChanged,self.offset_y.valueChanged):
            signal.connect(self.settings_changed)
        self.editor_tabs.addTab(scroll(templates),'Templates / Calibration')
        self.pages.addTab(designer,'Design label')

    def _build_history(self):
        page=QWidget();layout=QVBoxLayout(page)
        note('A failed or interrupted job may have printed some labels. Check the printer before resolving it. Stock is added once for a successful new-stock batch. Reprints and PDF previews do not add stock.',layout)
        self.history=table(['Batch','Created','Item','Godown','Labels','Stock added','State','Notes']);layout.addWidget(self.history,1)
        row=QHBoxLayout();layout.addLayout(row)
        button('Refresh',lambda:self.guard(self.refresh_history),row)
        button('Reprint saved batch',lambda:self.guard(self.reprint),row)
        row=QHBoxLayout();layout.addLayout(row)
        button('Confirm all printed / post stock',lambda:self.guard(lambda:self.resolve(True)),row)
        button('Cancel unresolved batch',lambda:self.guard(lambda:self.resolve(False)),row)
        self.pages.addTab(page,'Print history')

    def refresh(self):
        selected=self.item.currentData()
        fill(self.item,[('Create new catalog item',None)]+[(r['name']+' / '+r['category_name'],r['id']) for r in self.db.get_items()])
        fill(self.location,[(r['name'],r['id']) for r in self.db.get_godowns()])
        fill(self.category,[(r['name'],r['id']) for r in self.db.get_categories()])
        self.category_changed();self.item_changed()
        fill(self.template,[(r['name'],r['id']) for r in self.db.get_label_templates()])
        self.refresh_history()

    def refresh_history(self):
        rows=self.db.get_label_batches()
        populate(self.history,[(r['id'],r['created_at'],r['name'],r['godown'],r['quantity'],r['quantity'] if r['stock_posted'] else 0,r['status'],r['notes']) for r in rows])

    def item_changed(self):
        is_new=self.item.currentData() is None
        self.new_fields.setVisible(is_new)
        if is_new:self.stock_mode.setCurrentIndex(1)
        else:self.stock_mode.setCurrentIndex(0)
        self.refresh_preview()

    def category_changed(self):
        fill(self.brand,[('No brand/style',None)]+[(r['name'],r['id']) for r in self.db.get_subtypes(self.category.currentData())] if self.category.currentData() else [('No brand/style',None)])
        self.refresh_preview()

    def new_category(self):
        name,ok=QInputDialog.getText(self,'Add category','Category name:')
        if ok and name.strip():
            self.db.add_category(name);self.refresh();self.category.setCurrentIndex(self.category.findText(name.strip()))

    def new_brand(self):
        if not self.category.currentData():raise ValueError('Choose a category first.')
        name,ok=QInputDialog.getText(self,'Add brand/style','Brand or style name:')
        if ok and name.strip():
            self.db.add_subtype(self.category.currentData(),name);self.category_changed();self.brand.setCurrentIndex(self.brand.findText(name.strip()))

    def current_data(self):
        iid=self.item.currentData()
        if iid is None:
            item=dict(name=self.name.text().strip(),category_id=self.category.currentData(),subtype_id=self.brand.currentData(),
                category_name=self.category.currentText(),subtype_name=self.brand.currentText() if self.brand.currentData() else '',
                size=self.size.text().strip(),color=self.color.text().strip(),rate=self.rate.value(),barcode=self.code.text().strip())
        else:
            row=self.db.get_item_by_id(iid)
            if not row:raise ValueError('Catalog item no longer exists.')
            item=dict(row)
            if not item.get('barcode'):item['barcode']=f'CS-{iid:010d}'
        context=metadata_for(item,self.location.currentText(),custom_metadata(self.extra.toPlainText()))
        return iid,item,context

    def refresh_preview(self):
        if not hasattr(self,'scene'):return
        try:
            iid,item,self.context=self.current_data()
            self.fields.setText(', '.join('{'+k+'}' for k in self.context)+'\nUse {metadata} in a QR to encode the full metadata string; Code 128 is for short codes. Use {{ and }} for literal braces.')
            self.item_summary.setText(f"{item['name'] or 'New item'} · {self.location.currentText()} · Rs. {float(item['rate']):.2f}/piece")
        except (ValueError,PermissionError) as exc:
            self.preview_status.setText(str(exc));return
        self.scene.update();self.check_preview()

    def check_preview(self):
        if not hasattr(self,'preview_status'):return
        try:
            payloads=validate_design(self.document,self.context,self.dpi.currentData(),(self.offset_x.value(),self.offset_y.value()))
            self.preview_status.setText(f"{self.document['width']:g} × {self.document['height']:g} mm · actual-size print · "+ ('barcode ready' if payloads else 'add a barcode or QR'))
        except (ValueError,KeyError) as exc:self.preview_status.setText('Check layout: '+str(exc))

    def remember(self):
        self.undo_stack.append(deepcopy(self.document));self.undo_stack=self.undo_stack[-40:];self.redo_stack.clear()

    def undo(self):
        if self.undo_stack:
            self.redo_stack.append(deepcopy(self.document));self.document=self.undo_stack.pop();self.rebuild()

    def redo(self):
        if self.redo_stack:
            self.undo_stack.append(deepcopy(self.document));self.document=self.redo_stack.pop();self.rebuild()

    def settings_changed(self):
        if self._loading:return
        self.document['settings']=dict(dpi=self.dpi.currentData(),offset_x=self.offset_x.value(),offset_y=self.offset_y.value())
        for item in self.scene.items():
            if isinstance(item,LabelElement):
                item.setPos((item.data['x']+self.offset_x.value())*PX_PER_MM,(item.data['y']+self.offset_y.value())*PX_PER_MM)
        self.scene.update()
        self.check_preview()

    def rebuild(self,select_index=None):
        self._loading=True;self.scene.clear()
        self.width.setValue(self.document['width']);self.height.setValue(self.document['height'])
        settings=self.document.get('settings',{})
        self.dpi.setCurrentIndex(self.dpi.findData(settings.get('dpi',203)))
        self.offset_x.setValue(settings.get('offset_x',0));self.offset_y.setValue(settings.get('offset_y',0))
        w=self.document['width']*PX_PER_MM;h=self.document['height']*PX_PER_MM
        self.scene.setSceneRect(-12,-12,w+24,h+24)
        shadow=self.scene.addRect(QRectF(4,4,w,h),QPen(Qt.NoPen),QBrush(QColor('#a4adb4')));shadow.setZValue(-2)
        paper=self.scene.addRect(QRectF(0,0,w,h),QPen(QColor('#707b83'),.5),QBrush(Qt.white));paper.setZValue(-1)
        for i,e in enumerate(self.document['elements']):
            item=LabelElement(self,e);item.setZValue(i);self.scene.addItem(item)
            if i==select_index:item.setSelected(True)
        self.view.fitInView(self.scene.sceneRect(),Qt.KeepAspectRatio)
        self._loading=False;self.refresh_preview();self.selected()

    def selected_item(self):
        return next((r for r in self.scene.selectedItems() if isinstance(r,LabelElement)),None)

    def selected(self):
        if self._loading:return
        item=self.selected_item()
        if not item:return
        e=item.data;self.shape.setEnabled(e['kind'] in ('rectangle','ellipse','triangle','line'))
        self.shape.setCurrentIndex(self.shape.findData(e['kind']))
        self.text.setPlainText(e.get('text',''));self.font.setCurrentFont(QFont(e.get('font','Arial')))
        self.font_size.setValue(e.get('size',10))
        for key,field in self.coords.items():field.setValue(e.get(key,0))
        for field,key in [(self.bold,'bold'),(self.italic,'italic'),(self.underline,'underline'),(self.filled,'filled')]:field.setChecked(bool(e.get(key)))
        self.align.setCurrentText(e.get('align','left'));self.ink=e.get('color','#000000');self.fill_color=e.get('fill','#ffffff')

    def resize_paper(self):
        if self._loading:return
        self.remember();self.document['width']=self.width.value();self.document['height']=self.height.value();self.rebuild()

    def add_element(self):
        kind=self.kind.currentData();e=dict(kind=kind,x=4,y=4,w=min(50,self.document['width']-8),h=min(12,self.document['height']-8),text='Text',font='Arial',size=10)
        if kind in ('barcode','qr'):e.update(text='{metadata}' if kind=='qr' else '{barcode}',w=self.document['width']-8,h=min(40 if kind=='qr' else 20,self.document['height']-8))
        if kind=='image':
            path,_=QFileDialog.getOpenFileName(self,'Choose logo','','Images (*.png *.jpg *.jpeg *.bmp)')
            if not path:return
            if Path(path).stat().st_size>3_000_000:raise ValueError('Choose an image up to 3 MB.')
            data=Path(path).read_bytes()
            if len(data)>3_000_000 or QImage.fromData(data).isNull():raise ValueError('Choose an image up to 3 MB.')
            e['image']=base64.b64encode(data).decode();e['text']=''
        self.remember();self.document['elements'].append(e);self.rebuild(len(self.document['elements'])-1)
        if kind=='text':self.edit_text()
        elif kind in ('rectangle','ellipse','triangle','line'):self.edit_selected()

    def edit_selected(self):
        item=self.selected_item()
        if not item:raise ValueError('Select a box on the paper first, or choose an element and click Add.')
        if item.data['kind'] in ('text','barcode','qr'):self.edit_text()
        else:
            self.selected();self.editor_tabs.setCurrentIndex(1)

    def edit_text(self):
        item=self.selected_item()
        if not item or item.data['kind'] not in ('text','barcode','qr'):
            raise ValueError('Select a text, barcode or QR box on the label first, or add a Text box.')
        index=next(i for i,e in enumerate(self.document['elements']) if e is item.data)
        dialog=QDialog(self);dialog.setWindowTitle('Edit label text and style')
        layout=QVBoxLayout(dialog)
        note('Type your text below. Use multiple lines if needed. Fields such as {name} and {rate} insert item details.',layout)
        editor=QPlainTextEdit(item.data.get('text',''));editor.setObjectName('labelTextEditor')
        editor.setPlaceholderText('Type label text…');editor.setMinimumHeight(60);editor.setMaximumHeight(110);layout.addWidget(editor)
        style_panel=QWidget();style_form=QFormLayout(style_panel)
        style_form.setRowWrapPolicy(QFormLayout.WrapLongRows)
        font=QFontComboBox();font.setObjectName('labelTextFont');font.setCurrentFont(QFont(item.data.get('font','Arial')))
        size=number(maximum=150);size.setMinimum(1);size.setValue(item.data.get('size',10));size.setObjectName('labelTextSize')
        style_form.addRow('Font',font);style_form.addRow('Font size (pt)',size)
        styles={}
        row=QHBoxLayout()
        for key in ('bold','italic','underline'):
            field=QCheckBox(key.title());field.setObjectName('labelText'+key.title());field.setChecked(bool(item.data.get(key)));styles[key]=field;row.addWidget(field)
        style_form.addRow(row)
        alignment=combo([(v.title(),v) for v in ('left','center','right')]);alignment.setCurrentIndex(alignment.findData(item.data.get('align','left')))
        style_form.addRow('Alignment',alignment)
        dimensions={}
        for key,title in (('w','Box width (mm)'),('h','Box height (mm)')):
            field=number(maximum=500);field.setMinimum(.1);field.setValue(item.data[key]);field.setObjectName('labelText'+key.upper());dimensions[key]=field;style_form.addRow(title,field)
        style_panel.setEnabled(item.data['kind']=='text')
        layout.addWidget(scroll(style_panel),1)
        buttons=QDialogButtonBox(QDialogButtonBox.Save|QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);layout.addWidget(buttons)
        fit_window(dialog,540,560)
        editor.selectAll();editor.setFocus()
        if dialog.exec()!=QDialog.Accepted:return
        self.db.get_label_templates()  # Recheck admin access after the modal editor.
        updated=deepcopy(item.data);updated['text']=editor.toPlainText()
        if item.data['kind']=='text':
            updated.update(font=font.currentFont().family(),size=size.value(),align=alignment.currentData())
            updated.update({key:field.isChecked() for key,field in styles.items()})
            updated.update({key:field.value() for key,field in dimensions.items()})
        if updated!=item.data:
            self.remember();item.data.update(updated);self.rebuild(index)

    def apply_properties(self):
        item=self.selected_item()
        if not item:raise ValueError('Select an element on the paper first.')
        index=next(i for i,e in enumerate(self.document['elements']) if e is item.data);self.remember()
        if item.data['kind'] in ('rectangle','ellipse','triangle','line'):item.data['kind']=self.shape.currentData()
        item.data.update({key:field.value() for key,field in self.coords.items()})
        item.data.update(text=self.text.toPlainText(),font=self.font.currentFont().family(),size=self.font_size.value(),bold=self.bold.isChecked(),italic=self.italic.isChecked(),underline=self.underline.isChecked(),align=self.align.currentText(),color=self.ink,fill=self.fill_color,filled=self.filled.isChecked())
        self.rebuild(index)

    def choose_color(self,fill_color):
        color=QColorDialog.getColor(QColor(self.fill_color if fill_color else self.ink),self)
        if color.isValid():
            if fill_color:self.fill_color=color.name()
            else:self.ink=color.name()

    def delete(self):
        item=self.selected_item()
        if item:
            self.remember();index=next(i for i,e in enumerate(self.document['elements']) if e is item.data);self.document['elements'].pop(index);self.rebuild()

    def duplicate(self):
        item=self.selected_item()
        if item:
            self.remember();copy=deepcopy(item.data);copy['x']+=2;copy['y']+=2;self.document['elements'].append(copy);self.rebuild(len(self.document['elements'])-1)

    def layer(self,direction):
        item=self.selected_item()
        if not item:return
        index=next(i for i,e in enumerate(self.document['elements']) if e is item.data);new=max(0,min(len(self.document['elements'])-1,index+direction))
        self.remember();self.document['elements'].insert(new,self.document['elements'].pop(index));self.rebuild(new)

    def use_preset(self):
        self.remember()
        self.document=metadata_design() if self.preset.currentData()=='qr' else default_design()
        self.rebuild()

    def load_template(self):
        row=next((r for r in self.db.get_label_templates() if r['id']==self.template.currentData()),None)
        if row:self.remember();self.document=json.loads(row['document']);self.rebuild()

    def save_template(self):
        name,ok=QInputDialog.getText(self,'Save template','Template name (same name replaces it):',text=self.template.currentText())
        if ok:
            self.db.save_label_template(name,self.document)
            fill(self.template,[(r['name'],r['id']) for r in self.db.get_label_templates()])

    def import_template(self):
        path,_=QFileDialog.getOpenFileName(self,'Import label template','','JSON (*.json)')
        if not path:return
        if Path(path).stat().st_size>5_000_000:raise ValueError('Template exceeds 5 MB.')
        document=json.loads(Path(path).read_text(encoding='utf-8'));validate_design(document)
        self.remember();self.document=document;self.rebuild()

    def export_template(self):
        validate_design(self.document)
        path,_=QFileDialog.getSaveFileName(self,'Export label template','label.json','JSON (*.json)')
        if path:Path(path if path.lower().endswith('.json') else path+'.json').write_text(json.dumps(self.document,indent=2),encoding='utf-8')

    def export_pdf(self):
        _,_,metadata=self.current_data();validate_design(self.document,metadata,self.dpi.currentData())
        path,_=QFileDialog.getSaveFileName(self,'Label PDF preview (no stock change)','label-preview.pdf','PDF (*.pdf)')
        if not path:return
        path=path if path.lower().endswith('.pdf') else path+'.pdf'
        printer=QPrinter(QPrinter.HighResolution);printer.setOutputFormat(QPrinter.PdfFormat);printer.setOutputFileName(path)
        configure_label_printer(printer,self.document,self.dpi.currentData());print_labels(printer,self.document,metadata,1)
        if not Path(path).read_bytes().startswith(b'%PDF'):raise ValueError('PDF was not generated.')
        QMessageBox.information(self,'Preview saved','Saved one label at its physical page size. PDF preview does not update stock.')

    def confirm_twice(self,summary):
        for title,prompt in [('Confirm label quantity','Check quantity, data, paper size and stock change.'),('Final print confirmation','Send exactly this batch to the printer?')]:
            if QMessageBox.question(self,title,prompt+'\n\n'+summary,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return False
        return True

    def start_print(self,calibration=False,reprint_batch=None):
        self.db.get_label_templates()  # Recheck admin access even in a stale callback.
        if reprint_batch:
            document=json.loads(reprint_batch['document']);metadata=json.loads(reprint_batch['metadata'])
            iid=reprint_batch['item_id'];new_item=None;quantity=reprint_batch['quantity'];add_stock=False;cost=None
            location=reprint_batch['godown_id'];dpi=reprint_batch['dpi'];offsets=(reprint_batch['offset_x'],reprint_batch['offset_y'])
        else:
            iid,item,metadata=self.current_data();new_item=item if iid is None else None
            document=calibration_design(self.width.value(),self.height.value()) if calibration else deepcopy(self.document)
            quantity=1 if calibration else self.quantity.value();add_stock=False if calibration else bool(self.stock_mode.currentData())
            cost=self.cost.value() if self.cost_known.isChecked() else None
            location=self.location.currentData();dpi=self.dpi.currentData();offsets=(self.offset_x.value(),self.offset_y.value())
        validate_design(document,metadata,dpi,offsets)
        if not calibration and not any(e['kind'] in ('barcode','qr') for e in document['elements']):raise ValueError('Add a barcode or QR to the label.')
        dialog=LabelPrinterDialog(self)
        if dialog.exec()!=QDialog.Accepted:return
        queue=dialog.printer.currentText();transport=getattr(dialog,'transport','qt')
        if transport=='cups':check_cups_queue(queue)
        printer=QPrinter(QPrinter.HighResolution)
        if transport=='cups':printer.setOutputFormat(QPrinter.PdfFormat)
        else:printer.setPrinterName(queue)
        actual_dpi=configure_label_printer(printer,document,dpi)
        validate_design(document,metadata,actual_dpi,offsets)
        summary=(f"Printer: {queue}\nPaper: {document['width']:g} × {document['height']:g} mm at {actual_dpi} DPI\n"
            f"Labels: {quantity}\nItem: {metadata.get('name','Calibration')}\nPrice per piece: Rs. {metadata.get('rate','0.00')}\n"
            f"Godown: {metadata.get('godown','')}\nStock increase: {quantity if add_stock else 0} pieces\n"
            f"Stock cost per piece: {'Unknown' if cost is None else f'Rs. {cost:.2f}'}\nOffsets: {offsets[0]:g}, {offsets[1]:g} mm")
        if not self.confirm_twice(summary):return
        self.db.get_label_templates()  # logout may have occurred during a modal prompt
        def send_output():
            if transport!='cups':
                print_labels(printer,document,metadata,quantity,offsets)
                return 'Submitted through the native printer driver.'
            with tempfile.TemporaryDirectory(prefix='cloth-labels-') as folder:
                path=Path(folder)/'labels.pdf';printer.setOutputFileName(str(path))
                print_labels(printer,document,metadata,quantity,offsets)
                job=submit_pdf(path,queue,document['width'],document['height'],actual_dpi,title='Cloth Shop Labels')
            return 'CUPS job '+job
        if calibration:
            acknowledgement=send_output()
            QMessageBox.information(self,'Calibration submitted',acknowledgement+'\nMeasure the square: it must be 20 × 20 mm. Correct driver paper size/scaling before printing stock labels. No stock changed.');return
        if reprint_batch:
            batch_id=self.db.prepare_label_reprint(reprint_batch['id'],uuid.uuid4().hex,quantity,queue)
        else:
            batch_id=self.db.prepare_label_batch(uuid.uuid4().hex,iid,new_item,location,quantity,add_stock,cost,document,metadata,queue,actual_dpi,offsets)
        self.db.claim_label_batch(batch_id)
        try:
            acknowledgement=send_output()
            self.db.settle_label_batch(batch_id,True,acknowledgement+'; physical output must be checked at printer.')
        except Exception as exc:
            self.db.mark_label_uncertain(batch_id,str(exc))
            self.refresh_history()
            QMessageBox.warning(self,'Check printer before retrying',f'Batch L-{batch_id}: {exc}\nStock was not confirmed. Check Print history and the actual output. Do not blindly send the job again.');return
        self.refresh_history()
        if iid is None:
            saved=self.db.get_label_batch(batch_id)
            self.refresh();self.item.setCurrentIndex(self.item.findData(saved['item_id']))
            self.new_code='CS-'+uuid.uuid4().hex[:10].upper();self.code.setText(self.new_code)
        self.stock_mode.setCurrentIndex(0)
        if self.on_changed:self.on_changed()
        QMessageBox.information(self,'Labels submitted',f'{acknowledgement}\nBatch L-{batch_id}: {quantity} labels sent. Stock added: {quantity if add_stock else 0}. Check physical output. Reprint through History if needed; it will not add stock again.')

    def history_batch(self):
        row=self.history.currentRow()
        if row<0:raise ValueError('Select a print batch first.')
        return self.db.get_label_batch(int(self.history.item(row,0).text()))

    def reprint(self):
        batch=self.history_batch()
        if batch['status'] not in ('submitted','cancelled'):raise ValueError('Resolve this uncertain job before reprinting.')
        count,ok=QInputDialog.getInt(self,'Reprint quantity','How many replacement labels? No stock will be added.',batch['quantity'],1,10000)
        if ok:
            batch['quantity']=count
            self.start_print(reprint_batch=batch)

    def resolve(self,printed):
        batch=self.history_batch()
        if batch['status'] not in ('sending','uncertain','prepared'):raise ValueError('This batch is already resolved.')
        text=(f"Batch L-{batch['id']} — {batch['quantity']} labels.\n"+
            ('Confirm every label printed. This posts the recorded stock addition once.' if printed else 'Cancel this job without adding stock. Cancel queued output in your system print queue too. If only some labels printed, reconcile stock manually and reprint only what is needed.'))
        if QMessageBox.question(self,'Reconcile printer output',text,QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.db.settle_label_batch(batch['id'],printed,'Admin checked printer output.')
        self.refresh_history()
        if self.on_changed:self.on_changed()
