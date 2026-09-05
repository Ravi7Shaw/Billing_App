// api.js -- thin wrapper around fetch() for every backend endpoint.
// Everything is same-origin (the backend serves this frontend too), so no
// base URL or CORS handling is needed.

const DELETE_PASSWORD_HINT = "Ask the shop owner for the delete password";

async function request(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, opts);
  } catch (e) {
    setConnStatus(false);
    throw new Error("Can't reach the server. Check you're on the same Wi-Fi.");
  }
  setConnStatus(true);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = j.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  return res.json();
}

function setConnStatus(ok) {
  const el = document.getElementById("connStatus");
  if (!el) return;
  el.textContent = ok ? "● connected" : "● offline";
  el.className = "conn-status " + (ok ? "conn-ok" : "conn-bad");
}

export const api = {
  // categories
  getCategories: () => request("GET", "/api/categories"),
  addCategory: (name) => request("POST", "/api/categories", { name }),
  renameCategory: (id, name) => request("PUT", `/api/categories/${id}`, { name }),
  deleteCategory: (id) => request("DELETE", `/api/categories/${id}`),

  // subtypes
  getSubtypes: (categoryId) => request("GET", `/api/categories/${categoryId}/subtypes`),
  addSubtype: (categoryId, name) => request("POST", "/api/subtypes", { category_id: categoryId, name }),
  renameSubtype: (id, name) => request("PUT", `/api/subtypes/${id}`, { name }),
  deleteSubtype: (id) => request("DELETE", `/api/subtypes/${id}`),

  // items
  getItems: (params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") q.set(k, v); });
    return request("GET", `/api/items?${q.toString()}`);
  },
  getItemByBarcode: (code) => request("GET", `/api/items/barcode/${encodeURIComponent(code)}`),
  findItemByName: (name, categoryId, subtypeId) => {
    const q = new URLSearchParams({ name, category_id: categoryId });
    if (subtypeId) q.set("subtype_id", subtypeId);
    return request("GET", `/api/items/find?${q.toString()}`);
  },
  addItem: (item) => request("POST", "/api/items", item),
  updateItem: (id, item) => request("PUT", `/api/items/${id}`, item),
  deleteItem: (id) => request("DELETE", `/api/items/${id}`),

  // customers
  searchCustomers: (search = "") => request("GET", `/api/customers?search=${encodeURIComponent(search)}`),
  getCustomerByPhone: (phone) => request("GET", `/api/customers/phone/${encodeURIComponent(phone)}`),
  getCustomer: (id) => request("GET", `/api/customers/${id}`),
  addCustomer: (c) => request("POST", "/api/customers", c),
  updateCustomer: (id, c) => request("PUT", `/api/customers/${id}`, c),
  deleteCustomer: (id, password) => request("DELETE", `/api/customers/${id}`, { password }),
  getCustomerHistory: (id) => request("GET", `/api/customers/${id}/history`),

  // wishlist
  getWishlist: (customerId) => request("GET", `/api/customers/${customerId}/wishlist`),
  addWishlist: (customerId, description) => request("POST", `/api/customers/${customerId}/wishlist`, { description }),
  setWishlistFulfilled: (id, fulfilled) => request("PUT", `/api/wishlist/${id}`, { fulfilled }),
  deleteWishlist: (id) => request("DELETE", `/api/wishlist/${id}`),
  allOpenWishlist: () => request("GET", "/api/wishlist"),

  // bills
  searchBills: (params = {}) => {
    const q = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => { if (v) q.set(k, v); });
    return request("GET", `/api/bills?${q.toString()}`);
  },
  nextBillNo: () => request("GET", "/api/bills/next-number"),
  saveBill: (bill) => request("POST", "/api/bills", bill),
  getBill: (id) => request("GET", `/api/bills/${id}`),
  deleteBill: (id, password) => request("DELETE", `/api/bills/${id}`, { password }),

  // stats
  statTotals: (from, to) => request("GET", `/api/stats/totals?${qs({ date_from: from, date_to: to })}`),
  statDaily: (from, to) => request("GET", `/api/stats/daily?${qs({ date_from: from, date_to: to })}`),
  statMonthly: () => request("GET", "/api/stats/monthly"),
  statTopItems: (from, to, by = "quantity", limit = 10) =>
    request("GET", `/api/stats/top-items?${qs({ date_from: from, date_to: to, by, limit })}`),
  statCategory: (from, to) => request("GET", `/api/stats/category?${qs({ date_from: from, date_to: to })}`),
  statTopCustomers: (from, to, limit = 10) =>
    request("GET", `/api/stats/top-customers?${qs({ date_from: from, date_to: to, limit })}`),

  health: () => request("GET", "/api/health"),
};

function qs(obj) {
  const p = new URLSearchParams();
  Object.entries(obj).forEach(([k, v]) => { if (v !== undefined && v !== null && v !== "") p.set(k, v); });
  return p.toString();
}

export function toast(message, isError = false) {
  const el = document.getElementById("toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 3200);
}

export function rupees(v) {
  const n = Number(v) || 0;
  return "₹" + n.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export const DELETE_PASSWORD_PROMPT = "Enter delete password:";
