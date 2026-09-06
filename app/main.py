"""
main.py
-------
Entry point for the Cloth Shop Billing System.

Run with:
    python main.py
"""

import os
import sys

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
)
from PySide6.QtGui import QIcon

from database import Database
from theme import STYLESHEET
from billing_tab import BillingTab
from inventory_tab import InventoryTab
from customers_tab import CustomersTab
from sales_tab import SalesTab
from status_tab import StatusTab
from stats_tab import StatsTab


def resource_path(relative_path):
    """Resolves a path to a bundled resource (like the app icon) so it
    works both when running from source and when packaged into a single
    .exe by PyInstaller, which unpacks bundled files to a temp folder
    referenced by sys._MEIPASS at runtime."""
    base_path = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, relative_path)


APP_ICON_PATH = resource_path(os.path.join("assets", "icon.ico"))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Cloth Shop Billing System")
        self.resize(1300, 820)
        if os.path.exists(APP_ICON_PATH):
            self.setWindowIcon(QIcon(APP_ICON_PATH))

        self.db = Database()

        central = QWidget()
        central.setObjectName("centralWidget")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(central)

        self.tabs = QTabWidget()
        layout.addWidget(self.tabs)

        self.status_tab = StatusTab()
        self.customers_tab = CustomersTab(self.db)
        self.sales_tab = SalesTab(self.db)
        self.stats_tab = StatsTab(self.db)
        self.inventory_tab = InventoryTab(
            self.db, on_catalog_changed=self._on_catalog_changed
        )
        self.billing_tab = BillingTab(self.db, on_bill_saved=self._on_bill_saved)

        self.tabs.addTab(self.billing_tab, "New Bill")
        self.tabs.addTab(self.inventory_tab, "Inventory")
        self.tabs.addTab(self.customers_tab, "Customers")
        self.tabs.addTab(self.sales_tab, "Sales History")
        self.tabs.addTab(self.stats_tab, "Statistics")
        self.tabs.addTab(self.status_tab, "Application Status")

        self.tabs.currentChanged.connect(self._on_tab_changed)

    def _on_catalog_changed(self):
        self.billing_tab.refresh_catalog()

    def closeEvent(self, event):
        self.status_tab.shutdown_server()
        event.accept()

    def _on_bill_saved(self):
        self.sales_tab.refresh()
        self.stats_tab.refresh()
        self.customers_tab._refresh_customer_list()

    def _on_tab_changed(self, index):
        widget = self.tabs.widget(index)
        if widget is self.sales_tab:
            self.sales_tab.refresh()
        elif widget is self.stats_tab:
            self.stats_tab.refresh()
        elif widget is self.customers_tab:
            self.customers_tab._refresh_customer_list()


def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(STYLESHEET)
    app.setApplicationName("Cloth Shop Billing System")
    if os.path.exists(APP_ICON_PATH):
        app.setWindowIcon(QIcon(APP_ICON_PATH))
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
