"""
widgets.py
----------
Small reusable UI helpers shared across tabs, so tab files stay focused on
their own logic instead of re-implementing common bits.
"""

from PySide6.QtWidgets import (
    QComboBox, QCompleter, QFrame, QLabel, QVBoxLayout, QHBoxLayout, QWidget,
    QInputDialog, QLineEdit, QMessageBox
)
from PySide6.QtCore import Qt

# Deletions of bills / customers require this password so records aren't
# removed by accident or by a curious customer glancing at the screen.
DELETE_PASSWORD = "1852"


def confirm_delete_password(parent, action_description: str) -> bool:
    """
    Prompts for the delete password. Returns True only if the person entered
    it correctly. Shows its own warning on a wrong entry.
    """
    text, ok = QInputDialog.getText(
        parent,
        "Password Required",
        f"Enter the password to {action_description}:",
        QLineEdit.Password,
    )
    if not ok:
        return False
    if text != DELETE_PASSWORD:
        QMessageBox.warning(parent, "Incorrect Password", "That password is not correct. Nothing was deleted.")
        return False
    return True


def rupees(value) -> str:
    """Format a number as an Indian-style currency string, e.g. 1,499.00"""
    try:
        value = float(value)
    except (TypeError, ValueError):
        value = 0.0
    is_negative = value < 0
    value = abs(value)
    s = f"{value:,.2f}"
    return f"{'-' if is_negative else ''}\u20b9{s}"


def make_stat_card(title: str, value: str, subtitle: str = "") -> QFrame:
    """A small KPI card used on the Statistics tab."""
    card = QFrame()
    card.setProperty("role", "card")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(4)

    title_lbl = QLabel(title.upper())
    title_lbl.setProperty("role", "subheading")
    layout.addWidget(title_lbl)

    value_lbl = QLabel(value)
    value_lbl.setProperty("role", "total")
    layout.addWidget(value_lbl)

    if subtitle:
        sub_lbl = QLabel(subtitle)
        sub_lbl.setStyleSheet("color: #6c757d; font-size: 11px;")
        layout.addWidget(sub_lbl)

    card.value_label = value_lbl  # allow callers to update later
    return card


def make_heading(text: str, subtitle: str = None) -> QWidget:
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(2)
    h = QLabel(text)
    h.setProperty("role", "heading")
    v.addWidget(h)
    if subtitle:
        s = QLabel(subtitle)
        s.setProperty("role", "subheading")
        v.addWidget(s)
    return box


class EditableSearchCombo(QComboBox):
    """
    A QComboBox that is editable and offers live filtering / autocomplete
    over whatever items you set with set_items(). This is the "recommend
    existing items as you type" behaviour used for barcode-less items.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        completer = QCompleter(self)
        completer.setCaseSensitivity(Qt.CaseInsensitive)
        completer.setCompletionMode(QCompleter.PopupCompletion)
        completer.setFilterMode(Qt.MatchContains)
        self.setCompleter(completer)

    def set_items(self, items):
        """items: list of strings"""
        current_text = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems(items)
        self.setCurrentText(current_text)
        self.blockSignals(False)
        self.completer().setModel(self.model())


def divider() -> QFrame:
    line = QFrame()
    line.setProperty("role", "divider")
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    return line
