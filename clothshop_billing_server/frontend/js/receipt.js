// receipt.js -- A4 boxed GST invoice matching the desktop invoice layout.
import { rupees } from "./api.js";

const PAYMENT_VPA = "9008080213@ybl";
const SHOP_NAME = "BELLI APPERAL";
const SHOP_GSTIN = "29UZKPS8200M1Z5";
const SHOP_ADDRESS_1 = "Building No./Flat No.: 22, 1st Main Road";
const SHOP_ADDRESS_2 = "Mylasandara, Bengaluru Urban, Karnataka - 560059";
const SHOP_LOCATION = "Bengaluru, Karnataka";
const SHOP_PHONE = "6360086532";

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function money(value) {
  return rupees(Number(value) || 0);
}

function dateDisplay(value) {
  const s = String(value || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return s || "-";
  const [y, m, d] = s.split("-");
  return `${d}/${m}/${y}`;
}

function amountWords(value) {
  const n = Math.round((Number(value) || 0) * 100) / 100;
  const ones = ["Zero","One","Two","Three","Four","Five","Six","Seven","Eight","Nine","Ten","Eleven","Twelve","Thirteen","Fourteen","Fifteen","Sixteen","Seventeen","Eighteen","Nineteen"];
  const tens = ["","","Twenty","Thirty","Forty","Fifty","Sixty","Seventy","Eighty","Ninety"];
  const under100 = x => x < 20 ? ones[x] : tens[Math.floor(x / 10)] + (x % 10 ? " " + ones[x % 10] : "");
  const under1000 = x => x < 100 ? under100(x) : ones[Math.floor(x / 100)] + " Hundred" + (x % 100 ? " " + under100(x % 100) : "");
  const intPart = Math.floor(n);
  const paise = Math.round((n - intPart) * 100);
  let result = "";
  if (intPart >= 10000000) result += under100(Math.floor(intPart / 10000000)) + " Crore ";
  const lakh = Math.floor((intPart % 10000000) / 100000);
  if (lakh) result += under100(lakh) + " Lakh ";
  const thousand = Math.floor((intPart % 100000) / 1000);
  if (thousand) result += under100(thousand) + " Thousand ";
  const rest = intPart % 1000;
  if (rest) result += under1000(rest);
  if (!result.trim()) result = "Zero";
  result = result.trim() + " Rupees";
  if (paise) result += " and " + under100(paise) + " Paise";
  return result + " Only";
}

function paymentQrUrl(amount, billNo) {
  const params = new URLSearchParams({
    amount: (Number(amount) || 0).toFixed(2),
    bill_no: billNo || ""
  });
  return `/api/payment-qr?${params.toString()}`;
}

export function buildReceiptHtml(bill, items) {
  const total = Number(bill.total) || 0;
  const discount = Number(bill.discount_amount || 0);
  const taxable = Math.max(0, Number(bill.taxable_amount ?? bill.subtotal ?? 0) - discount);
  const gstTotal = Number(bill.gst_amount || 0) || Math.max(0, total - taxable);
  const gstRate = Number(bill.gst_rate || 5);
  const intraState = true;
  const cgst = intraState ? gstTotal / 2 : 0;
  const sgst = intraState ? gstTotal / 2 : 0;
  const igst = intraState ? 0 : gstTotal;
  const customerName = bill.customer_name || "-";
  const customerPhone = bill.customer_phone || "-";
  const customerAddress = bill.customer_address || "-";
  const paymentMode = bill.payment_mode || "Cash";
  const rows = Array.from({ length: Math.max(8, items.length) }, (_, i) => items[i] || null);

  const itemRows = rows.map((it, i) => {
    if (!it) return `<tr><td>${i + 1}</td><td>-</td><td></td><td>-</td><td></td><td></td><td></td><td></td><td></td><td></td></tr>`;
    const lineTax = Number(it.gst_amount || 0);
    const lineGstRate = Number(it.gst_rate ?? gstRate);
    const lineCgst = lineTax / 2;
    const lineSgst = lineTax / 2;
    const lineTotal = Number(it.subtotal || 0) + lineTax;
    return `<tr>
      <td>${i + 1}</td>
      <td>${esc(it.barcode || "-")}</td>
      <td class="desc">${esc(it.item_name_snapshot || it.name || "-")}</td>
      <td>${esc(it.hsn || "-")}</td>
      <td>${money(it.rate)}</td>
      <td>${Number(it.quantity || 0)}</td>
      <td>${money(it.subtotal)}</td>
      <td>${lineGstRate.toFixed(0)}%</td>
      <td>CGST ${money(lineCgst)}<br>SGST ${money(lineSgst)}</td>
      <td><strong>${money(lineTotal)}</strong></td>
    </tr>`;
  }).join("");

  return `<div class="invoice-page">
    <section class="invoice-header">
      <div class="shop-name">${SHOP_NAME}</div>
      <div>${SHOP_ADDRESS_1}</div>
      <div>${SHOP_ADDRESS_2}</div>
      <div class="shop-gstin">GSTIN: ${SHOP_GSTIN}</div>
      <div class="shop-meta">Location: ${SHOP_LOCATION} <span>|</span> Phone: ${SHOP_PHONE}</div>
    </section>

    <div class="invoice-title">TAX INVOICE</div>

    <section class="invoice-info">
      <div>
        <div><b>Invoice No:</b> ${esc(bill.bill_no)}</div>
        <div><b>Customer ID:</b> ${esc(bill.customer_id ?? "-")}</div>
        <div><b>Name:</b> ${esc(customerName)}</div>
        <div><b>Address:</b> ${esc(customerAddress)}</div>
        <div><b>Phone:</b> ${esc(customerPhone)}</div>
      </div>
      <div>
        <div><b>Date:</b> ${dateDisplay(bill.bill_date)}</div>
        <div><b>Type:</b> Retail Sale</div>
        <div><b>GSTIN:</b> -</div>
        <div><b>POS:</b> Karnataka (29)</div>
        <div><b>Payment Mode:</b> ${esc(paymentMode)}</div>
      </div>
    </section>

    <table class="invoice-items">
      <thead><tr>
        <th>Sr.</th><th>Product<br>ID</th><th>Product Description</th><th>HSN</th><th>MRP<br>(₹)</th><th>Qty</th><th>Taxable<br>Value (₹)</th><th>GST%</th><th>CGST ₹ &amp;<br>SGST ₹</th><th>Amount (₹)</th>
      </tr></thead>
      <tbody>${itemRows}</tbody>
      <tfoot><tr><td colspan="8"></td><td><b>Total</b></td><td><b>${money(total)}</b></td></tr></tfoot>
    </table>

    <section class="bottom-grid">
      <div class="summary-box">
        <div><b>Taxable Amount</b><span>${money(taxable)}</span></div>
        <div><b>Discount</b><span>${money(discount)}</span></div>
        <div><b>CGST (${(gstRate / 2).toFixed(1)}%)</b><span>${money(cgst)}</span></div>
        <div><b>SGST (${(gstRate / 2).toFixed(1)}%)</b><span>${money(sgst)}</span></div>
        <div><b>IGST (0.0%)</b><span>${money(igst)}</span></div>
        <div class="invoice-amount"><b>Invoice Amount</b><span>${money(total)}</span></div>
      </div>

      <div class="qr-box">
        <img src="${paymentQrUrl(total, bill.bill_no)}" alt="UPI payment QR code">
        <div class="scan">SCAN TO PAY</div>
        <div class="qr-amount">${money(total)}</div>
        <div>${PAYMENT_VPA}</div>
      </div>
    </section>

    <div class="words"><b>Amount in Words:</b> ${amountWords(total)}</div>
    <div class="payment-line"><b>Payment Mode:</b> ${esc(paymentMode)} <b>Paid:</b> ${money(bill.paid_amount ?? total)} <b>Balance:</b> ${money(Math.max(0, total - Number(bill.paid_amount ?? total)))} <b>Payment in favour of:</b> ${SHOP_NAME}</div>

    <section class="signatures">
      <div><div class="signature-line"></div><b>Accountant's Signature</b></div>
      <div><div class="signature-line"></div><b>Customer's Signature</b></div>
    </section>

    <div class="thank-you">Thank You. Visit Again.</div>
  </div>`;
}

export function openReceipt(bill, items) {
  const host = document.getElementById("receiptText");
  host.innerHTML = buildReceiptHtml(bill, items);
  document.getElementById("receiptModal").classList.remove("hidden");
}

export function initReceiptModal() {
  document.getElementById("closeReceiptBtn").addEventListener("click", () => {
    document.getElementById("receiptModal").classList.add("hidden");
  });
  document.getElementById("printReceiptBtn").addEventListener("click", () => window.print());
}

