"""
main.py
-------
Entry point for the Cloth Shop Billing System.
"""

import os
import sys
import time

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QTabWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QInputDialog,
    QLineEdit,
    QComboBox,
    QMessageBox,
)
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt
from window_utils import fit_window, scroll_page
from maintenance import backup_now, UpdateDialog

from database import Database
from access import AccessSession, RoleDatabase, make_accountant_credential, ROLE_ADMIN, ROLE_ACCOUNTANT
from theme import STYLESHEET
from billing_tab import BillingTab
from commerce_ui import PurchasesTab, ChartAccountsTab
from citizen_tab import CitizenLabelTab as LabelTab
from inventory_tab import InventoryTab
from customers_tab import CustomersTab
from sales_tab import SalesTab
from stats_tab import StatsTab
from balances_tab import BalancesTab
from expenses_tab import ExpensesTab
from offers_tab import OffersTab
from exchanges_tab import ExchangesTab
from employees_tab import EmployeesTab
from exchanges_tab import ExchangeCart


def resource_path(relative_path):
    """Resolve a bundled resource path for source runs and PyInstaller."""
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

        self.session = AccessSession()
        self.raw_db = Database()
        self.db = RoleDatabase(self.raw_db, self.session)

        central = QWidget()
        central.setObjectName("centralWidget")
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        self.setCentralWidget(central)

        self.tabs = QTabWidget()
        self.tabs.setUsesScrollButtons(True)
        layout.addWidget(self.tabs)
        controls = QWidget()
        access_bar = QHBoxLayout(controls)
        access_bar.setContentsMargins(4, 0, 4, 0)
        access_bar.setSpacing(4)
        self.admin_button = QPushButton("Admin")
        self.admin_button.clicked.connect(self._login_admin)
        self.accountant_button = QPushButton("Accountant")
        self.accountant_button.setToolTip("Purchases and Chart of Accounts only")
        self.accountant_button.clicked.connect(self._login_accountant)
        self._accountant_failures = 0
        self._accountant_locked_until = 0.0
        self.logout_button = QPushButton("Lock")
        self.logout_button.setToolTip("Lock admin and return to employee billing")
        self.logout_button.clicked.connect(self._logout_admin)
        self.backup_button = QPushButton("Backup")
        self.backup_button.clicked.connect(lambda: backup_now(self, self.db))
        self.update_button = QPushButton("Update")
        self.update_button.clicked.connect(self._update_app)
        for button in (self.admin_button, self.accountant_button,
                       self.backup_button, self.update_button, self.logout_button):
            button.setProperty("compact", "true")
            access_bar.addWidget(button)
        self.tabs.setCornerWidget(controls, Qt.TopRightCorner)
        self.section_picker = QComboBox()
        self.section_picker.setMinimumWidth(140)
        self.section_picker.setStyleSheet("QComboBox { min-height: 16px; padding: 4px 8px; }")
        self.section_picker.setFixedHeight(30)
        self.section_picker.setMaximumWidth(230)
        self.section_picker.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.section_picker.setToolTip('Open any section')
        self.section_picker.currentIndexChanged.connect(self.tabs.setCurrentIndex)
        self.section_corner = QWidget()
        section_layout = QHBoxLayout(self.section_corner)
        section_layout.setContentsMargins(0, 2, 0, 2)
        section_layout.addWidget(self.section_picker)
        self.tabs.setCornerWidget(self.section_corner, Qt.TopLeftCorner)

        self.tabs.currentChanged.connect(self._on_tab_changed)
        self._build_dashboard()
        fit_window(self, 1300, 820)

    def _build_dashboard(self):
        self.tabs.blockSignals(True)
        while self.tabs.count():
            widget = self.tabs.widget(0)
            self.tabs.removeTab(0)
            widget.hide()
            widget.deleteLater()
        for name in ("inventory", "customers", "balances", "expenses", "sales", "stats", "status", "offers", "exchanges", "employees", "purchases", "accounts", "labels"):
            setattr(self, name + "_tab", None)
        self.billing_tab = None
        if self.session.is_accountant:
            self.purchases_tab = PurchasesTab(self.db, on_saved=self._on_catalog_changed)
            self.accounts_tab = ChartAccountsTab(self.db, on_changed=self._on_catalog_changed,
                on_purchase=lambda: self.tabs.setCurrentWidget(self.purchases_tab),
                can_edit_catalog=False)
            self.tabs.addTab(self.purchases_tab, "Purchases")
            self.tabs.addTab(self.accounts_tab, "Chart of Accounts")
            self.tabs.setCurrentIndex(0)
            self._finish_dashboard()
            return
        self.billing_tab = BillingTab(self.db, on_bill_saved=self._on_bill_saved)
        self.tabs.addTab(self.billing_tab, "New Bill")
        if self.session.is_admin:
            self.purchases_tab = PurchasesTab(self.db, on_saved=self._on_catalog_changed)
            self.accounts_tab = ChartAccountsTab(self.db, on_changed=self._on_catalog_changed,
                on_purchase=lambda: self.tabs.setCurrentWidget(self.purchases_tab),
                on_accountant_password=self._set_accountant_password)
            self.labels_tab = LabelTab(self.db, on_changed=self._on_catalog_changed)
            self.tabs.addTab(self.labels_tab, "Barcode Labels")
            self.tabs.addTab(self.purchases_tab, "Purchases")
            self.tabs.addTab(self.accounts_tab, "Chart of Accounts")
            self.customers_tab = CustomersTab(self.db, autoload=False)
            self.balances_tab = BalancesTab(self.db, autoload=False)
            self.expenses_tab = ExpensesTab(self.db, autoload=False)
            self.sales_tab = SalesTab(self.db, autoload=False)
            self.stats_tab = StatsTab(self.db, autoload=False)
            self.employees_tab = EmployeesTab(self.db, autoload=False)
            self.offers_tab = OffersTab(self.db, on_changed=self.billing_tab._refresh_offers, autoload=False)
            self.inventory_tab = InventoryTab(
                self.db, on_catalog_changed=self._on_catalog_changed
            )
            self.exchanges_tab = ExchangesTab(self.db, autoload=False, open_tab=self._open_work_tab,
                close_tab=self._close_work_tab)
            self.tabs.addTab(self.inventory_tab, "Inventory")
            self.tabs.addTab(self.customers_tab, "Customers")
            self.tabs.addTab(self.balances_tab, "Balances")
            self.tabs.addTab(self.expenses_tab, "Expenses")
            self.tabs.addTab(self.sales_tab, "Sales History")
            self.tabs.addTab(self.stats_tab, "Statistics")
            self.tabs.addTab(self.offers_tab, "Offers")
            self.tabs.addTab(self.exchanges_tab, "Exchanges")
            self.tabs.addTab(self.employees_tab, "Employees")

        for index in range(1, self.tabs.count()):
            widget = self.tabs.widget(index)
            if widget not in (self.stats_tab, self.purchases_tab, self.accounts_tab, self.labels_tab):
                scroll_page(widget)
        self._finish_dashboard()

    def _finish_dashboard(self):
        self.tabs.setCurrentIndex(0)
        self.tabs.blockSignals(False)
        self.section_picker.blockSignals(True)
        self.section_picker.clear()
        for index in range(self.tabs.count()):
            self.section_picker.addItem(self.tabs.tabText(index))
        self.section_picker.setCurrentIndex(0)
        self.section_picker.blockSignals(False)
        admin = self.session.is_admin
        accountant = self.session.is_accountant
        self.section_picker.setVisible(admin or accountant)
        self.section_corner.setVisible(admin or accountant)
        self.admin_button.setVisible(not admin)
        self.accountant_button.setVisible(not admin and not accountant)
        self.logout_button.setVisible(admin or accountant)
        self.backup_button.setVisible(admin)
        self.update_button.setVisible(admin)
        if admin:
            title = "Admin"
        elif accountant:
            title = "Accountant"
        else:
            title = "Employee"
        self.setWindowTitle("Cloth Shop Billing System — " + title)

    def _unfinished_work(self):
        """Return a description of unsaved work that a dashboard rebuild would discard."""
        items = []
        if self.billing_tab and self.billing_tab.cart:
            items.append("an unfinished bill")
        if self.purchases_tab and self.purchases_tab.lines:
            items.append("an unfinished purchase")
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if isinstance(widget, ExchangeCart) and not widget.completed and widget.cart:
                items.append("an unfinished exchange")
                break
        return items

    def _confirm_discard(self, action):
        items = self._unfinished_work()
        if not items:
            return True
        answer = QMessageBox.question(
            self, action, "This will discard " + " and ".join(items) + ". Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        return answer == QMessageBox.Yes

    def _login_admin(self):
        if self.session.is_admin:
            return
        if not self._confirm_discard("Switch dashboard"):
            return
        password, accepted = QInputDialog.getText(
            self, "Admin login", "Admin password:", QLineEdit.Password,
        )
        if not accepted:
            return
        if not self.session.login(password):
            QMessageBox.warning(self, "Incorrect password", "Admin access was not unlocked.")
            return
        self._build_dashboard()

    def _login_accountant(self):
        if self.session.is_accountant or self.session.is_admin:
            return
        if not self._confirm_discard("Switch dashboard"):
            return
        stored = self.raw_db.get_accountant_credential()
        if not stored:
            QMessageBox.information(
                self, "Accountant login not set up",
                "The admin has not set an accountant password yet. Log in as admin and use "
                "'Accountant password' to create one.",
            )
            return
        if time.monotonic() < self._accountant_locked_until:
            QMessageBox.warning(self, "Try again later", "Too many wrong passwords. Wait a minute and try again.")
            return
        password, accepted = QInputDialog.getText(
            self, "Accountant login", "Accountant password:", QLineEdit.Password,
        )
        if not accepted:
            return
        if not self.session.login_accountant(password, stored):
            self._accountant_failures += 1
            if self._accountant_failures >= 5:
                self._accountant_failures = 0
                self._accountant_locked_until = time.monotonic() + 60
            QMessageBox.warning(self, "Incorrect password", "Accountant access was not unlocked.")
            return
        self._accountant_failures = 0
        self._build_dashboard()

    def _set_accountant_password(self):
        if not self.session.is_admin:
            return
        first, ok = QInputDialog.getText(
            self, "Accountant password", "New accountant password (at least 6 characters):", QLineEdit.Password,
        )
        if not ok:
            return
        if len(first) < 6:
            QMessageBox.warning(self, "Too short", "Use at least 6 characters.")
            return
        second, ok = QInputDialog.getText(
            self, "Accountant password", "Type the new password again:", QLineEdit.Password,
        )
        if not ok:
            return
        if first != second:
            QMessageBox.warning(self, "Passwords differ", "The two entries did not match. Nothing was changed.")
            return
        self.raw_db.set_accountant_credential(make_accountant_credential(first))
        QMessageBox.information(self, "Saved", "Accountant password updated.")

    def _open_work_tab(self, widget, title):
        """Show a work-in-progress screen (such as an exchange) as its own tab."""
        scroll_page(widget)
        index = self.tabs.addTab(widget, title)
        self.tabs.setCurrentIndex(index)
        return widget

    def _close_work_tab(self, widget):
        index = self.tabs.indexOf(widget)
        if index >= 0:
            self.tabs.removeTab(index)
        widget.hide()
        widget.deleteLater()

    def _update_app(self):
        if not self.session.is_admin:
            return
        if (self.billing_tab and self.billing_tab.cart) or (self.purchases_tab and self.purchases_tab.lines):
            QMessageBox.information(self, "Finish the bill", "Complete or clear the current sale or purchase before updating.")
            return
        UpdateDialog(self.db, self).exec()

    def _logout_admin(self):
        # Clear privileged widgets and unfinished discounted/credit bills on lock.
        self.session.logout()
        self._build_dashboard()

    def _on_catalog_changed(self):
        if self.billing_tab:
            self.billing_tab.refresh_catalog()
        # A purchase or catalog edit changes stock everywhere it is shown.
        if self.purchases_tab:
            self.purchases_tab.refresh()
        if self.accounts_tab:
            self.accounts_tab.refresh()
        if self.session.is_admin and self.inventory_tab:
            self.inventory_tab._refresh_items()

    def _on_bill_saved(self):
        if not self.session.is_admin:
            return
        # Other screens refresh when opened. Billing must not wait for hidden
        # ledgers or charts, and each screen reads current committed data on entry.
        self._on_tab_changed(self.tabs.currentIndex())

    def _on_tab_changed(self, index):
        self.section_picker.blockSignals(True)
        self.section_picker.setCurrentIndex(index)
        self.section_picker.blockSignals(False)
        if self.session.is_accountant:
            widget = self.tabs.widget(index)
            if widget in (self.purchases_tab, self.accounts_tab):
                widget.refresh()
            return
        if not self.session.is_admin:
            return
        widget = self.tabs.widget(index)

        if widget in (self.purchases_tab, self.accounts_tab, self.labels_tab):
            widget.refresh()
        elif widget is self.sales_tab:
            self.sales_tab.refresh()
        elif widget is self.stats_tab:
            self.stats_tab.refresh()
        elif widget is self.customers_tab:
            self.customers_tab._refresh_customer_list()
        elif widget is self.balances_tab:
            self.balances_tab.refresh()
        elif widget is self.expenses_tab:
            self.expenses_tab.refresh()
        elif widget is self.employees_tab:
            self.employees_tab.refresh()
        elif widget is self.exchanges_tab:
            self.exchanges_tab.refresh()
        elif widget is self.offers_tab:
            self.offers_tab.refresh()
        elif widget is self.inventory_tab:
            self.inventory_tab._refresh_items()


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
