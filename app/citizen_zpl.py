"""Fixed 4 x 2 inch / 203 DPI Citizen label, based on the shop's working ZPL."""
from decimal import Decimal, InvalidOperation

COMPANY='BELLI APPERAL SYNDICATE'
WIDTH=812
HEIGHT=406
LAYOUT='citizen-zpl-v1'


def ean13(value):
    value=str(value).strip()
    if len(value)!=13 or not value.isascii() or not value.isdigit():
        raise ValueError('Barcode must contain exactly 13 digits (EAN-13).')
    check=(-sum(int(d)*(1 if i%2==0 else 3) for i,d in enumerate(value[:12])))%10
    if int(value[-1])!=check:
        raise ValueError(f'Invalid EAN-13 check digit. For {value[:12]}, the last digit must be {check}.')
    return value


def price(value):
    try:
        result=Decimal(str(value))
        if not result.is_finite() or result<0 or result>99999999:
            raise ValueError('Prices must be between 0 and 99,999,999.')
        return f'{result.quantize(Decimal("0.01")):.2f}'
    except InvalidOperation as exc:
        raise ValueError('Enter a valid price.') from exc


def text(value,field,maximum):
    value=str(value).strip()
    if not value or len(value)>maximum or any(ord(c)<32 or ord(c)==127 for c in value):
        raise ValueError(f'{field} needs 1–{maximum} characters without line breaks or control characters.')
    # ^FH hex escapes every byte, including ^, ~ and _, so data cannot become ZPL commands.
    return ''.join(f'_{b:02X}' for b in value.encode('utf-8'))


def document():
    # A frozen, versioned batch descriptor. This is not an editable drawing.
    return dict(version=1,layout=LAYOUT,width=101.6,height=50.8,elements=[
        dict(kind='ean13',x=420*25.4/203,y=105*25.4/203,w=345*25.4/203,h=105*25.4/203,text='{label_barcode}')])


def create_zpl(brand,product,size,mrp,selling_price,barcode,quantity):
    if isinstance(quantity,bool) or not isinstance(quantity,int) or not 1<=quantity<=10000:
        raise ValueError('Quantity must be a whole number from 1 to 10,000.')
    brand=text(brand,'Brand',20);product=text(product,'Product',20);size=text(size,'Size',20)
    barcode=ean13(barcode);mrp=price(mrp);selling_price=price(selling_price)
    return f'''^XA
^PW{WIDTH}
^LL{HEIGHT}
^CI28
^FO35,25^A0N,34,34^FD{COMPANY}^FS
^FO35,85^A0N,26,26^FDBrand:^FS
^FO155,85^A0N,26,26^FH^FD{brand}^FS
^FO35,135^A0N,26,26^FDProd  :^FS
^FO155,135^A0N,26,26^FH^FD{product}^FS
^FO35,185^A0N,26,26^FDSize:^FS
^FO155,185^A0N,26,26^FH^FD{size}^FS
^FO35,235^A0N,26,26^FDMRP:^FS
^FO155,235^A0N,26,26^FDRs. {mrp}^FS
^FO35,285^A0N,26,26^FDSelling price:^FS
^FO225,285^A0N,26,26^FDRs.: {selling_price}^FS
^BY3,3,105
^FO450,105^BEN,105,Y,N^FD{barcode}^FS
^PQ{quantity},0,1,Y
^XZ
'''.encode('ascii')


def from_snapshot(layout,metadata,quantity):
    if layout!=document():
        raise ValueError('This is an older/custom label layout. Create a new fixed Citizen label for this item.')
    return create_zpl(metadata.get('label_brand',metadata.get('brand','')),metadata.get('label_product',metadata['name']),
                      metadata.get('label_size',metadata['size']),metadata['mrp'],metadata['rate'],metadata['label_barcode'],quantity)
