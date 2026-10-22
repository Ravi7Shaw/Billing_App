"""Shared two-decimal money calculations (round half up)."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def money(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite():
            raise ValueError("Amount must be finite.")
        return float(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ValueError("Invalid amount.") from exc


def line_amount(quantity, rate):
    return money(Decimal(str(quantity)) * Decimal(str(rate)))


def tax_amount(amount, rate):
    return money(Decimal(str(amount)) * Decimal(str(rate)) / 100)


def sum_money(values):
    return money(sum((Decimal(str(value)) for value in values), Decimal(0)))
