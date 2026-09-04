# Cloth Shop Billing System

A desktop billing application built with **PySide6** and **SQLite**, made for
a clothing shop that sells shirts, t-shirts, jeans (multiple brands), cotton
pants, and ladies wear — with or without barcodes.

## 1. Setup

Requires Python 3.9+.

```bash
cd app
pip install -r requirements.txt
python main.py
```

The database file is created automatically on first run at:
- Windows: `C:\Users\<you>\.cloth_shop_billing\cloth_shop.db`
- Mac/Linux: `~/.cloth_shop_billing/cloth_shop.db`

Back up that one file to back up your entire shop's data.

## 2. What's inside

| Tab | Purpose |
|---|---|
| **New Bill** | Scan a barcode, or enter items manually with autocomplete. Customer lookup by phone. Discount panel is hidden until you click "+ Discount". Prints / saves a PDF receipt. |
| **Inventory** | Manage categories → brands/styles → items. Add, rename, delete, set rates. Nothing is hardcoded — everything here is editable data. |
| **Customers** | Search any customer, see full purchase history, and log what they said they want next time ("Wants Next"). |
| **Sales History** | Every bill ever made, filterable by date range, searchable, exportable to CSV. |
| **Statistics** | Revenue, pieces sold, best sellers (by quantity or revenue), daily sales trend with **mean and standard deviation**, category breakdown, top customers, and open customer requests. |

## 3. How "no hardcoding" works

The category list (Shirts, T-Shirts, Jeans, Cotton Pants, Ladies) and the
starter brands (LP / Mufti / US Polo for Jeans, Round Neck / Collar for
T-Shirts) are inserted into the database **once**, the first time the app
runs — as a convenience starting point, not as code. From that point on:

- You can rename or delete any category or brand from the **Inventory** tab.
- Add a new category any time (e.g. "Sarees", "Kids Wear") — no code changes.
- Every item you type manually while billing (that isn't already in the
  catalog) is **automatically saved** to the catalog, so it's suggested by
  autocomplete the next time you type a similar name. This is how the
  "recommend as you type" behaviour learns your actual stock over time.

## 4. Barcode-less items — the recommended workflow

1. Try scanning the barcode first.
2. If nothing is found, the app tells you and shifts focus to manual entry.
3. Pick the **Category** (e.g. Jeans), then **Brand/Style** (e.g. Mufti).
4. Start typing the item name — existing items in that category/brand show
   up as suggestions. Pick one to auto-fill its rate, size, and color, or
   just type a new name and fill in the rate yourself.
5. Optionally assign it a barcode right there so it can be scanned next time.
6. Click **+ Add to Bill**.

## 5. Discount (per item)

Each line in the bill has its own **Discount** field, but it's hidden by
default — the cart table only shows Item / Category / Qty / Rate / Amount.
Click the small **"▸ Discount"** button above the cart to reveal the
Discount column and type a rupee discount straight into any line; click it
again ("▾ Discount") to hide it. The totals at the bottom always show the
Subtotal (before discount) and the final Total to Pay; the "Total discount
given" line only appears while the Discount column is open, so a customer
glancing at the screen doesn't see it by default.

## 6. Deleting a bill or a customer

Both **Sales History** and **Customers** have a **Remove** button (per bill,
and for a whole customer). Removing anything asks for a confirmation and
then a password — **1852** — so it can't happen by accident or by someone
just clicking around. Removing a bill also puts its stock quantities back.
Removing a customer keeps their past bills in your sales records (as
walk-in sales) but deletes their profile and wishlist. To change the
password, edit `DELETE_PASSWORD` near the top of `widgets.py`.

## 7. Customizing the receipt

Open `receipt.py` and edit `SHOP_NAME` and `SHOP_TAGLINE` at the top of the
file to match your shop's name.

## 8. Project structure

```
app/
  main.py          - application entry point, wires all tabs together
  database.py       - all SQLite schema + queries (the only place SQL lives)
  theme.py           - one stylesheet used across the whole app
  widgets.py          - small reusable UI helpers (currency formatting, etc.)
  billing_tab.py       - New Bill screen
  inventory_tab.py      - Category / brand / item management
  customers_tab.py       - Customer search, history, wishlist
  sales_tab.py             - Sales history + CSV export
  stats_tab.py               - Charts and KPIs
  receipt.py                  - Printable / PDF receipt dialog
  assets/icon.ico               - app icon (Windows .exe icon, title bar, taskbar)
  assets/icon_*.png               - the same icon as plain PNGs
  generate_icon.py                 - regenerates the icon if you want to change it
  build_windows.bat                 - one-click .exe build (see below)
  requirements.txt
```

## 9. The app icon

`assets/icon.ico` is used three places: the Windows `.exe` file itself, the
window's title bar, and the taskbar. If you ever want a different design,
edit the colors/shapes at the top of `generate_icon.py` and re-run:

```bash
python generate_icon.py
```

That regenerates `assets/icon.ico` and the PNG copies from scratch — nothing
else in the app needs to change.

## 10. Packaging as a standalone .exe (Windows)

This turns the whole app into a single `ClothShopBilling.exe` your staff can
double-click, with no need to install Python on the shop's computer.

**Easiest way:** double-click `build_windows.bat` inside the `app` folder.
It installs everything needed and builds the exe for you.

**Manual way**, from a Command Prompt inside the `app` folder:

```cmd
pip install -r requirements.txt
pip install pyinstaller
pyinstaller --noconsole --onefile --name ClothShopBilling --icon=assets\icon.ico --add-data "assets;assets" main.py
```

Either way, the finished file appears at `dist\ClothShopBilling.exe` — copy
that one file to the shop's desktop. Right-click it any time and choose
"Pin to Start" or "Pin to taskbar" to make it a normal-looking desktop app
with your icon.

