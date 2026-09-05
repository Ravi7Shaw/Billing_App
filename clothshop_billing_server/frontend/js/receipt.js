// receipt.js -- builds a plain-text receipt (mirrors the desktop app's
// receipt.py layout) and shows it in a printable modal.
import { rupees } from "./api.js";

const WIDTH = 42;

function line(ch = "-") { return ch.repeat(WIDTH); }
function center(text) {
  const pad = Math.max(0, Math.floor((WIDTH - text.length) / 2));
  return " ".repeat(pad) + text;
}
function twoCol(left, right) {
  const space = Math.max(1, WIDTH - left.length - right.length);
  return left + " ".repeat(space) + right;
}

export function buildReceiptText(bill, items) {
  const rows = [];
  rows.push(center("CLOTH SHOP"));
  rows.push(center("Thank you for shopping with us!"));
  rows.push(line());
  rows.push(twoCol("Bill No:", bill.bill_no));
  rows.push(twoCol("Date:", (bill.bill_date || "").slice(0, 16)));
  if (bill.customer_name) rows.push(twoCol("Customer:", bill.customer_name));
  if (bill.customer_phone) rows.push(twoCol("Phone:", bill.customer_phone));
  rows.push(line());
  items.forEach(it => {
    const name = it.item_name_snapshot.length > 22 ? it.item_name_snapshot.slice(0, 22) : it.item_name_snapshot;
    rows.push(name);
    rows.push(twoCol(`  ${it.quantity} x ${rupees(it.rate)}`, rupees(it.subtotal)));
  });
  rows.push(line());
  rows.push(twoCol("Subtotal:", rupees(bill.subtotal)));
  if (bill.discount_amount > 0) rows.push(twoCol("Discount:", "-" + rupees(bill.discount_amount)));
  rows.push(twoCol("TOTAL:", rupees(bill.total)));
  rows.push(twoCol("Payment:", bill.payment_mode || "Cash"));
  rows.push(line());
  rows.push(center("Exchange within 7 days with bill."));
  return rows.join("\n");
}

export function openReceipt(bill, items) {
  const text = buildReceiptText(bill, items);
  document.getElementById("receiptText").textContent = text;
  document.getElementById("receiptModal").classList.remove("hidden");
}

export function initReceiptModal() {
  document.getElementById("closeReceiptBtn").addEventListener("click", () => {
    document.getElementById("receiptModal").classList.add("hidden");
  });
  document.getElementById("printReceiptBtn").addEventListener("click", () => window.print());
}
