"""
receipt.py
----------
A simple printable / PDF-exportable receipt dialog.
"""

from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QTextEdit,
    QPushButton,
    QFileDialog,
    QMessageBox,
)
from PySide6.QtGui import QTextDocument, QFont
from PySide6.QtPrintSupport import QPrinter, QPrintDialog
from PySide6.QtCore import Qt

from widgets import rupees

SHOP_NAME = "SHOP NAME"
SHOP_TAGLINE = "Men's & Women's Wear"


def build_receipt_text(bill_row, bill_items):
    width = 42
    lines = []
    lines.append(SHOP_NAME.center(width))
    lines.append(SHOP_TAGLINE.center(width))
    lines.append("-" * width)
    lines.append(f"Bill No : {bill_row['bill_no']}")
    lines.append(f"Date    : {bill_row['bill_date']}")
    cname = (
        bill_row["customer_name"] if bill_row["customer_name"] else "Walk-in Customer"
    )
    lines.append(f"Customer: {cname}")
    if bill_row["customer_phone"]:
        lines.append(f"Phone   : {bill_row['customer_phone']}")
    lines.append("-" * width)
    lines.append(f"{'Item':<20}{'Qty':>4}{'Rate':>8}{'Amt':>10}")
    lines.append("-" * width)
    for it in bill_items:
        name = it["item_name_snapshot"]
        if len(name) > 20:
            name = name[:17] + "..."
        lines.append(
            f"{name:<20}{it['quantity']:>4}{it['rate']:>8.0f}{it['subtotal']:>10.2f}"
        )
    lines.append("-" * width)
    lines.append(f"{'Subtotal:':<32}{bill_row['subtotal']:>10.2f}")
    if bill_row["discount_amount"]:
        disc_label = (
            f"Discount ({bill_row['discount_percent']:.0f}%):"
            if bill_row["discount_percent"]
            else "Discount:"
        )
        lines.append(f"{disc_label:<32}{bill_row['discount_amount']:>10.2f}")
    lines.append(f"{'TOTAL:':<32}{bill_row['total']:>10.2f}")
    lines.append(f"{'Payment:':<32}{bill_row['payment_mode']:>10}")
    lines.append("-" * width)
    lines.append("Thank you for shopping with us!".center(width))
    return "\n".join(lines)


class ReceiptDialog(QDialog):
    def __init__(self, bill_row, bill_items, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Receipt - {bill_row['bill_no']}")
        self.resize(420, 560)
        self.bill_row = bill_row
        self.bill_items = bill_items

        layout = QVBoxLayout(self)

        self.text_edit = QTextEdit()
        self.text_edit.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(10)
        self.text_edit.setFont(mono)
        self.text_edit.setPlainText(build_receipt_text(bill_row, bill_items))
        layout.addWidget(self.text_edit)

        btn_row = QHBoxLayout()
        print_btn = QPushButton("Print")
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

    def print_receipt(self):
        printer = QPrinter(QPrinter.HighResolution)
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QDialog.Accepted:
            self.text_edit.document().print_(printer)

    def save_pdf(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Receipt as PDF",
            f"{self.bill_row['bill_no']}.pdf",
            "PDF Files (*.pdf)",
        )
        if not path:
            return
        printer = QPrinter(QPrinter.HighResolution)
        printer.setOutputFormat(QPrinter.PdfFormat)
        printer.setOutputFileName(path)
        self.text_edit.document().print_(printer)
        QMessageBox.information(self, "Saved", f"Receipt saved to:\n{path}")
