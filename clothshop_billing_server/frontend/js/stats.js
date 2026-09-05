// stats.js -- KPI cards + charts, mirrors the desktop app's Statistics tab.
import { api, rupees } from "./api.js";

const $ = (id) => document.getElementById(id);
let dailyChart, categoryChart, monthlyChart;

export async function initStats() {
  $("statsQuickRange").addEventListener("change", refreshStats);
  $("topItemsBy").addEventListener("change", refreshStats);
  await refreshStats();
}

export async function refreshStats() {
  const { from, to } = rangeToDates($("statsQuickRange").value);

  const [totals, daily, category, monthly, topItems, topCustomers, wishlist] = await Promise.all([
    api.statTotals(from, to),
    api.statDaily(from, to),
    api.statCategory(from, to),
    api.statMonthly(),
    api.statTopItems(from, to, $("topItemsBy").value),
    api.statTopCustomers(from, to),
    api.allOpenWishlist(),
  ]);

  renderCards(totals);
  renderDailyChart(daily);
  renderCategoryChart(category);
  renderMonthlyChart(monthly);
  renderTopItems(topItems);
  renderTopCustomers(topCustomers);
  renderWishlist(wishlist);
}

function renderCards(t) {
  $("statCards").innerHTML = [
    ["Revenue", rupees(t.revenue)],
    ["Bills", t.bill_count],
    ["Pieces sold", t.pieces],
    ["Avg bill", rupees(t.avg_bill)],
  ].map(([label, value]) => `<div class="stat-card"><div class="label">${label}</div><div class="value">${value}</div></div>`).join("");
}

function renderDailyChart(rows) {
  const labels = rows.map(r => r.d);
  const values = rows.map(r => r.revenue);
  const mean = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
  dailyChart?.destroy();
  dailyChart = new Chart($("dailyChart"), {
    type: "line",
    data: {
      labels,
      datasets: [
        { label: "Revenue", data: values, borderColor: "#2f6f4f", backgroundColor: "rgba(47,111,79,0.15)", fill: true, tension: 0.25 },
        { label: "Average", data: labels.map(() => mean), borderColor: "#c77b30", borderDash: [6, 4], pointRadius: 0 },
      ],
    },
    options: { responsive: true, plugins: { legend: { display: true } } },
  });
}

function renderCategoryChart(rows) {
  categoryChart?.destroy();
  categoryChart = new Chart($("categoryChart"), {
    type: "pie",
    data: {
      labels: rows.map(r => r.category),
      datasets: [{ data: rows.map(r => r.revenue), backgroundColor: palette(rows.length) }],
    },
    options: { responsive: true },
  });
}

function renderMonthlyChart(rows) {
  monthlyChart?.destroy();
  monthlyChart = new Chart($("monthlyChart"), {
    type: "bar",
    data: { labels: rows.map(r => r.month), datasets: [{ label: "Revenue", data: rows.map(r => r.revenue), backgroundColor: "#2f6f4f" }] },
    options: { responsive: true, plugins: { legend: { display: false } } },
  });
}

function renderTopItems(rows) {
  $("topItemsBody").innerHTML = rows.map(r => `<tr><td>${esc(r.name)}</td><td>${r.total_qty}</td><td>${rupees(r.total_revenue)}</td></tr>`).join("");
}

function renderTopCustomers(rows) {
  $("topCustBody").innerHTML = rows.map(r => `<tr><td>${esc(r.name)}</td><td>${r.visits}</td><td>${rupees(r.total_spent)}</td></tr>`).join("");
}

function renderWishlist(rows) {
  $("wishlistStatBody").innerHTML = rows.map(r =>
    `<tr><td>${esc(r.customer_name)}</td><td>${esc(r.item_description)}</td><td>${(r.date_added || "").slice(0, 10)}</td></tr>`
  ).join("");
}

function rangeToDates(range) {
  const today = new Date();
  const iso = (d) => d.toISOString().slice(0, 10);
  if (range === "all") return { from: undefined, to: undefined };
  const days = parseInt(range, 10);
  const d = new Date(today);
  d.setDate(d.getDate() - (days - 1));
  return { from: iso(d), to: iso(today) };
}

function palette(n) {
  const base = ["#2f6f4f", "#c77b30", "#7a9e7e", "#a3611f", "#5a8a6a", "#d99a5c", "#3d5a4a", "#e0b585"];
  return Array.from({ length: n }, (_, i) => base[i % base.length]);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
