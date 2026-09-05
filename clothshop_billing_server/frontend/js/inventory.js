// inventory.js -- Categories -> Brands/Styles -> Items, same cascade as
// the desktop app's Inventory tab.
import { api, toast, rupees } from "./api.js";

let selectedCategoryId = null;
let selectedSubtypeId = null;

const $ = (id) => document.getElementById(id);

export async function initInventory() {
  $("addCategoryBtn").addEventListener("click", addCategory);
  $("addSubtypeBtn").addEventListener("click", addSubtype);
  $("addItemInvBtn").addEventListener("click", () => openItemDialog(null));
  $("itemSearchInv").addEventListener("input", debounce(refreshItems, 300));
  await refreshCategories();
}

async function refreshCategories() {
  const cats = await api.getCategories();
  const list = $("categoryList");
  list.innerHTML = "";
  cats.forEach(c => {
    const li = document.createElement("li");
    li.className = c.id === selectedCategoryId ? "selected" : "";
    li.innerHTML = `<span>${esc(c.name)}</span>`;
    const actions = document.createElement("span");
    actions.innerHTML = `<button class="btn-link" data-act="rename">✎</button><button class="btn-link" data-act="del">🗑</button>`;
    actions.querySelector('[data-act="rename"]').onclick = (e) => { e.stopPropagation(); renameCategory(c); };
    actions.querySelector('[data-act="del"]').onclick = (e) => { e.stopPropagation(); deleteCategory(c); };
    li.appendChild(actions);
    li.onclick = () => selectCategory(c.id);
    list.appendChild(li);
  });
  if (!selectedCategoryId && cats.length) selectedCategoryId = cats[0].id;
  if (selectedCategoryId) await selectCategory(selectedCategoryId);
}

async function selectCategory(id) {
  selectedCategoryId = id;
  selectedSubtypeId = null;
  await refreshCategories2Highlight();
  await refreshSubtypes();
  await refreshItems();
}

async function refreshCategories2Highlight() {
  document.querySelectorAll("#categoryList li").forEach((li, i) => {});
  // simplest: just re-render category list to update highlight
  const cats = await api.getCategories();
  const list = $("categoryList");
  [...list.children].forEach((li, i) => {
    li.classList.toggle("selected", cats[i] && cats[i].id === selectedCategoryId);
  });
}

async function addCategory() {
  const name = prompt("New category name:");
  if (!name) return;
  await api.addCategory(name.trim());
  toast("Category added");
  await refreshCategories();
}

async function renameCategory(c) {
  const name = prompt("Rename category:", c.name);
  if (!name || name.trim() === c.name) return;
  await api.renameCategory(c.id, name.trim());
  await refreshCategories();
}

async function deleteCategory(c) {
  if (!confirm(`Delete category "${c.name}"? This only works if it has no items.`)) return;
  try {
    await api.deleteCategory(c.id);
    toast("Category deleted");
    if (selectedCategoryId === c.id) selectedCategoryId = null;
    await refreshCategories();
  } catch (e) {
    toast(e.message, true);
  }
}

async function refreshSubtypes() {
  const list = $("subtypeList");
  list.innerHTML = "";
  if (!selectedCategoryId) return;
  const subs = await api.getSubtypes(selectedCategoryId);
  subs.forEach(s => {
    const li = document.createElement("li");
    li.className = s.id === selectedSubtypeId ? "selected" : "";
    li.innerHTML = `<span>${esc(s.name)}</span>`;
    const actions = document.createElement("span");
    actions.innerHTML = `<button class="btn-link" data-act="rename">✎</button><button class="btn-link" data-act="del">🗑</button>`;
    actions.querySelector('[data-act="rename"]').onclick = (e) => { e.stopPropagation(); renameSubtype(s); };
    actions.querySelector('[data-act="del"]').onclick = (e) => { e.stopPropagation(); deleteSubtype(s); };
    li.appendChild(actions);
    li.onclick = () => { selectedSubtypeId = (selectedSubtypeId === s.id ? null : s.id); refreshSubtypes(); refreshItems(); };
    list.appendChild(li);
  });
}

async function addSubtype() {
  if (!selectedCategoryId) { toast("Select a category first", true); return; }
  const name = prompt("New brand/style name:");
  if (!name) return;
  await api.addSubtype(selectedCategoryId, name.trim());
  await refreshSubtypes();
}

async function renameSubtype(s) {
  const name = prompt("Rename:", s.name);
  if (!name || name.trim() === s.name) return;
  await api.renameSubtype(s.id, name.trim());
  await refreshSubtypes();
}

async function deleteSubtype(s) {
  if (!confirm(`Delete "${s.name}"?`)) return;
  await api.deleteSubtype(s.id);
  if (selectedSubtypeId === s.id) selectedSubtypeId = null;
  await refreshSubtypes();
  await refreshItems();
}

async function refreshItems() {
  const body = $("invItemsBody");
  body.innerHTML = "";
  if (!selectedCategoryId) return;
  const search = $("itemSearchInv").value.trim();
  const items = await api.getItems({ category_id: selectedCategoryId, subtype_id: selectedSubtypeId || undefined, search });
  items.forEach(it => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${esc(it.name)}</td>
      <td>${esc(it.size || "-")} / ${esc(it.color || "-")}</td>
      <td>${rupees(it.rate)}</td>
      <td>${it.stock_qty}</td>
      <td><button class="btn-link" data-act="edit">Edit</button> <button class="btn-link" data-act="del">Delete</button></td>
    `;
    tr.querySelector('[data-act="edit"]').onclick = () => openItemDialog(it);
    tr.querySelector('[data-act="del"]').onclick = async () => {
      if (!confirm(`Remove "${it.name}" from the active catalog?`)) return;
      await api.deleteItem(it.id);
      toast("Item removed");
      await refreshItems();
    };
    body.appendChild(tr);
  });
}

async function openItemDialog(item) {
  const name = prompt("Item name:", item?.name || "");
  if (!name) return;
  const rateStr = prompt("Rate (₹):", item?.rate ?? "");
  const rate = parseFloat(rateStr);
  if (isNaN(rate) || rate < 0) { toast("Invalid rate", true); return; }
  const size = prompt("Size (optional):", item?.size || "") || "";
  const color = prompt("Color (optional):", item?.color || "") || "";
  const barcode = prompt("Barcode (optional):", item?.barcode || "") || "";
  const stockStr = prompt("Stock quantity:", item?.stock_qty ?? "0");
  const stock = parseInt(stockStr, 10) || 0;

  const payload = {
    name: name.trim(), category_id: selectedCategoryId, subtype_id: selectedSubtypeId,
    barcode: barcode.trim(), size: size.trim(), color: color.trim(), rate, stock_qty: stock, active: 1,
  };

  if (item) {
    await api.updateItem(item.id, payload);
    toast("Item updated");
  } else {
    await api.addItem(payload);
    toast("Item added");
  }
  await refreshItems();
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
