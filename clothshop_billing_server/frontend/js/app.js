// app.js -- entry point. Wires up tab switching and initializes each
// screen, the same role main.py plays for the desktop app's five tabs.
import { api } from "./api.js";
import { initBilling } from "./billing.js";
import { initInventory } from "./inventory.js";
import { initCustomers, refreshCustomersTab } from "./customers.js";
import { initSales, refreshSales } from "./sales.js";
import { initStats, refreshStats } from "./stats.js";
import { initReceiptModal } from "./receipt.js";

const initialized = { billing: false, inventory: false, customers: false, sales: false, stats: false };

async function activateTab(name) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === `tab-${name}`));

  if (!initialized[name]) {
    initialized[name] = true;
    if (name === "billing") await initBilling();
    if (name === "inventory") await initInventory();
    if (name === "customers") await initCustomers();
    if (name === "sales") await initSales();
    if (name === "stats") await initStats();
  } else {
    // Tabs refresh themselves with the latest data every time they're
    // revisited, same as the desktop app's on-tab-change refresh.
    if (name === "customers") await refreshCustomersTab();
    if (name === "sales") await refreshSales();
    if (name === "stats") await refreshStats();
  }
}

document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => activateTab(btn.dataset.tab));
});

// After a bill is saved, refresh anything that depends on it -- mirrors
// the on_bill_saved callback wiring in the desktop app's main.py.
document.addEventListener("bill-saved", () => {
  initialized.sales = false;
  initialized.stats = false;
  initialized.customers = false;
});

initReceiptModal();
activateTab("billing");

// Lightweight periodic health check just to keep the "connected" badge
// honest if the server or Wi-Fi drops mid-session.
setInterval(() => { api.health().catch(() => {}); }, 15000);
