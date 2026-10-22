"""Keep initial desktop windows inside the current screen's usable area."""
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QScrollArea, QLayout


def fit_window(widget, width, height):
    screen = widget.parentWidget().screen() if widget.parentWidget() else QApplication.primaryScreen()
    if screen is None:
        widget.resize(width, height)
        return
    area = screen.availableGeometry()
    width, height = min(width, max(1, area.width()-32)), min(height, max(1, area.height()-64))
    widget.resize(width, height)
    widget.move(area.x()+(area.width()-width)//2, area.y()+(area.height()-height)//2)


def scroll_page(widget):
    """Keep a management tab usable when its forms exceed the screen dimensions."""
    content = QWidget()
    content.setLayout(widget.layout())
    content.layout().setSizeConstraint(QLayout.SetMinimumSize)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.addWidget(scroll)
