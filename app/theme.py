"""
theme.py
--------
A single, clean stylesheet for the whole app so every tab looks consistent.
Kept in one place so the "look and feel" can be tweaked without touching
business logic.
"""

COLORS = {
    "bg": "#f4f5f7",
    "surface": "#ffffff",
    "primary": "#2f6f4f",       # deep green - cloth/retail friendly, calm
    "primary_dark": "#24563e",
    "primary_light": "#e7f2ec",
    "accent": "#c77b30",        # warm accent for totals / highlights
    "danger": "#c0392b",
    "text": "#212529",
    "muted": "#6c757d",
    "border": "#dcdfe3",
}

STYLESHEET = f"""
* {{
    font-family: 'Segoe UI', 'Inter', 'Helvetica Neue', Arial, sans-serif;
    font-size: 13px;
    color: {COLORS['text']};
}}

QMainWindow, QWidget#centralWidget {{
    background: {COLORS['bg']};
}}

QWidget {{
    background: transparent;
}}

QTabWidget::pane {{
    border: 1px solid {COLORS['border']};
    background: {COLORS['bg']};
    border-radius: 6px;
    top: -1px;
}}

QTabBar::tab {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-bottom: none;
    padding: 10px 22px;
    margin-right: 3px;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    font-weight: 600;
    color: {COLORS['muted']};
}}

QTabBar::tab:selected {{
    background: {COLORS['primary']};
    color: white;
}}

QTabBar::tab:hover:!selected {{
    background: {COLORS['primary_light']};
    color: {COLORS['primary_dark']};
}}

QGroupBox {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
    font-weight: 600;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: {COLORS['primary_dark']};
}}

QLabel[role="heading"] {{
    font-size: 18px;
    font-weight: 700;
    color: {COLORS['primary_dark']};
}}

QLabel[role="subheading"] {{
    font-size: 12px;
    color: {COLORS['muted']};
}}

QLabel[role="total"] {{
    font-size: 22px;
    font-weight: 800;
    color: {COLORS['accent']};
}}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QTextEdit {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 22px;
    selection-background-color: {COLORS['primary_light']};
    selection-color: {COLORS['text']};
}}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QDateEdit:focus, QTextEdit:focus {{
    border: 1.5px solid {COLORS['primary']};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

/* Dialogs (Add/Edit Item, etc.) don't inherit the main window's background,
   so without this they fall back to the OS theme -- black on a dark-mode
   system. Force the same light background everywhere. */
QDialog {{
    background: {COLORS['bg']};
}}

/* Popup lists -- QComboBox dropdowns and the QCompleter suggestion popup
   used for item-name autocomplete -- are separate top-level windows and
   have the same black-on-dark-mode problem unless styled explicitly. */
QComboBox QAbstractItemView, QListView, QAbstractItemView {{
    background: {COLORS['surface']};
    color: {COLORS['text']};
    border: 1px solid {COLORS['border']};
    outline: none;
    selection-background-color: {COLORS['primary_light']};
    selection-color: {COLORS['text']};
}}

QPushButton {{
    background: {COLORS['primary']};
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}}

QPushButton:hover {{
    background: {COLORS['primary_dark']};
}}

QPushButton:pressed {{
    background: #1a3f2c;
}}

QPushButton:disabled {{
    background: #b7c2bc;
    color: #eef1ef;
}}

QPushButton[role="secondary"] {{
    background: {COLORS['surface']};
    color: {COLORS['primary_dark']};
    border: 1px solid {COLORS['primary']};
}}

QPushButton[role="secondary"]:hover {{
    background: {COLORS['primary_light']};
}}

QPushButton[role="danger"] {{
    background: {COLORS['danger']};
}}

QPushButton[role="danger"]:hover {{
    background: #922b21;
}}

QPushButton[role="link"] {{
    background: transparent;
    color: {COLORS['primary']};
    text-decoration: underline;
    padding: 4px;
    font-weight: 600;
    border-radius: 4px;
}}

QPushButton[role="link"]:hover {{
    background: {COLORS['primary_light']};
}}

/* Buttons embedded inside table cells (Edit/Delete/Remove) use the same
   colors as their role, but need shorter padding -- the default padding
   makes the button taller than a normal table row, which was causing Qt
   to squeeze/clip the button and render it looking corrupted. */
QPushButton[compact="true"] {{
    padding: 3px 10px;
    font-weight: 600;
    border-radius: 5px;
}}

QTableWidget {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
    gridline-color: {COLORS['border']};
    selection-background-color: {COLORS['primary_light']};
    selection-color: {COLORS['text']};
    alternate-background-color: #fafbfc;
}}

QHeaderView::section {{
    background: {COLORS['primary_light']};
    color: {COLORS['primary_dark']};
    padding: 8px;
    border: none;
    border-bottom: 2px solid {COLORS['primary']};
    font-weight: 700;
}}

QTableWidget::item {{
    padding: 4px;
}}

QListWidget {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 6px;
}}

QListWidget::item {{
    padding: 8px;
    border-bottom: 1px solid {COLORS['border']};
}}

QListWidget::item:selected {{
    background: {COLORS['primary']};
    color: white;
    border-radius: 4px;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
}}

QScrollBar::handle:vertical {{
    background: #c4c9cf;
    border-radius: 5px;
    min-height: 24px;
}}

QScrollBar::handle:vertical:hover {{
    background: #a7adb5;
}}

QFrame[role="card"] {{
    background: {COLORS['surface']};
    border: 1px solid {COLORS['border']};
    border-radius: 10px;
}}

QFrame[role="divider"] {{
    background: {COLORS['border']};
    max-height: 1px;
}}

QToolTip {{
    background: {COLORS['primary_dark']};
    color: white;
    border: none;
    padding: 4px 8px;
    border-radius: 4px;
}}
"""
