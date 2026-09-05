// sales.js -- bill history with quick date-range filters and CSV export.
import { api, toast, rupees } from "./api.js";
import { openReceipt } from "./receipt.js";

const $ = (id) => document.getElementById(id);
let lastResults = [];

export async function initSales() {
  $("salesQuickRange").addEventListener("change", refreshSales);
  $("salesSearch").addEventListener("input", debounce(refreshSales, 300));
  $("exportCsvBtn").addEventListener("click", exportCsv);
  await refreshSales();
}

export async function refreshSales() {
  const range = $("salesQuickRange").value;
  const { from, to } = rangeToDates(range);
  const search = $("salesSearch").value.trim();
  lastResults = await api.searchBills({ date_from: from, date_to: to, search });
  const body = $("salesBody");
  body.innerHTML = "";
  lastResults.forEach(b => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(b.bill_no)}</td>
      <td>${(b.bill_date || "").slice(0, 16)}</td>
      <td>${esc(b.customer_name || "Walk-in")}</td>
      <td>${b.piece_count ?? "-"}</td>
      <td>${rupees(b.total)}</td>
      <td><button class="btn-link" data-act="view">View</button> <button class="btn-link" data-act="del">🗑</button></td>
    `;
    tr.querySelector('[data-act="view"]').onclick = async () => {
      const { bill, items } = await api.getBill(b.id);
      openReceipt(bill, items);
    };
    tr.querySelector('[data-act="del"]').onclick = async () => {
      const pw = prompt("Enter delete password to delete this bill (stock will be restored):");
      if (pw === null) return;
      try {
        await api.deleteBill(b.id, pw);
        toast("Bill deleted");
        refreshSales();
      } catch (e) { toast(e.message, true); }
    };
    body.appendChild(tr);
  });
}

function rangeToDates(range) {
  const today = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  if (range === "today") return { from: iso(today), to: iso(today) };
  if (range === "7") { const d = new Date(today); d.setDate(d.getDate() - 6); return { from: iso(d), to: iso(today) }; }
  if (range === "30") { const d = new Date(today); d.setDate(d.getDate() - 29); return { from: iso(d), to: iso(today) }; }
  if (range === "month") { const d = new Date(today.getFullYear(), today.getMonth(), 1); return { from: iso(d), to: iso(today) }; }
  return { from: undefined, to: undefined };
}

function exportCsv() {
  if (lastResults.length === 0) { toast("Nothing to export", true); return; }
  const header = ["Bill No", "Date", "Customer", "Phone", "Pieces", "Subtotal", "Discount", "Total", "Payment Mode"];
  const rows = lastResults.map(b => [
    b.bill_no, b.bill_date, b.customer_name || "Walk-in", b.customer_phone || "",
    b.piece_count ?? "", b.subtotal, b.discount_amount, b.total, b.payment_mode,
  ]);
  const csv = [header, ...rows].map(r => r.map(csvCell).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `sales_export_${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

function csvCell(v) {
  const s = String(v ?? "");
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
