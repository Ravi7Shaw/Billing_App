"""Local desktop session permissions, shared by dashboards and billing commands."""
import hashlib
import hmac
import inspect
from datetime import date

from money import money, line_amount, sum_money
from offers import offer_taxes


# Store a verifier rather than the password. This is an application access gate;
# users who can edit the program/database need separate OS account restrictions.
_ADMIN_DIGEST = "2a28a012069a3b1157afe78c07537a6416f628b3987d2ae2d57f4c7d9c93764c"


def verify_admin_password(password):
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), b"billing-desktop-admin-v1", 200000
    ).hex()
    return hmac.compare_digest(digest, _ADMIN_DIGEST)


class AccessSession:
    def __init__(self):
        self._admin = False
        self.receipt_ids = set()

    @property
    def is_admin(self):
        return self._admin

    def login(self, password):
        if not verify_admin_password(password):
            return False
        self._admin = True
        self.receipt_ids.clear()
        return True

    def logout(self):
        self._admin = False
        self.receipt_ids.clear()


class RoleDatabase:
    """Check permissions when commands run, including callbacks saved before logout."""
    _EMPLOYEE_METHODS = frozenset({
        "get_categories", "get_subtypes", "get_items", "get_item_by_barcode",
        "get_item_by_id", "find_item_by_name", "get_customer_by_phone",
        "add_customer", "quote_offers", "get_customer_phone_suggestions",
    })

    def __init__(self, database, session):
        self._database = database
        self.session = session

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        target = getattr(self._database, name)
        if not callable(target):
            if not self.session.is_admin:
                raise PermissionError("Admin access is required.")
            return target

        def checked(*args, **kwargs):
            if not self.session.is_admin and name not in self._EMPLOYEE_METHODS:
                raise PermissionError("Admin access is required.")
            return target(*args, **kwargs)
        return checked

    def validate_employee_cart(self, cart, paid_now, bill_date):
        if self.session.is_admin:
            return
        lines = []
        net_lines = []
        active_categories = {c["id"] for c in self._database.get_categories()}
        offers = self._database.quote_offers(cart)
        for row, offer in zip(cart, offers):
            item = self._database.get_item_by_id(row["item_id"])
            if not item or not item["active"] or item["category_id"] not in active_categories:
                raise PermissionError("Employees can only bill active catalog items.")
            qty = row["qty"]
            if qty <= 0 or int(qty) != qty:
                raise PermissionError("Enter a whole quantity greater than zero.")
            amount = line_amount(qty, money(item["rate"]))
            if (money(row["rate"]) != money(item["rate"])
                    or money(row["amount"]) != amount
                    or float(row["gst_rate"]) != float(item["gst_rate"])
                    or money(row["discount"]) != 0):
                raise PermissionError("Only admins can change prices, GST or discounts. Refresh the bill if catalog prices changed.")
            if (money(row.get('offer_discount',0)) != offer['offer_discount']
                    or row.get('offer_id') != offer['offer_id']):
                raise PermissionError('Offers changed. Click Refresh offers and check the new total.')
            net = money(amount-offer['offer_discount'])
            lines.append(net)
            net_lines.append(dict(net=net,gst_rate=item["gst_rate"],offer_id=offer["offer_id"]))
        total = sum_money(lines + offer_taxes(net_lines))
        if money(paid_now) != total:
            raise PermissionError("Employees must collect full payment. Partial payment and credit require an admin.")
        if bill_date != date.today().isoformat():
            raise PermissionError("Only admins can change the bill date.")

    def save_bill(self, *args, **kwargs):
        if not self.session.is_admin:
            bound = inspect.signature(self._database.save_bill).bind(*args, **kwargs)
            bound.apply_defaults()
            values = bound.arguments
            # Enforce canonical pricing in the same transaction that saves the bill.
            values['employee_pricing'] = True
            try:
                result = self._database.save_bill(**values)
            except ValueError as exc:
                raise PermissionError(str(exc)) from exc
            self.session.receipt_ids.add(result[0])
            return result
        return self._database.save_bill(*args, **kwargs)

    def get_bill(self, bill_id):
        if not self.session.is_admin and bill_id not in self.session.receipt_ids:
            raise PermissionError("Sales history requires admin access.")
        return self._database.get_bill(bill_id)
