# Cloth Shop Billing — Local Server

Runs the billing system as a small web server on one computer (the shop
PC). Anyone on the **same Wi-Fi** — including phones — opens a URL in
their browser to bill, check stock, look up customers, or view stats.
Nothing goes over the internet; nothing to install on the phones.

```
clothshop_billing_server/
  backend/          FastAPI server + SQLite database (all business logic)
    main.py
    database.py
    requirements.txt
    data/           created automatically: cloth_shop.db + backups/
    logs/           created automatically: server.log + a QR code image
  frontend/         plain HTML/CSS/JS the browser loads (no build step)
    index.html
    css/style.css
    js/*.js
  run_server.py     launcher: finds your IP, shows the URL + QR, starts the server
  start_server.bat  double-click this on Windows
  start_server.sh   for Mac/Linux
```

## First-time setup (Windows shop PC)

1. Install Python 3.10+ from python.org if it isn't already installed
   (tick "Add python.exe to PATH" during install).
2. Copy this whole `clothshop_billing_server` folder onto the shop PC.
3. Double-click **`start_server.bat`**. The first run installs everything
   automatically (needs internet once, for the install only) — after
   that it starts instantly, offline.

## Every day

Double-click `start_server.bat`. A console window opens and shows:

```
On this computer, open:
  http://192.168.1.42:5000

On a PHONE (same Wi-Fi as this computer), either:
  - type http://192.168.1.42:5000 into the browser, or
  - scan this QR code:
  [QR code]
```

Point a phone's camera at the QR code (or open it from Photos/the saved
PNG) to jump straight to the billing page — no typing, no app install.
Keep the console window open while billing; closing it stops the server
for everyone. Bookmark the URL on each phone/laptop so it's one tap next
time (the IP can change if the router reassigns it, so re-check the QR
if a device suddenly can't connect).

## Logs and backups

- **`backend/logs/server.log`** — every request the server handled, and
  when it started/stopped. Rotates automatically (keeps the last 5 x 2MB).
- **`backend/data/cloth_shop.db`** — the live database.
- **`backend/data/backups/`** — a fresh timestamped copy of the database
  is saved here automatically after *every* change (new bill, edited
  item, deleted customer, etc.), keeping the most recent 200 copies. To
  restore one: stop the server, rename a backup file to `cloth_shop.db`
  in `backend/data/`, restart.

For extra safety, periodically copy the whole `backend/data/` folder
somewhere else (a USB drive, cloud folder) — the automatic backups
protect against a bad edit, not against the PC itself failing.

## Notes on what changed vs. the desktop app

- **Delete protection**: deleting a bill or customer still needs the
  password (`1852`, same as the desktop app) — now checked on the server
  too, since the API is reachable by any device on the Wi-Fi, not just
  from in front of the till.
- **Barcode scanning**: assumes a USB/Bluetooth barcode scanner set to
  "keyboard wedge" mode (the common, cheap kind), same as the desktop
  app — it types the code into the barcode field. Scanning with a
  phone's *camera* isn't wired up yet; it's a natural next add-on if
  useful (a JS library like `html5-qrcode` handles it without a native
  app).
- **Inventory editing** on the web version uses simple pop-up prompts
  for now rather than a full form dialog — functional, but the desktop
  app's inventory form is nicer for bulk catalog work. Worth polishing
  if the team ends up doing catalog entry from the web version a lot.
- Everything else (cart logic, discount toggle, stock deduction,
  statistics, wishlist) mirrors the desktop app's behaviour, since it's
  the same `database.py` underneath.

## Security note

This is designed for a **trusted local Wi-Fi** (the shop's own network).
Anyone who can join that Wi-Fi can reach the billing system. If the
Wi-Fi is shared with customers, put the shop PC and till devices on a
separate guest-free network or VLAN.
