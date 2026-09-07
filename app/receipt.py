
"""
receipt.py
----------
Professional A4 Tax Invoice for BELLI APPERAL.
"""

from html import escape
from urllib.parse import urlencode

from io import BytesIO

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QFileDialog,
    QMessageBox,
)
from PySide6.QtCore import QSizeF, QUrl, QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QTextDocument, QFont, QPageSize, QImage
from PySide6.QtPrintSupport import QPrinter, QPrintDialog

from widgets import rupees

try:
    import qrcode
except ImportError:
    qrcode = None


# ============================================================
# COMPANY SETTINGS
# ============================================================

SHOP_NAME = "BELLI APPERAL"
SHOP_GSTIN = "29UZKPS8200M1Z5"

SHOP_ADDRESS_LINE_1 = "Building No./Flat No.: 22, 1st Main Road"
SHOP_ADDRESS_LINE_2 = "Mylasandara, Bengaluru Urban, Karnataka - 560059"
SHOP_LOCATION = "Bengaluru, Karnataka"

SHOP_ADDRESS = (
    "Building No./Flat No.: 22, "
    "1st Main Road, "
    "Mylasandara, "
    "Bengaluru Urban, Karnataka - 560059"
)

SHOP_PHONE = "6360086532"

# ============================================================
# UPI PAYMENT QR
# ============================================================
# The QR uses a UPI VPA. For a PhonePe number-based VPA this is
# commonly 9008080213@ybl, but verify the actual VPA on the
# receiving PhonePe account and change this value if needed.
UPI_ID = "9008080213@ybl"
UPI_PAYEE_NAME = SHOP_NAME


# ============================================================
# BANK DETAILS
# Change this single string later.
# Do NOT put real bank credentials in GitHub.
# ============================================================

BANK_DETAILS = """\
Bank Name: SAMPLE BANK
A/C No.: XXXXXXXX
IFSC: XXXXXXXX
Branch: SAMPLE BRANCH
"""

A4_WIDTH_PT = 595.0
A4_HEIGHT_PT = 842.0
INVOICE_WIDTH_PT = 565.0


TERMS = [
    "Goods once sold will not be taken back or exchanged unless agreed by the shop.",
    "Please check the items and bill before leaving the shop.",
    "Subject to the terms and conditions of BELLI APPERAL.",
]


def _value(row, key, default=None):
    """Safely read sqlite3.Row/dict values."""
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _money(value):
    try:
        return rupees(float(value or 0))
    except (TypeError, ValueError):
        return "₹0.00"


def _num(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _format_date(value):
    """Convert YYYY-MM-DD to DD/MM/YYYY."""
    if not value:
        return ""
    text = str(value)
    parts = text.split("-")
    if len(parts) == 3 and len(parts[0]) == 4:
        return f"{parts[2]}/{parts[1]}/{parts[0]}"
    return text


def _number_to_words_indian(number):
    ones = [
        "", "One", "Two", "Three", "Four", "Five", "Six", "Seven",
        "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen",
        "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen",
        "Nineteen",
    ]
    tens = [
        "", "", "Twenty", "Thirty", "Forty", "Fifty",
        "Sixty", "Seventy", "Eighty", "Ninety",
    ]

    def under_100(n):
        if n < 20:
            return ones[n]
        return tens[n // 10] + (f" {ones[n % 10]}" if n % 10 else "")

    def under_1000(n):
        if n < 100:
            return under_100(n)
        result = f"{ones[n // 100]} Hundred"
        if n % 100:
            result += f" {under_100(n % 100)}"
        return result

    if number < 1000:
        return under_1000(number)

    parts = []

    crore = number // 10_000_000
    number %= 10_000_000

    lakh = number // 100_000
    number %= 100_000

    thousand = number // 1000
    number %= 1000

    if crore:
        parts.append(f"{_number_to_words_indian(crore)} Crore")
    if lakh:
        parts.append(f"{_number_to_words_indian(lakh)} Lakh")
    if thousand:
        parts.append(f"{_number_to_words_indian(thousand)} Thousand")
    if number:
        parts.append(under_1000(number))

    return " ".join(parts)


def _amount_in_words(number):
    """Indian-style amount in words."""
    number = _num(number)

    rupee_part = int(number)
    paise_part = int(round((number - rupee_part) * 100))

    if paise_part == 100:
        rupee_part += 1
        paise_part = 0

    words = (
        "Zero"
        if rupee_part == 0
        else _number_to_words_indian(rupee_part)
    )

    result = f"{words} Rupees"

    if paise_part:
        result += (
            f" and {_number_to_words_indian(paise_part)} Paise"
        )

    return result + " Only"


def _customer_address(bill_row):
    """
    Customer address is optional.

    If a future database version provides customer_address/address,
    it will automatically appear.
    """
    return (
        _value(bill_row, "customer_address", "")
        or _value(bill_row, "address", "")
        or ""
    ).strip()


def _paid_amount(bill_row):
    """
    Supports paid_amount/amount_paid if supplied by the database.

    Until get_bill() exposes the payment aggregate, the fallback is
    the bill total so the existing receipt flow keeps working.
    """
    total = _num(_value(bill_row, "total", 0))

    paid = _value(bill_row, "paid_amount", None)
    if paid is None:
        paid = _value(bill_row, "amount_paid", None)

    if paid is None:
        paid = total

    return max(0.0, min(_num(paid), total))


def _balance_amount(bill_row):
    total = _num(_value(bill_row, "total", 0))

    balance = _value(bill_row, "balance", None)
    if balance is None:
        balance = _value(bill_row, "outstanding", None)

    if balance is None:
        balance = total - _paid_amount(bill_row)

    return max(0.0, _num(balance))


def _upi_payment_uri(bill_row, total):
    """Build a standard UPI payment URI with the invoice amount."""
    bill_no = str(_value(bill_row, "bill_no", "invoice") or "invoice")
    params = {
        "pa": UPI_ID,
        "pn": UPI_PAYEE_NAME,
        "am": f"{max(_num(total), 0.0):.2f}",
        "cu": "INR",
        "tn": f"Invoice {bill_no}",
    }
    return "upi://pay?" + urlencode(params)


def _make_payment_qr_image(bill_row, total):
    """Create the payment QR as a QImage for QTextDocument."""
    if qrcode is None:
        return QImage()

    payload = _upi_payment_uri(bill_row, total)
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=3,
    )
    qr.add_data(payload)
    qr.make(fit=True)
    pil_image = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    buffer = BytesIO()
    pil_image.save(buffer, format="PNG")
    image = QImage()
    image.loadFromData(buffer.getvalue(), "PNG")
    return image


def build_receipt_html(bill_row, bill_items):
    """Build the complete A4 tax invoice as HTML."""

    bill_no = escape(str(_value(bill_row, "bill_no", "")))
    bill_date = escape(
        _format_date(_value(bill_row, "bill_date", ""))
    )

    customer_name = (
        _value(bill_row, "customer_name", "")
        or "Walk-in Customer"
    )
    customer_phone = _value(bill_row, "customer_phone", "") or ""
    customer_address = _customer_address(bill_row)

    customer_name = escape(str(customer_name))
    customer_phone = escape(str(customer_phone))
    customer_address = escape(str(customer_address))

    subtotal = _num(_value(bill_row, "subtotal", 0))
    discount_percent = _num(
        _value(bill_row, "discount_percent", 0)
    )
    discount_amount = _num(
        _value(bill_row, "discount_amount", 0)
    )

    taxable_amount = _value(
        bill_row, "taxable_amount", None
    )
    if taxable_amount is None:
        taxable_amount = max(subtotal - discount_amount, 0.0)
    taxable_amount = _num(taxable_amount)

    gst_rate = _num(_value(bill_row, "gst_rate", 0))
    gst_amount = _num(_value(bill_row, "gst_amount", 0))
    total = _num(_value(bill_row, "total", 0))

    payment_mode = escape(
        str(_value(bill_row, "payment_mode", "Cash") or "Cash")
    )

    paid_amount = _paid_amount(bill_row)
    balance_amount = _balance_amount(bill_row)

    # Intra-state Karnataka sale: 5% GST -> 2.5% CGST + 2.5% SGST.
    cgst_rate = gst_rate / 2 if gst_rate else 0.0
    sgst_rate = gst_rate / 2 if gst_rate else 0.0
    cgst_amount = gst_amount / 2 if gst_amount else 0.0
    sgst_amount = gst_amount / 2 if gst_amount else 0.0

    item_rows = []

    for index, item in enumerate(bill_items, start=1):
        name = str(
            _value(item, "item_name_snapshot", "")
            or _value(item, "name", "")
            or "Item"
        )

        category = str(
            _value(item, "category_snapshot", "")
            or _value(item, "category", "")
            or ""
        )

        product_id = (
            _value(item, "barcode", "")
            or _value(item, "product_id", "")
            or "-"
        )

        hsn = (
            _value(item, "hsn", "")
            or _value(item, "hsn_code", "")
            or "-"
        )

        quantity = _num(_value(item, "quantity", 0))
        rate = _num(_value(item, "rate", 0))
        line_subtotal = _num(_value(item, "subtotal", 0))

        line_gst_rate = _num(
            _value(item, "gst_rate", gst_rate)
        )

        line_gst_amount = _num(
            _value(item, "gst_amount", 0)
        )

        line_cgst = line_gst_amount / 2
        line_sgst = line_gst_amount / 2

        description = escape(name)

        if category:
            description += (
                f'<div class="item-category">'
                f'{escape(category)}</div>'
            )

        item_rows.append(
            f"""
            <tr class="item-row">
                <td class="center">{index}</td>
                <td class="center">{escape(str(product_id))}</td>
                <td>{description}</td>
                <td class="center">{escape(str(hsn))}</td>
                <td class="right">{_money(rate)}</td>
                <td class="center">{quantity:g}</td>
                <td class="right">{_money(line_subtotal)}</td>
                <td class="center">{line_gst_rate:g}%</td>
                <td class="center tax-cell">
                    CGST {_money(line_cgst)}<br>
                    SGST {_money(line_sgst)}
                </td>
                <td class="right">
                    <b>{_money(line_subtotal + line_gst_amount)}</b>
                </td>
            </tr>
            """
        )

    # The reference has a deliberately tall item grid.
    # Keep at least 8 rows so a one-item bill still looks like a
    # proper full A4 showroom invoice.
    while len(item_rows) < 8:
        item_rows.append(
            """
            <tr class="blank-item-row">
                <td>&nbsp;</td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
                <td>&nbsp;</td>
            </tr>
            """
        )

    customer_extra = ""

    if customer_phone:
        customer_extra += (
            f"<div><b>Phone:</b> {customer_phone}</div>"
        )

    if customer_address:
        customer_extra += (
            f"<div><b>Address:</b> {customer_address}</div>"
        )

    if not customer_extra:
        customer_extra = (
            '<div class="muted">'
            "Address / phone not provided"
            "</div>"
        )

    bank_html = escape(BANK_DETAILS).replace("\n", "<br>")

    terms_html = "".join(
        f"<li>{escape(term)}</li>"
        for term in TERMS
    )

    return f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>

* {{
    box-sizing: border-box;
}}

html,
body {{
    margin: 0;
    padding: 0;
    width: 565pt;
    background: #fff;
    color: #000;
}}

body {{
    font-family: Arial, Helvetica, sans-serif;
    font-size: 7.2pt;
}}

.invoice {{
    width: 565pt;
    border: 1.3px solid #000;
}}

table {{
    border-collapse: collapse;
}}

.center {{
    text-align: center;
}}

.right {{
    text-align: right;
}}

.small-muted {{
    font-size: 6.2pt;
    color: #444;
}}

.header-table {{
    width: 100%;
    height: 70pt;
    table-layout: fixed;
}}

.logo-cell {{
    width: 67pt;
    border-right: 1px solid #000;
    text-align: center;
    vertical-align: middle;
}}

.logo-box {{
    color: #aaa;
    font-size: 8pt;
    font-weight: bold;
    line-height: 1.2;
}}

.company-cell {{
    text-align: center;
    vertical-align: middle;
}}

.company-name {{
    font-size: 19pt;
    font-weight: bold;
}}

.company-address {{
    font-size: 8pt;
    margin-top: 5px;
}}

.company-gstin {{
    font-size: 8pt;
    font-weight: bold;
    margin-top: 4px;
}}

.location-table {{
    width: 100%;
    height: 21pt;
    table-layout: fixed;
    border-top: 1px solid #000;
}}

.location-table td {{
    padding: 4px 7px;
    font-size: 7.4pt;
    font-weight: bold;
}}

.location-left {{
    width: 50%;
    border-right: 1px solid #000;
}}

.location-right {{
    width: 50%;
}}

.title-row {{
    height: 20pt;
    border-top: 1px solid #000;
    border-bottom: 1px solid #000;
    text-align: center;
    padding: 4px;
    font-size: 10.5pt;
    font-weight: bold;
}}

.invoice-info {{
    width: 100%;
    height: 72pt;
    table-layout: fixed;
}}

.invoice-info td {{
    vertical-align: top;
    padding: 5px 7px;
    font-size: 6.9pt;
}}

.info-left {{
    width: 50%;
    border-right: 1px solid #000;
}}

.info-right {{
    width: 50%;
}}

.info-line {{
    margin-bottom: 2px;
}}

.info-label {{
    font-weight: bold;
}}

.invoice-items {{
    width: 100%;
    table-layout: fixed;
    border-top: 1px solid #000;
}}

.invoice-items th {{
    border: 1px solid #000;
    height: 29pt;
    padding: 3px 2px;
    font-size: 6.2pt;
    font-weight: bold;
    text-align: center;
    vertical-align: middle;
}}

.invoice-items td {{
    border: 1px solid #000;
    height: 28pt;
    padding: 3px 2px;
    font-size: 6.8pt;
    vertical-align: middle;
}}

.invoice-items .blank-item-row td {{
    height: 28pt;
}}

.invoice-items .item-category {{
    font-size: 5.8pt;
    color: #444;
    margin-top: 1px;
}}

.invoice-items .tax-cell {{
    font-size: 5.4pt;
    line-height: 1.15;
}}

.summary-area {{
    width: 100%;
    table-layout: fixed;
    border-top: 1px solid #000;
}}

.summary-cell {{
    width: 40%;
    vertical-align: top;
    padding: 0;
    border-right: 1px solid #000;
}}

.qr-cell {{
    width: 22%;
    vertical-align: middle;
    text-align: center;
    padding: 4px 2px;
}}

.qr-title {{
    font-size: 7pt;
    font-weight: bold;
    margin-bottom: 2px;
}}

.qr-amount {{
    font-size: 6.8pt;
    font-weight: bold;
    margin-top: 2px;
}}

.qr-upi {{
    font-size: 5.2pt;
    margin-top: 1px;
}}

.remarks-title,
.summary-title {{
    height: 19pt;
    border-bottom: 1px solid #000;
    padding: 4px 6px;
    font-size: 7pt;
    font-weight: bold;
}}

.summary-title {{
    text-align: center;
}}

.remarks-body {{
    height: 47pt;
    padding: 5px 6px;
}}

.summary-table {{
    width: 100%;
    table-layout: fixed;
}}

.summary-table td {{
    height: 17pt;
    border-bottom: 1px solid #000;
    padding: 3px 5px;
    font-size: 6.8pt;
}}

.summary-label {{
    width: 55%;
    text-align: left;
    font-weight: bold;
}}

.summary-value {{
    width: 45%;
    text-align: right;
}}

.invoice-total td {{
    font-size: 8.5pt;
    font-weight: bold;
    border-top: 1.2px solid #000;
}}

.amount-words {{
    width: 100%;
    min-height: 25pt;
    border-top: 1px solid #000;
    border-bottom: 1px solid #000;
    padding: 5px 6px;
    font-size: 6.8pt;
}}

.payment-line {{
    width: 100%;
    min-height: 23pt;
    border-bottom: 1px solid #000;
    padding: 5px 6px;
    font-size: 6.8pt;
}}

.signature-table {{
    width: 100%;
    height: 69pt;
    table-layout: fixed;
    border-bottom: 1px solid #000;
}}

.signature-table td {{
    width: 50%;
    vertical-align: bottom;
    padding: 6px 18px;
}}

.signature-left {{
    text-align: center;
}}

.signature-right {{
    border-left: 1px solid #000;
    text-align: center;
}}

.signature-box {{
    width: 104pt;
    height: 26pt;
    border: 1px solid #000;
    margin: 0 auto 4px auto;
}}

.signature-label {{
    font-size: 6.8pt;
    font-weight: bold;
}}

.footer {{
    height: 19pt;
    text-align: center;
    padding: 4px;
    font-size: 8.5pt;
    font-weight: bold;
}}

</style>
</head>

<body>

<div class="invoice">

    <table class="header-table">
        <tr>
            <td class="header-main-cell">
                <div class="company-name">
                    {escape(SHOP_NAME)}
                </div>

                <div class="company-address">
                    {escape(SHOP_ADDRESS_LINE_1)}<br>
                    {escape(SHOP_ADDRESS_LINE_2)}
                </div>

                <div class="company-gstin">
                    GSTIN: {escape(SHOP_GSTIN)}
                </div>

                <div class="location-line">
                    <b>Location: {escape(SHOP_LOCATION)}</b>
                    &nbsp;&nbsp;&nbsp;|&nbsp;&nbsp;&nbsp;
                    <b>Phone: {escape(SHOP_PHONE or "-")}</b>
                </div>
            </td>
        </tr>
    </table>

    <div class="title-row">
        TAX INVOICE
    </div>

    <table class="invoice-info">
        <tr>

            <td class="info-left">

                <div class="info-line">
                    <span class="info-label">Invoice No:</span>
                    {bill_no}
                </div>

                <div class="info-line">
                    <span class="info-label">Cust. ID:</span>
                    {_value(bill_row, "customer_id", "") or "-"}
                </div>

                <div class="info-line">
                    <span class="info-label">Name:</span>
                    {customer_name}
                </div>

                <div class="info-line">
                    <span class="info-label">Address:</span>
                    {customer_address or "-"}
                </div>

                {customer_extra}

            </td>

            <td class="info-right">

                <div class="info-line">
                    <span class="info-label">Date:</span>
                    {bill_date}
                </div>

                <div class="info-line">
                    <span class="info-label">Type:</span>
                    Retail Sale
                </div>

                <div class="info-line">
                    <span class="info-label">GSTIN:</span>
                    -
                </div>

                <div class="info-line">
                    <span class="info-label">POS:</span>
                    Karnataka (29)
                </div>

                <div class="info-line">
                    <span class="info-label">Payment Mode:</span>
                    {escape(payment_mode)}
                </div>

            </td>

        </tr>
    </table>

    <table class="invoice-items">

        <thead>
            <tr>
                <th style="width:5%;">Sr.</th>
                <th style="width:8%;">Product<br>ID</th>
                <th style="width:20%;">Product Description</th>
                <th style="width:8%;">HSN</th>
                <th style="width:8%;">MRP</th>
                <th style="width:7%;">Qty</th>
                <th style="width:12%;">Taxable<br>Value</th>
                <th style="width:7%;">GST%</th>
                <th style="width:15%;">CGST &amp;<br>SGST / IGST</th>
                <th style="width:10%;">Amount</th>
            </tr>
        </thead>

        <tbody>
            {''.join(item_rows)}

            <tr>
                <td colspan="8">&nbsp;</td>
                <td class="right">
                    <b>Total</b>
                </td>
                <td class="right">
                    <b>{_money(total)}</b>
                </td>
            </tr>
        </tbody>

    </table>

    <table class="summary-area">
        <tr>
            <td class="summary-cell">
                <div class="summary-title">Summary</div>
                <table class="summary-table">
                    <tr>
                        <td class="summary-label">Taxable Amount</td>
                        <td class="summary-value">{_money(taxable_amount)}</td>
                    </tr>
                    <tr>
                        <td class="summary-label">Discount</td>
                        <td class="summary-value">{_money(discount_amount)}</td>
                    </tr>
                    <tr>
                        <td class="summary-label">CGST ({gst_rate / 2:g}%)</td>
                        <td class="summary-value">{_money(cgst_amount)}</td>
                    </tr>
                    <tr>
                        <td class="summary-label">SGST ({gst_rate / 2:g}%)</td>
                        <td class="summary-value">{_money(sgst_amount)}</td>
                    </tr>
                    <tr>
                        <td class="summary-label">IGST (0.0%)</td>
                        <td class="summary-value">{_money(0)}</td>
                    </tr>
                    <tr class="invoice-total">
                        <td class="summary-label">Invoice Amount</td>
                        <td class="summary-value">{_money(total)}</td>
                    </tr>
                </table>
            </td>

            <td class="qr-cell">
                <img src="qr://invoice-payment" width="88" height="88">
                <div class="qr-title">SCAN TO PAY</div>
                <div class="qr-amount">{_money(total)}</div>
                <div class="qr-upi">{escape(UPI_ID)}</div>
            </td>
        </tr>
    </table>


    <div class="amount-words">
        <b>Amount in Words:</b>
        {escape(_amount_in_words(total))}
    </div>

    <div class="payment-line">
        <b>Payment Mode:</b> {payment_mode}
        &nbsp;&nbsp;&nbsp;
        <b>Paid:</b> {_money(paid_amount)}
        &nbsp;&nbsp;&nbsp;
        <b>Balance:</b> {_money(balance_amount)}
        &nbsp;&nbsp;&nbsp;
        <b>Payment in favour of:</b> {escape(SHOP_NAME)}
    </div>

    <table class="signature-table">
        <tr>

            <td class="signature-left">
                <div class="signature-box"></div>
                <div class="signature-label">
                    Accountant's Signature
                </div>
            </td>

            <td class="signature-right">
                <div class="signature-box"></div>
                <div class="signature-label">
                    Customer's Signature
                </div>
            </td>

        </tr>
    </table>

    <div class="footer">
        Thank You. Visit Again.
    </div>

</div>

</body>
</html>
"""


def build_receipt_text(bill_row, bill_items):
    """Plain-text fallback for older code."""

    lines = [
        SHOP_NAME,
        "TAX INVOICE",
        f"GSTIN: {SHOP_GSTIN}",
        SHOP_ADDRESS,
        "",
        f"Invoice No: {_value(bill_row, 'bill_no', '')}",
        (
            "Invoice Date: "
            f"{_format_date(_value(bill_row, 'bill_date', ''))}"
        ),
        (
            "Customer: "
            f"{_value(bill_row, 'customer_name', '') or 'Walk-in Customer'}"
        ),
        "",
        "Items:",
        "-" * 75,
        f"{'Item':30} {'Qty':>5} {'Rate':>12} {'Amount':>14}",
        "-" * 75,
    ]

    for item in bill_items:
        name = str(
            _value(item, "item_name_snapshot", "")
            or _value(item, "name", "")
        )
        qty = _num(_value(item, "quantity", 0))
        rate = _num(_value(item, "rate", 0))
        subtotal = _num(_value(item, "subtotal", 0))

        lines.append(
            f"{name[:30]:30} "
            f"{qty:>5g} "
            f"{_money(rate):>12} "
            f"{_money(subtotal):>14}"
        )

    lines.extend(
        [
            "-" * 75,
            (
                "Subtotal:       "
                f"{_money(_value(bill_row, 'subtotal', 0))}"
            ),
            (
                "Discount:       "
                f"{_money(_value(bill_row, 'discount_amount', 0))}"
            ),
            (
                "Taxable Amount: "
                f"{_money(_value(bill_row, 'taxable_amount', 0))}"
            ),
            (
                "GST:            "
                f"{_money(_value(bill_row, 'gst_amount', 0))}"
            ),
            (
                "GRAND TOTAL:    "
                f"{_money(_value(bill_row, 'total', 0))}"
            ),
            "",
            (
                "Payment Mode: "
                f"{_value(bill_row, 'payment_mode', 'Cash')}"
            ),
            f"Paid: {_money(_paid_amount(bill_row))}",
            f"Balance: {_money(_balance_amount(bill_row))}",
            "",
            "Thank you for shopping with BELLI APPERAL",
        ]
    )

    return "\n".join(lines)


class ReceiptDialog(QDialog):
    def __init__(self, bill_row, bill_items, parent=None):
        super().__init__(parent)

        self.setWindowTitle(
            f"Tax Invoice - {_value(bill_row, 'bill_no', '')}"
        )
        self.resize(900, 1100)

        self.bill_row = bill_row
        self.bill_items = bill_items

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        self.text_edit.setFont(QFont("Arial", 10))

        document = QTextDocument(self)
        document.setDocumentMargin(0)

        # Register the bill-specific UPI QR as a document resource so it
        # prints correctly in both the A4 printer output and saved PDF.
        qr_image = _make_payment_qr_image(
            bill_row,
            _value(bill_row, "total", 0),
        )
        if not qr_image.isNull():
            document.addResource(
                QTextDocument.ImageResource,
                QUrl("qr://invoice-payment"),
                qr_image,
            )

        document.setHtml(
            build_receipt_html(bill_row, bill_items)
        )

        # Force the preview to be a real A4 page.
        document.setPageSize(
            QSizeF(A4_WIDTH_PT, A4_HEIGHT_PT)
        )

        self.text_edit.setDocument(document)
        layout.addWidget(self.text_edit)

        btn_row = QHBoxLayout()

        print_btn = QPushButton("Print A4")
        print_btn.clicked.connect(self.print_receipt)

        pdf_btn = QPushButton("Save as PDF")
        pdf_btn.setProperty("role", "secondary")
        pdf_btn.clicked.connect(self.save_pdf)

        close_btn = QPushButton("Close")
        close_btn.setProperty("role", "secondary")
        close_btn.clicked.connect(self.accept)

        btn_row.addWidget(print_btn)
        btn_row.addWidget(pdf_btn)
        btn_row.addStretch()
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    @staticmethod
    def _configure_printer(printer):
        printer.setResolution(300)
        printer.setPageSize(QPageSize(QPageSize.A4))
        printer.setPageMargins(
            4,
            4,
            4,
            4,
            QPrinter.Millimeter,
        )

    def _prepare_document_for_printer(self, printer):
        page_rect = printer.pageRect(QPrinter.Point)
        self.text_edit.document().setPageSize(
            page_rect.size()
        )

    def print_receipt(self):
        printer = QPrinter(QPrinter.HighResolution)
        self._configure_printer(printer)

        dialog = QPrintDialog(printer, self)

        if dialog.exec() == QDialog.Accepted:
            self._prepare_document_for_printer(printer)
            self.text_edit.document().print_(printer)
            self.text_edit.document().setPageSize(
                QSizeF(A4_WIDTH_PT, A4_HEIGHT_PT)
            )

    def save_pdf(self):
        default_name = (
            f"{_value(self.bill_row, 'bill_no', 'invoice')}.pdf"
        )

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Tax Invoice as PDF",
            default_name,
            "PDF Files (*.pdf)",
        )

        if not path:
            return

        printer = QPrinter(QPrinter.HighResolution)
        self._configure_printer(printer)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)

        self._prepare_document_for_printer(printer)
        self.text_edit.document().print_(printer)
        self.text_edit.document().setPageSize(
            QSizeF(A4_WIDTH_PT, A4_HEIGHT_PT)
        )

        QMessageBox.information(
            self,
            "Saved",
            f"A4 tax invoice saved to:\n{path}",
        )
