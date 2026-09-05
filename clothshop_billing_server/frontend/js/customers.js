// customers.js
import { api, toast, rupees } from "./api.js";

let selectedCustomerId = null;
const $ = (id) => document.getElementById(id);

export async function initCustomers() {
  $("custSearchInput").addEventListener("input", debounce(refreshList, 250));
  $("saveCustBtn").addEventListener("click", saveCustomer);
  $("deleteCustBtn").addEventListener("click", deleteCustomer);
  $("addWishBtn").addEventListener("click", addWishlist);
  await refreshList();
}

export async function refreshCustomersTab() {
  await refreshList();
  if (selectedCustomerId) await selectCustomer(selectedCustomerId);
}

async function refreshList() {
  const text = $("custSearchInput").value.trim();
  const customers = await api.searchCustomers(text);
  const list = $("customerList");
  list.innerHTML = "";
  customers.forEach(c => {
    const li = document.createElement("li");
    li.className = c.id === selectedCustomerId ? "selected" : "";
    li.innerHTML = `<span>${esc(c.name)}${c.phone ? " · " + esc(c.phone) : ""}</span>`;
    li.onclick = () => selectCustomer(c.id);
    list.appendChild(li);
  });
}

async function selectCustomer(id) {
  selectedCustomerId = id;
  await refreshList();
  const c = await api.getCustomer(id);
  if (!c) return;
  $("custEmptyMsg").classList.add("hidden");
  $("custDetailCard").classList.remove("hidden");
  $("detName").value = c.name || "";
  $("detPhone").value = c.phone || "";
  $("detAddress").value = c.address || "";
  $("detNotes").value = c.notes || "";
  $("custStatsLine").textContent = `${c.visit_count} visit(s), ${rupees(c.total_spent)} total spent`;

  const history = await api.getCustomerHistory(id);
  const hb = $("custHistoryBody");
  hb.innerHTML = "";
  history.forEach(b => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${esc(b.bill_no)}</td><td>${(b.bill_date || "").slice(0, 10)}</td><td>${rupees(b.total)}</td>
      <td><button class="btn-link" data-act="del">🗑</button></td>`;
    tr.querySelector('[data-act="del"]').onclick = async () => {
      const pw = prompt("Enter delete password to remove this bill (stock will be restored):");
      if (pw === null) return;
      try {
        await api.deleteBill(b.id, pw);
        toast("Bill deleted");
        selectCustomer(id);
      } catch (e) { toast(e.message, true); }
    };
    hb.appendChild(tr);
  });

  const wishlist = await api.getWishlist(id);
  const wl = $("wishList");
  wl.innerHTML = "";
  wishlist.forEach(w => {
    const li = document.createElement("li");
    li.innerHTML = `<span style="${w.fulfilled ? "text-decoration:line-through;color:var(--muted);" : ""}">${esc(w.item_description)}</span>
      <button class="btn-link" data-act="toggle">${w.fulfilled ? "Undo" : "Got it ✓"}</button>`;
    li.querySelector('[data-act="toggle"]').onclick = async () => {
      await api.setWishlistFulfilled(w.id, w.fulfilled ? 0 : 1);
      selectCustomer(id);
    };
    wl.appendChild(li);
  });
}

async function saveCustomer() {
  if (!selectedCustomerId) return;
  const name = $("detName").value.trim();
  if (!name) { toast("Name is required", true); return; }
  await api.updateCustomer(selectedCustomerId, {
    name, phone: $("detPhone").value.trim(),
    address: $("detAddress").value.trim(), notes: $("detNotes").value.trim(),
  });
  toast("Saved");
  await refreshList();
}

async function deleteCustomer() {
  if (!selectedCustomerId) return;
  if (!confirm("Delete this customer? Their past bills stay on record as walk-in sales.")) return;
  const pw = prompt("Enter delete password:");
  if (pw === null) return;
  try {
    await api.deleteCustomer(selectedCustomerId, pw);
    toast("Customer deleted");
    selectedCustomerId = null;
    $("custDetailCard").classList.add("hidden");
    $("custEmptyMsg").classList.remove("hidden");
    await refreshList();
  } catch (e) { toast(e.message, true); }
}

async function addWishlist() {
  if (!selectedCustomerId) return;
  const desc = $("wishInput").value.trim();
  if (!desc) return;
  await api.addWishlist(selectedCustomerId, desc);
  $("wishInput").value = "";
  selectCustomer(selectedCustomerId);
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
