// billing.js -- the "New Bill" screen: the mobile-billing core of the app.
import { api, toast, rupees } from "./api.js";
import { openReceipt } from "./receipt.js";

let cart = [];               // { item_id, name, category, qty, rate, amount, discount }
let categories = [];
let currentItemsByName = {}; // name -> item row, for the selected category/subtype
let matchedCustomerId = null;
let discountVisible = false;

const $ = (id) => document.getElementById(id);

export async function initBilling() {
    const today = getLocalDateISO();

  $("billDateInput").value = today;
  $("billDateInput").max = today;
  categories = await api.getCategories();
  const catSel = $("categorySelect");
  catSel.innerHTML = categories.map(c => `<option value="${c.id}">${escapeHtml(c.name)}</option>`).join("");
  catSel.addEventListener("change", onCategoryChanged);
  await onCategoryChanged();

  $("barcodeInput").addEventListener("keydown", (e) => { if (e.key === "Enter") onBarcodeScanned(); });
  $("addItemBtn").addEventListener("click", addManualItem);
  $("itemNameInput").addEventListener("input", onItemNameTyped);
  $("toggleDiscountBtn").addEventListener("click", toggleDiscount);
  $("completeBillBtn").addEventListener("click", completeBill);
  $("custPhone").addEventListener("blur", lookupCustomerByPhone);

  renderCart();
}

function getLocalDateISO() {
  const now = new Date();
  const offset = now.getTimezoneOffset();

  return new Date(
    now.getTime() - offset * 60 * 1000
  ).toISOString().slice(0, 10);
}

async function onCategoryChanged() {
  const catId = $("categorySelect").value;
  const subs = await api.getSubtypes(catId);
  const subSel = $("subtypeSelect");
  subSel.innerHTML = `<option value="">(none)</option>` + subs.map(s => `<option value="${s.id}">${escapeHtml(s.name)}</option>`).join("");
  subSel.onchange = refreshItemSuggestions;
  await refreshItemSuggestions();
}

async function refreshItemSuggestions() {
  const catId = $("categorySelect").value;
  const subId = $("subtypeSelect").value || undefined;
  const items = await api.getItems({ category_id: catId, subtype_id: subId });
  currentItemsByName = {};
  items.forEach(it => { currentItemsByName[it.name] = it; });
  $("itemNamesList").innerHTML = items.map(it => `<option value="${escapeHtml(it.name)}">`).join("");
}

function onItemNameTyped() {
  const name = $("itemNameInput").value;
  const match = currentItemsByName[name];
  if (match) {
    $("itemRateInput").value = match.rate;
    $("itemSizeInput").value = match.size || "";
    $("itemColorInput").value = match.color || "";
  }
}

async function onBarcodeScanned() {
  const code = $("barcodeInput").value.trim();
  if (!code) return;
  const statusEl = $("barcodeStatus");
  const result = await api.getItemByBarcode(code);
  if (result.found) {
    addToCart({
      item_id: result.item.id,
      name: result.item.name,
      category: result.item.category_name,
      qty: 1,
      rate: result.item.rate,
    });
    statusEl.textContent = `Added: ${result.item.name}`;
    statusEl.className = "small";
    $("barcodeInput").value = "";
    $("barcodeInput").focus();
  } else {
    statusEl.textContent = "Not found — assign it to a new item below.";
    statusEl.className = "small";
    statusEl.style.color = "var(--rust-dark)";
    $("itemBarcodeInput").value = code;
    $("itemNameInput").focus();
  }
}

async function addManualItem() {
  const categoryId = Number($("categorySelect").value);
  const subtypeId = $("subtypeSelect").value ? Number($("subtypeSelect").value) : null;
  const name = $("itemNameInput").value.trim();
  const rate = parseFloat($("itemRateInput").value);
  const qty = parseInt($("itemQtyInput").value, 10) || 1;
  const size = $("itemSizeInput").value.trim();
  const color = $("itemColorInput").value.trim();
  const barcode = $("itemBarcodeInput").value.trim();
  const categoryName = categories.find(c => c.id === categoryId)?.name;

  if (!name) { toast("Enter an item name", true); return; }
  if (isNaN(rate) || rate < 0) { toast("Enter a valid rate", true); return; }

  let item = await api.findItemByName(name, categoryId, subtypeId);
  let itemId;
  if (item) {
    itemId = item.id;
    await api.updateItem(itemId, {
      name, category_id: categoryId, subtype_id: subtypeId, barcode: barcode || item.barcode,
      size: size || item.size, color: color || item.color, rate, stock_qty: item.stock_qty, active: 1,
    });
  } else {
    const res = await api.addItem({ name, category_id: categoryId, subtype_id: subtypeId, barcode, size, color, rate, stock_qty: 0 });
    itemId = res.id;
  }

  addToCart({ item_id: itemId, name, category: categoryName, qty, rate });

  $("itemNameInput").value = "";
  $("itemSizeInput").value = "";
  $("itemColorInput").value = "";
  $("itemRateInput").value = "";
  $("itemQtyInput").value = "1";
  $("itemBarcodeInput").value = "";
  refreshItemSuggestions();
}

function addToCart({ item_id, name, category, qty, rate }) {
  const existing = cart.find(r => r.item_id === item_id);
  if (existing) {
    existing.qty += qty;
    existing.amount = existing.qty * existing.rate;
    if (existing.discount > existing.amount) existing.discount = existing.amount;
  } else {
    cart.push({ item_id, name, category, qty, rate, amount: qty * rate, discount: 0 });
  }
  renderCart();
}

function renderCart() {
  const body = $("cartBody");
  body.innerHTML = "";
  cart.forEach((row, idx) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${escapeHtml(row.name)}</td>
      <td><input type="number" min="1" step="1" value="${row.qty}" data-role="qty" data-idx="${idx}"></td>
      <td><input type="number" min="0" step="0.01" value="${row.rate}" data-role="rate" data-idx="${idx}"></td>
      <td class="discount-col ${discountVisible ? "" : "hidden"}"><input type="number" min="0" step="0.01" value="${row.discount}" data-role="discount" data-idx="${idx}"></td>
      <td>${rupees(row.amount - row.discount)}</td>
      <td><button class="remove-btn" data-idx="${idx}">✕</button></td>
    `;
    body.appendChild(tr);
  });
  $("emptyCartMsg").classList.toggle("hidden", cart.length > 0);
  $("cartTable").classList.toggle("hidden", cart.length === 0);

  body.querySelectorAll("input").forEach(inp => inp.addEventListener("change", onCartCellChanged));
  body.querySelectorAll(".remove-btn").forEach(btn => btn.addEventListener("click", (e) => {
    cart.splice(Number(e.target.dataset.idx), 1);
    renderCart();
  }));

  updateTotals();
}

function onCartCellChanged(e) {
  const idx = Number(e.target.dataset.idx);
  const role = e.target.dataset.role;
  const row = cart[idx];
  let val = parseFloat(e.target.value);
  if (isNaN(val) || val < 0) val = 0;
  if (role === "qty") row.qty = Math.max(1, Math.round(val));
  if (role === "rate") row.rate = val;
  if (role === "discount") row.discount = val;
  row.amount = row.qty * row.rate;
  if (row.discount > row.amount) row.discount = row.amount;
  renderCart();
}

function toggleDiscount() {
  discountVisible = !discountVisible;
  $("toggleDiscountBtn").textContent = (discountVisible ? "▾" : "▸") + " Discount";
  document.querySelectorAll(".discount-col").forEach(el => el.classList.toggle("hidden", !discountVisible));
  renderCart();
}

function subtotal() { return cart.reduce((s, r) => s + r.amount, 0); }
function totalDiscount() { return cart.reduce((s, r) => s + r.discount, 0); }
function grandTotal() { return Math.max(0, subtotal() - totalDiscount()); }

function updateTotals() {
  $("subtotalVal").textContent = rupees(subtotal());
  $("discountVal").textContent = rupees(totalDiscount());
  $("grandTotalVal").textContent = rupees(grandTotal());
}

async function lookupCustomerByPhone() {
  const phone = $("custPhone").value.trim();
  matchedCustomerId = null;
  $("custMatchNote").textContent = "";
  if (!phone) return;
  const c = await api.getCustomerByPhone(phone);
  if (c) {
    matchedCustomerId = c.id;
    $("custName").value = c.name;
    $("custMatchNote").textContent = `Existing customer: ${c.name}`;
  }
}

async function completeBill() {
  if (cart.length === 0) { toast("Cart is empty", true); return; }

  const phone = $("custPhone").value.trim();
  const name = $("custName").value.trim();
  let customerId = matchedCustomerId;

  if (phone || name) {
    if (!name) { toast("Enter the customer's name", true); return; }
    if (customerId) {
      await api.updateCustomer(customerId, { name, phone, address: "", notes: "" });
    } else {
      const res = await api.addCustomer({ name, phone });
      customerId = res.id;
    }
  }

  const sub = subtotal();
  const disc = totalDiscount();
  const total = grandTotal();
  const discountPercent = sub > 0 ? (disc / sub) * 100 : 0;

  const items = cart.map(r => ({
    item_id: r.item_id, name: r.name, category: r.category,
    quantity: r.qty, rate: r.rate, subtotal: r.amount - r.discount,
  }));

  const paymentMode = $("paymentModeSelect").value;
  const billDate = $("billDateInput").value;

  if (!billDate) {
    toast("Select a bill date", true);
    return;
  }


  const result = await api.saveBill({
    customer_id: customerId, items, subtotal: sub,
    discount_percent: discountPercent, discount_amount: disc,
    total, payment_mode: paymentMode,bill_date:billDate,
  });

  toast(`Bill ${result.bill_no} saved`);
  const { bill, items: savedItems } = await api.getBill(result.bill_id);
  openReceipt(bill, savedItems);

  cart = [];
  $("custPhone").value = "";
  $("custName").value = "";
  $("custMatchNote").textContent = "";
  matchedCustomerId = null;
  renderCart();

  document.dispatchEvent(new CustomEvent("bill-saved"));
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
