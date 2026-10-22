"""Millimetre label documents shared by designer, PDF and physical printer.

Code 128 encoding is delegated to python-barcode; QR uses qrcode. Barcode
modules are integer device dots, with explicit quiet zones and no antialiasing.
"""
import base64
from functools import lru_cache
import json
import math
from string import Formatter

from barcode import Code128
import qrcode
from PySide6.QtCore import Qt, QRectF, QSizeF, QMarginsF
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QGuiApplication, QImage, QPainter, QPageLayout, QPageSize, QPen, QTransform
from PySide6.QtPrintSupport import QPrinter

PX_PER_MM = 96 / 25.4
KINDS = ('text', 'barcode', 'qr', 'rectangle', 'line', 'image')


def default_design():
    return dict(version=1, width=100, height=50, elements=[
        dict(kind='text', x=4, y=3, w=92, h=7, text='{name}', font='Arial', size=14, bold=True),
        dict(kind='text', x=4, y=11, w=92, h=6, text='{category} / {brand} / {size}', font='Arial', size=10),
        dict(kind='text', x=4, y=18, w=92, h=6, text='Rs. {rate}', font='Arial', size=12, bold=True),
        dict(kind='barcode', x=4, y=27, w=92, h=15, text='{barcode}'),
        dict(kind='text', x=4, y=43, w=92, h=5, text='{barcode}', font='Arial', size=9, align='center'),
    ])


def metadata_design():
    return dict(version=1,width=100,height=50,elements=[
        dict(kind='text',x=3,y=3,w=48,h=12,text='{name}',font='Arial',size=12,bold=True),
        dict(kind='text',x=3,y=16,w=48,h=10,text='{category} / {brand} / {size}',font='Arial',size=9),
        dict(kind='text',x=3,y=27,w=48,h=8,text='Rs. {rate}',font='Arial',size=12,bold=True),
        dict(kind='text',x=3,y=37,w=48,h=7,text='{barcode}',font='Arial',size=9),
        dict(kind='qr',x=56,y=5,w=40,h=40,text='{metadata}'),
    ])


def expand(pattern, metadata):
    if not isinstance(pattern, str) or len(pattern) > 4096:
        raise ValueError('Text/template must be at most 4096 characters.')
    for _, field, spec, conversion in Formatter().parse(pattern):
        if field is not None and (field not in metadata or spec or conversion):
            raise ValueError('Unknown field or format: {' + str(field) + '}. Use the listed plain field names.')
    return pattern.format_map(metadata)


def custom_metadata(text):
    values = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition('=')
        key = key.strip()
        if not separator or not key.isidentifier() or key in values:
            raise ValueError('Custom metadata must use unique field=value lines.')
        values[key] = value.strip()
    return values


def metadata_for(item, location, custom=None):
    result = {k: str(item.get(k) or '') for k in ('name', 'barcode', 'size', 'color')}
    result.update(category=str(item.get('category_name') or ''), brand=str(item.get('subtype_name') or ''),
                  style=str(item.get('style') or item.get('subtype_name') or ''),
                  rate=f"{float(item.get('rate', 0)):.2f}", godown=location)
    for key, value in (custom or {}).items():
        if key in result or key == 'metadata':
            raise ValueError('Custom field duplicates a catalog field: ' + key)
        result[key] = value
    result['metadata'] = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return result


@lru_cache(maxsize=128)
def barcode_pattern(text):
    if not text or len(text) > 512 or any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        raise ValueError('Code 128 needs 1–512 printable ASCII characters. Use QR for Unicode metadata.')
    return Code128(text).build()[0]


@lru_cache(maxsize=128)
def qr_matrix(text):
    if not text or len(text.encode('utf-8')) > 2000:
        raise ValueError('QR data must contain 1–2000 UTF-8 bytes.')
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.get_matrix()


def element_bounds(element):
    transform = QTransform().translate(element['x'], element['y']).rotate(element.get('rotation', 0))
    return transform.mapRect(QRectF(0, 0, element['w'], element['h']))


def validate_design(design, metadata=None, dpi=203, offsets=(0, 0)):
    if not isinstance(design, dict) or design.get('version') != 1:
        raise ValueError('Unsupported label template version.')
    width, height = design.get('width'), design.get('height')
    for value in (width, height):
        if not isinstance(value, (int, float)) or not math.isfinite(value) or not 10 <= value <= 500:
            raise ValueError('Label width and height must be between 10 and 500 mm.')
    elements = design.get('elements')
    if not isinstance(elements, list) or not 1 <= len(elements) <= 100:
        raise ValueError('Add 1–100 elements to the label.')
    settings=design.get('settings', {})
    if not isinstance(settings, dict) or settings.get('dpi',203) not in (203,300,600):
        raise ValueError('Invalid printer settings in template.')
    for value in (*offsets, settings.get('offset_x',0), settings.get('offset_y',0)):
        if not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value)>20:
            raise ValueError('Printer offsets must be between -20 and 20 mm.')
    payloads = []
    for e in elements:
        if not isinstance(e, dict) or e.get('kind') not in KINDS:
            raise ValueError('Unknown label element.')
        for field in ('x', 'y', 'w', 'h', 'rotation', 'size'):
            value = e.get(field, 0 if field == 'rotation' else 10)
            if not isinstance(value, (float, int)) or not math.isfinite(value):
                raise ValueError('Label coordinates and font sizes must be finite numbers.')
        if e['w'] <= 0 or e['h'] <= 0 or not 1 <= e.get('size', 10) <= 150:
            raise ValueError('Element dimensions must be positive; font size must be 1–150 pt.')
        bounds = element_bounds(e).translated(*offsets)
        if not QRectF(-.01, -.01, width+.02, height+.02).contains(bounds):
            raise ValueError('An element is outside the label. Move/resize it or correct the print offset.')
        if e['kind'] == 'image':
            try:
                image = QImage.fromData(base64.b64decode(e.get('image', ''), validate=True))
            except (ValueError, TypeError) as exc:
                raise ValueError('Invalid logo image.') from exc
            if image.isNull() or len(e.get('image', '')) > 4_000_000 or image.width()*image.height() > 16_000_000:
                raise ValueError('Logo is missing or too large (maximum 3 MB / 16 megapixels).')
        if metadata is None:
            continue
        text = expand(e.get('text', ''), metadata)
        if e['kind']=='text' and QGuiApplication.instance():
            font=QFont(e.get('font','Arial'))
            font.setPixelSize(max(1,round(e.get('size',10)*dpi/72)))
            font.setBold(bool(e.get('bold')));font.setItalic(bool(e.get('italic')))
            rect=QRectF(0,0,e['w']*dpi/25.4,e['h']*dpi/25.4)
            bounds=QFontMetricsF(font).boundingRect(rect,Qt.TextWordWrap|Qt.AlignLeft,text)
            if bounds.width()>rect.width()+1 or bounds.height()>rect.height()+1:
                raise ValueError('Text is clipped: enlarge its box, shorten the text or reduce its font size.')
        if e['kind'] in ('barcode', 'qr'):
            if e.get('rotation',0)%90:
                raise ValueError('Rotate barcodes in 90° steps to preserve scanner accuracy.')
            modules = len(barcode_pattern(text))+20 if e['kind'] == 'barcode' else len(qr_matrix(text))
            dots = (e['w'] if e['kind'] == 'barcode' else min(e['w'], e['h'])) * dpi / 25.4
            if int(dots / modules) < 2:
                raise ValueError('Barcode data is too long for this box at the selected DPI. Enlarge it, shorten the data, or use QR.')
            if e['kind'] == 'barcode' and e['h'] < 8:
                raise ValueError('Code 128 bars must be at least 8 mm tall.')
            payloads.append(text)
    # Later opaque elements must not obscure scanner symbols or their quiet zones.
    for index,e in enumerate(elements):
        if e['kind'] in ('barcode','qr'):
            box=element_bounds(e)
            for above in elements[index+1:]:
                if box.intersects(element_bounds(above)):
                    raise ValueError('An element overlaps a barcode or its quiet zone. Move it away or send it behind the barcode.')
    return list(dict.fromkeys(payloads))


def draw_element(painter, e, metadata, scale):
    """Paint local coordinates; caller positions/rotates the element."""
    rect = QRectF(0, 0, e['w']*scale, e['h']*scale)
    kind = e['kind']
    painter.save()
    painter.setClipRect(rect)
    text = expand(e.get('text', ''), metadata)
    if kind == 'text':
        font = QFont(e.get('font', 'Arial'))
        font.setPixelSize(max(1, round(e.get('size', 10)*25.4/72*scale)))
        font.setBold(bool(e.get('bold')))
        font.setItalic(bool(e.get('italic')))
        font.setUnderline(bool(e.get('underline')))
        painter.setFont(font)
        painter.setPen(QColor(e.get('color', '#000000')))
        alignment = {'left': Qt.AlignLeft, 'center': Qt.AlignHCenter, 'right': Qt.AlignRight}.get(e.get('align'), Qt.AlignLeft)
        painter.drawText(rect, alignment | Qt.AlignVCenter | Qt.TextWordWrap, text)
    elif kind in ('barcode', 'qr'):
        painter.setRenderHint(QPainter.Antialiasing, False)
        painter.fillRect(rect, Qt.white)
        painter.setPen(Qt.NoPen)
        painter.setBrush(Qt.black)
        if kind == 'barcode':
            pattern = barcode_pattern(text)
            unit = max(1, int(rect.width()/(len(pattern)+20)))
            left = int((rect.width()-len(pattern)*unit)/2)
            for index, bit in enumerate(pattern):
                if bit == '1':
                    painter.drawRect(QRectF(left+index*unit, 0, unit, int(rect.height())))
        else:
            matrix = qr_matrix(text)
            unit = max(1, int(min(rect.width(),rect.height())/len(matrix)))
            x = int((rect.width()-len(matrix)*unit)/2)
            y = int((rect.height()-len(matrix)*unit)/2)
            for row, values in enumerate(matrix):
                for col, black in enumerate(values):
                    if black:
                        painter.drawRect(QRectF(x+col*unit,y+row*unit,unit,unit))
    elif kind in ('rectangle', 'line'):
        painter.setPen(QPen(QColor(e.get('color','#000000')), max(.15*scale, 1)))
        painter.setBrush(QColor(e.get('fill','#ffffff')) if e.get('filled') else Qt.NoBrush)
        if kind == 'line':
            painter.drawLine(rect.topLeft(), rect.bottomRight())
        else:
            painter.drawRect(rect.adjusted(.1*scale,.1*scale,-.1*scale,-.1*scale))
    elif kind == 'image':
        image = QImage.fromData(base64.b64decode(e['image']))
        target = QSizeF(image.size())
        target.scale(rect.size(), Qt.KeepAspectRatio)
        painter.drawImage(QRectF((rect.width()-target.width())/2,(rect.height()-target.height())/2,target.width(),target.height()),image)
    painter.restore()


def paint_label(painter, design, metadata, dpi, offsets=(0, 0)):
    scale = dpi/25.4
    for e in design['elements']:
        painter.save()
        painter.translate(round((e['x']+offsets[0])*scale),round((e['y']+offsets[1])*scale))
        painter.rotate(e.get('rotation',0))
        draw_element(painter,e,metadata,scale)
        painter.restore()


def configure_label_printer(printer, design, dpi=203):
    printer.setResolution(dpi)
    printer.setCopyCount(1)  # quantity is page count, never multiplied by driver copies
    printer.setFullPage(True)
    size = QPageSize(QSizeF(design['width'],design['height']),QPageSize.Millimeter,'Custom label',QPageSize.ExactMatch)
    layout = QPageLayout(size,QPageLayout.Portrait,QMarginsF(0,0,0,0),QPageLayout.Millimeter)
    if not printer.setPageLayout(layout):
        raise ValueError('Printer rejected this paper size. Set a matching custom label size in its driver.')
    actual = printer.pageLayout().fullRect(QPageLayout.Millimeter)
    if abs(actual.width()-design['width'])>.3 or abs(actual.height()-design['height'])>.3:
        raise ValueError('The printer changed the paper dimensions. Configure the driver for the entered label size.')
    return printer.resolution()


def print_labels(printer, design, metadata, quantity, offsets=(0, 0)):
    dpi = printer.resolution()
    validate_design(design,metadata,dpi,offsets)
    if not printer.isValid():
        raise ValueError('Printer is unavailable. Check the connection and installed driver.')
    painter = QPainter()
    if not painter.begin(printer):
        raise ValueError('Could not start the print job.')
    try:
        for page in range(quantity):
            if page and not printer.newPage():
                raise ValueError('Printer stopped during this batch. Check the output before retrying.')
            paint_label(painter,design,metadata,dpi,offsets)
    finally:
        ended = painter.end()
    if not ended or printer.printerState() in (QPrinter.Error,QPrinter.Aborted):
        raise ValueError('Print submission failed or was interrupted. Some labels may already have printed.')


def calibration_design(width, height):
    if width < 30 or height < 30:
        raise ValueError('Use a label at least 30 × 30 mm for the 20 mm calibration square.')
    return dict(version=1,width=width,height=height,elements=[
        dict(kind='rectangle',x=3,y=3,w=20,h=20),
        dict(kind='text',x=3,y=24,w=width-6,h=5,text='20 mm square',size=7),
    ])
