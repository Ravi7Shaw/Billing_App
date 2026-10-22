# Cloth Shop Billing

## Employee and admin dashboards

The app opens in **Employee** mode: existing catalog items, fixed prices with
automatic admin-defined offers, quantities, customer details, full payment and receipts. GST is automatically included in the
amount due using the existing calculation: a ₹100 price with 5% GST totals ₹105.

**Admin login** unlocks exchanges, offers, manual discounts, partial payments, credit, inventory, customers,
balances, expenses, sales history, statistics, backup and update buttons. The recommended copy also has whole-database
CSV export. **Lock** clears the unfinished bill
and returns to employee access. Restarting always begins in Employee mode.

In both Employee and Admin billing, typing a phone number shows matching
`phone — customer name` suggestions. Selecting one fills the existing customer
details and uses their stored phone number for the bill.

The admin password configured for this release also authorizes deletion confirmations.
The local application access gate does not replace Windows account/file permissions
for protecting the SQLite database or preventing edits to the program itself.

The receipt's UPI QR uses the recorded payment amount. For example, a ₹700 bill
with **Paid now** set to ₹200 generates a ₹200 QR and shows ₹500 outstanding.
The QR caption shows the same payment amount. A fully credit bill (₹0 paid) has
no payment QR. Reopening a receipt uses its current total recorded payments.

## Screen sizes, printing, backup and updates

Billing uses two columns on wide screens. On smaller windows, switch between
**Customer / Add items** and **Bill**; adding an item brings the bill forward.
The Bill view has its own barcode field for repeated scanning.
The cart uses the remaining height, payment fields scroll when needed, and
**Complete Bill** stays visible. Management pages can scroll in smaller windows.
Admin controls share the tab row instead of taking a separate row.

Receipts open within the screen's usable area. **Print A4**, **Save PDF** and
**Close** stay outside the scrolling preview. Print opens a printer chooser with
copies; install the printer driver and configure the system queue if none is
listed. Linux uses CUPS when its command tools are installed; Windows uses the
native printer driver. Failed jobs show an error. Check the system print queue,
connection and printer status if a submitted job does not reach paper.

In Admin mode, **Backup** lets you choose a folder for a complete SQLite `.db`
backup. It includes customers, bills, payments, inventory and all other tables,
using SQLite's online backup operation while the app is open. Both app copies
provide this button; copies have unique names and do not overwrite each other.

**Update** checks the checkout's current remote tracking branch. Choose
**Back up, update and rebuild** to close billing, fast-forward that same branch,
build a new executable in a staging folder and reopen it. Git for Windows,
standard supported Python and an internet connection are required. Local edits,
local-only commits and divergent history stop the update. The previous executable
is retained if building fails, and a successful update keeps
`ClothShopBilling.previous.exe` alongside the new executable. A failed build may
leave the source checkout updated; retry Update to rebuild, or use the previous
executable. Build details are in the app folder's `setup.log`.

Run the executable from `app/dist` or `Billing_App/app/dist` inside its complete
Git clone so Update can locate it. A detached executable or ZIP copy needs a Git
clone first. **Existing installations need one initial pull and rebuild to get
these buttons.** After that, use Update; manually deleting the executable is not
required. Complete or clear unfinished bills before updating. The updater takes
a database backup before closing and keeps the database in the same user profile.

## Windows: install and build

1. Install **standard 64-bit Python 3.13** from [python.org](https://www.python.org/downloads/windows/), including the Python launcher. Supported: Windows 10/11 x64, CPython 3.10–3.14. Free-threaded Python, 32-bit Python and ARM64 Python are not supported by this build path.
2. Clone the repository, or download and **extract the complete ZIP** into a folder you can write to. Keep the `tools` folder with the application folders.
3. Double-click **`setup.bat` at the repository root**. Internet is needed to install packages.
4. When it reports success, open **`Billing_App\app\dist\ClothShopBilling.exe`**.

The root setup builds the copy with backup/export tools. `Billing_App\setup.bat`
uses the same setup. To build the alternate root `app` copy, run
`app\build_windows.bat`; its executable is in `app\dist`.

Setup chooses a compatible interpreter, installs packages into that app's
`.venv-windows` folder, checks database/desktop/receipt behavior using temporary
data, and builds the executable. It does not install into your global Python or
require Administrator access. Keep using the same shop Windows account: its data
lives at `%USERPROFILE%\.cloth_shop_billing\cloth_shop.db`.

For a manual update, pull changes, close the running billing executable and run setup again.
The app environment is reused when healthy and repaired when the selected Python
changes. Old generated build files and Python caches do not belong in Git.

## If setup fails

The window stays open with a short error. The detailed log is:

- Recommended copy: `Billing_App\app\setup.log`
- Alternate copy: `app\setup.log`

Share that file and the first error, rather than copying thousands of console
lines. Setup stops on the failing step and never reports a failed build as a
success. It requests binary wheels only, so unsupported packages fail clearly
instead of attempting a C/C++ or Rust build.

For a Python detection error, install a supported standard x64 Python and reopen
setup. For package download errors, check your internet/proxy settings. For file
access errors, close the app and place the repository in a writable folder.

Do not delete your `.cloth_shop_billing` data folder to repair setup. An existing
unrecognized `.venv-windows` folder is left alone; rename it if setup asks you to.

## Advanced checks

From Command Prompt at the repository root:

```bat
setup.bat --skip-build
```

This installs dependencies and runs the source smoke checks without creating an
executable. For automation, set `BILLING_NO_PAUSE=1` before invoking a batch file;
every entry point preserves the setup process's exit code.

See [tests/README.md](tests/README.md) for regression commands and historical data
limitations. A native Windows build and packaged-app startup check is still
required before distributing an executable; Linux and wheel-resolution checks
alone do not establish that the packaged Windows application runs.

## Reports and date filters

Sales History, Balances, Expenses and Statistics share these period choices:
Today, Last 7 days, Last 30 days, This month, Selected month, Month range,
Custom dates and All time. Month ranges include both entire months; custom
ranges include both boundary days, including bills with timestamps. All time
includes every recorded date. Select a month to see that month's bills immediately.
Changing a filter refreshes the report; **Apply / Refresh** reloads current data.
Invalid ranges clear the result and prevent an export of stale rows.

CSV exports use the current period and any search/category filter, even if you
haven't pressed Refresh. Balances offers an export of all matched bills and a
separate export of the selected customer's payments. Statistics exports its
summary metrics and the selected period's daily, monthly, product, brand/style and category
series. CSV is UTF-8 for Excel, with spreadsheet formulas escaped in text fields.

Statistics includes total revenue, expenses, net profit, payments collected,
outstanding credit, bill count, pieces sold, average bill value, and daily mean,
median and population standard deviation. Daily statistics use days with sales,
including days with zero-value bills. Monthly and daily bars use the selected
period. The doughnut chart switches between products, brands/styles and categories; small groups
beyond the top eight are combined as Other.

Best Selling Items shows category, Brand / Style and item name together (for
example, Shirts → CK → CK shirt). Best Selling Brands / Styles groups each brand
within its category. Both tables show the top ten by quantity sold or revenue,
using the selected period; CSV includes all groups. Exchange returns reduce
quantity and revenue. Reports use current catalog labels; items without a brand
show “No brand / style”, and historical lines without a linked catalog item show
“Unknown brand / style”. Missing historical brands cannot be reconstructed.

Definitions:

- Revenue uses bill dates and includes GST. Net profit here means billed revenue
  minus recorded expenses; the app does not track item purchase cost separately.
- Payments collected uses payment dates, including collections on older bills.
- Outstanding means the current unpaid amount on bills issued in the period.
  Balances' "Paid toward these bills" includes later payments too. Its separate
  payment history uses the payment-date filter.
- Product/brand/category revenue allocates each bill's total to its lines, including
  historical bill-level discounts and GST. Allocations retain exact cents so
  chart totals reconcile to billed revenue.

Click a bill row in Balances or Customers' Purchase History to view, print or save
its receipt. Clicking a payment row in Balances opens that payment's bill receipt.
The Balances panes can be resized by dragging their dividers. Receive-payment and
delete buttons keep their own actions. All reporting tools remain admin-only.

### Reporting performance and reliability

Tables display up to 200 rows per page. Use **Previous / Next** to browse the
remaining matches. Totals and CSV exports always include the entire filtered
result, including rows on other pages. Exports are written to a temporary file
and replace the chosen file only after successful completion.

Reports load when their tab is opened and refresh on each visit. Saving a bill
no longer calculates hidden reports. Changing a chart's product/category or
ranking option reuses the current report; **Apply / Refresh** reads fresh data.
The trend uses monthly bars above 180 active sales days, with explicitly monthly
mean/deviation labels. Daily cards and exported daily rows retain daily values.

Date filters use SQLite date indexes. Report totals use integer cents and exact
Decimal aggregation for collections/expenses; historical bill discounts are
allocated to products with cent reconciliation. Query snapshots keep report
cards and chart series consistent during writes. SQLite WAL uses FULL write
synchronization in both desktop copies. No optional database fields are removed.

## Automatic category and brand offers

Log in as admin and open **Offers → New offer**. Enter a name, the number of
pieces per bundle, and the **total bundle price before GST**. Add the eligible
category / brand combinations, then save with **Active** checked. You can mix
several combinations in one deal, or create separate offers for separate groups.
Use **All brands / styles** for a category-wide offer. Brands are the existing
Inventory **Brand / Style** values; matching uses their IDs, not item-name text.

For example, with catalog prices of ₹800 each, add Free Soul in Jackets and Free
Soul in Sweatshirts to a bundle of **3 pieces for ₹2,000**. Any mix of those
combinations qualifies. At 5% GST, three cost ₹2,100; four cost ₹2,940; six cost
₹4,200. No example rule is automatically inserted into your database.

Employees get active offers automatically when adding, removing or changing
quantities. They still cannot edit prices, enter manual discounts, or accept
partial/credit payments. Admins manage offers and retain their manual discounts.
The bill displays offer savings, and receipts store the offer name and savings
as historical snapshots. Changing an offer never reprices an old receipt.

Select a row in Offers to edit it. Uncheck **Active** and save to disable it;
use the **Disabled** filter to find it later. The list has 200-row pages and an
indexed name-prefix search. The details panel scrolls on smaller screens.

Pricing rules:

- Every complete qualifying bundle can repeat; leftover pieces retain regular
  prices. Only bundles that reduce the price apply. With unequal catalog prices,
  the highest-priced eligible pieces enter bundles first.
- One active offer is allowed per exact category / brand combination. Conflicts
  are rejected without overwriting an existing offer. Brand-specific offers take
  priority over an all-brands offer; offers do not stack on the same pieces.
- Custom prices entered by admins do not receive automatic bundles. Manual admin
  discounts on catalog-priced lines apply after the bundle saving and cannot
  make the line negative.
- Discounts are allocated in exact cents. Bundle GST is rounded once per offer
  and tax rate, then allocated to its lines, so splitting a bundle among products
  does not lose a tax cent. Different GST rates remain separate.
- The app rechecks offers and employee pricing inside the bill transaction. If
  an offer changes while a draft is open, use **Refresh offers**, confirm the new
  total and complete the bill again.

Billing fetches only the category / brand rules relevant to cart items through a
unique active-scope index. It does not load the entire offer list. Large quantities
are calculated per cart line rather than expanding into individual pieces.

## Admin exchanges

Open **Admin → Exchanges** and search by the customer's **full phone number** or
an **exact bill number** (letter case does not matter). Select the original bill,
check its receipt if needed, and choose the number of pieces being returned.
Then click **Continue to new purchases**, scan/add the replacement items and any
extra purchases, choose the payment method, and complete the bill.

- New purchases, including GST and current offers/discounts, must cost **strictly
  more** than the returned items. Collect the entire positive difference. Equal
  or lower totals are blocked; there are no refunds or store credits.
- Return value uses the original paid price, including its discounts/offers and
  GST. Later catalog price changes do not change that value. Partial quantities
  divide the original line value, with cumulative cent allocation so returning
  every piece never loses or creates money. A ₹2,100 three-piece line credits
  ₹700 for one piece. The remaining pieces keep their original prices.
- Settle any outstanding payment on the original bill in **Balances** first.
  Walk-in bills can be found by bill number without adding a customer phone.
- The original bill stays intact. A linked exchange receipt shows returned lines
  as negatives, new purchases as positives, and the difference paid. It can be
  reprinted from Sales History, Customers, or Balances. Bills linked to an
  exchange cannot be deleted because that would break stock/payment history.
- Returned quantities are restored to stock; new purchases deduct stock. Saving
  the exchange, returned quantities, stock and payment is one transaction. The
  same original piece cannot be returned twice. Retrying the same save does not
  create another bill. New purchased pieces can later be exchanged using their
  full purchase value on the linked receipt.
- Reports record the net sale and pieces on the exchange date, and only the new
  money collected. Original sale dates remain unchanged. Product/category charts
  use signed bars when the selected period contains a negative net group, so
  returned value is included correctly. Bill counts include exchange receipts.
- Lookup uses indexed phone/bill matching and 100 bills per page. Employees cannot
  access the exchange screens or commands. Existing optional database fields
  remain optional; exchange history uses additional tables.

## Employee attendance and expenses

Open **Admin → Employees**. Add an employee with their name and optional phone,
or search by the beginning of their name. Employee IDs and phone numbers help
separate people with the same name. Choose a month, then select the employee.

- The **Attendance sheet** lists every day in that month. Select a day, choose
  **Full Day**, **Half Day**, or **Absent**, and click **Save attendance**.
  **Not marked** clears a mistaken entry. Admins can correct earlier dates;
  future attendance cannot be marked. Saving a day again updates its existing
  entry. Employee mode cannot view or change these records.
- **Days attended** counts dates marked Full Day or Half Day, with separate full
  and half-day counts. Unmarked dates are not assumed absent; future dates are
  shown as Upcoming. No salary is calculated automatically from these counts.
- **Employee expenses** records money paid for Salary, Salary advance, Travel,
  Food, or another category. The selected month shows that employee's total and
  history. These are the same expense records used by Expenses and Statistics,
  so they enter shop totals once. If an advance was already recorded, enter
  only the remaining amount when paying salary; do not record the full salary
  again. This is a record of payments, not an automatic payroll/loan system.
- Export the selected month's attendance or all its employee expense rows to CSV.
  History is paged at 100 rows, while totals and exports include all matches.
- Use **Edit / Archive** and clear Active to archive someone. Their history stays
  available through **Include archived**; restore them before adding/correcting
  records. Existing employee IDs, roles, optional fields, and attendance remain
  intact. Older expenses without an employee link remain shop expenses; the app
  does not guess which employee they belong to.

## Purchases, Chart of Accounts and godowns

These screens appear after **Admin** login in both desktop copies. Employee
billing stays in **Default shop / Retail**. The same SQLite database and Backup
button include all purchase, account and location records.

### First-time setup

1. Back up the existing database using **Backup** before installing the update.
2. Open **Chart of Accounts → Inventory master**. Stock categories/items share
   the existing Inventory catalog. Create stock groups (for example, Jeans with
   specification Denim), and units such as **Bundle = 50 pieces**. Select an item
   and use **Configure selected item** to assign its group and preferred unit.
   Existing brand/style, size, colour, barcode and selling-price editing remains
   available under **Inventory**.
3. Open **Godowns → Manage godowns** to create each shop/location. **Main Godown**
   is a combined view, not an extra location containing duplicate stock.
   Existing stock is assigned once to **Default shop**. Transfers move pieces
   and their recorded cost between locations atomically.
4. Enter **Opening stock / cost** before posting the first purchase, sale or
   transfer for an item/location. This sets total opening pieces and purchase
   cost per piece, excluding recoverable GST. Existing stock has **unknown cost**
   until a cost is supplied; the app never guesses costs from selling prices.
5. Under **Account master → Groups**, create account groups and choose their
   accounting nature (Asset, Liability, Income, Expense or Equity). Groups are
   separate from stock categories. Add supplier/general ledgers with their group,
   phone, optional GSTIN and address. Customer ledgers are linked automatically
   to Customers. Existing bills and payments appear automatically in statements.

### Purchase workflow

- **Purchases**, or **Chart of Accounts → New purchase**, opens the purchase form.
- Select a supplier ledger, invoice number, date, receiving godown and GST mode.
  Enter seller and buyer **name and phone**; **addresses are optional**.
- **Without GST:** neither party needs a GSTIN and every line's GST is zero.
  **With GST:** both GSTINs must have the 15-character GSTIN format; the admin
  enters each line's GST percentage. Rates are **before GST**. This is a purchase
  record, not GST return filing or an online GSTIN verification service.
- Select stock items and units, quantity, purchase rate per unit and optional
  rupee discount per line. For example, 2 Bundles of 50 pieces add 100 pieces.
  Unit size, item name, rate, tax and party details are saved as snapshots, so
  subsequent master edits do not rewrite receipts.
- Enter **Paid now** (zero is valid). A ₹50,000 purchase with ₹25,000 paid adds
  the stock and leaves ₹25,000 payable in the supplier ledger. Stock, purchase
  lines and initial payment commit together; a failed save rolls everything back.
- Use **Last receipt** or select the purchase in **Reports → Purchases → Open
  purchase receipt** for Print / Save PDF. Duplicate invoice numbers for the
  same supplier are rejected. Posted purchases are retained as transaction history.

### Ledgers and vouchers

- **Account master** shows date-filtered journal entries or one selected ledger
  with opening and closing balance. Positive balances are debit, negative credit.
  Sales (retail/wholesale), purchases, GST, receipts, payments, recorded expenses
  and known cost of sales feed the ledger from their source documents.
- **Voucher → Payment**, allocated to a purchase, settles supplier debt.
  **Voucher → Receipt**, allocated to a sale, settles customer debt. Amounts cannot
  exceed the remaining invoice balance. For allocations, the invoice determines
  the party and godown; debit/credit selections are disabled.
- Leave the invoice unallocated to record an advance/token payment. Payment
  debits the supplier and credits Cash / bank; Receipt debits Cash / bank and
  credits the customer. **Apply advance** selects an existing voucher and invoice
  belonging to the same party, and applies up to the remaining advance/balance.
  Allocation does not count the cash movement twice.
- Journal, Credit note, Debit note and Opening balance vouchers require distinct
  debit/credit ledgers, an amount, date, godown and narration. They adjust account
  balances; they do **not** alter stock quantities or invoice balances. Use invoice
  allocations to settle invoices, and stock transfers/opening stock for quantities.
- Cash / bank is a combined settlement account. Customer profiles are edited in
  Customers; supplier/general ledgers and account groups can be edited here.

### Sales, expenses and reports

Admin **New Bill** offers **Retail / Wholesale** and a selling godown. Prices and
sales quantities are per **piece**, and the existing admin discount/partial-payment
controls apply to either channel. Exchanges return stock to the original sale's
godown, retain its channel and reverse the original recorded cost. Expenses can
also be assigned to a godown; existing expenses and employee salary expenses
belong to Default shop.

**Chart of Accounts → Reports** contains:

- **Master / Profit & loss:** net sales, net purchases, known cost of sold items,
  expenses, journal adjustments, profit/loss, unknown-cost count, stock in/out,
  and retail/wholesale sales totals.
- **Sales:** invoice, customer, godown, channel, net/GST/total, paid, balance and cost.
- **Purchases:** supplier invoice, godown, net/GST/total, paid and balance; reprint
  a selected purchase receipt.
- **Stock in / out:** piece movements, location, item and reference, including
  purchases, sales, exchanges, transfers and inventory adjustments.
- CSV export of each report and ledger statement, with spreadsheet-formula escaping.

Date and godown filters apply to reports. The channel filter affects sales only;
shop expenses are not apportioned between retail and wholesale. Paid/balance
columns show settlement **to date** for invoices in the selected period. Stock
quantities/value in the Godowns and Inventory master views are **current**.
Combined stock movement totals include transfers in and out, labelled accordingly.

Profit = sales excluding GST − recorded cost of sold items − expenses + journal
income/expense adjustments. Purchases become inventory; unsold stock is not
immediately deducted from profit. Cost uses **moving weighted average in posting
order**. Backdated entries do not recost earlier sales. Opening stock postings are
balanced against opening-stock equity. GST is tracked separately as input/output
GST; these screens do not prepare statutory tax returns.

Historical sales lack purchase-cost snapshots, and the previous billing policy
allows sales beyond recorded stock. Such lines have **unknown cost** and the
report displays **Incomplete** instead of asserting a profit figure. Sales still
use only the selected godown's available recorded stock. Old Inventory aggregate
quantity adjustments apply their difference to Default shop and mark cost unknown
when no cost was supplied; use purchases for costed stock receipts. Opening costs
cannot be rewritten after an item's first location transaction. These rules protect
prior recorded costs and avoid inventing historical valuations.

The older Statistics card previously called Net Profit is labelled **Sales less
expenses**; use Chart of Accounts for the cost-based profit calculation.

## Citizen barcode labels — Linux and Windows

**Admin → Barcode Labels** now opens a simple form using the shop's working
Citizen ZPL layout. The label heading is **BELLI APPERAL SYNDICATE**. There is
no drawing/canvas workflow in this screen.

- Fixed **4 × 2 inches (101.6 × 50.8 mm), 812 × 406 dots at 203 DPI**.
- Enter **Brand, Product, Size, MRP, Selling price, EAN-13 barcode and quantity**.
- Barcode must contain 13 ASCII digits with a valid EAN-13 check digit. Example:
  `8905310168599`. Stock keeps the catalog category/brand/godown selections.
  For a new item, a typed brand is matched or created in that category only after
  both print confirmations; exporting/cancelling creates no brand or stock.
- Select an existing catalog item or create one. For existing items, selling
  price is read from Inventory. Change that price in Inventory before printing.
  Print text and MRP can be entered here; the fixed text rows allow 20 characters.
- Choose the stock godown and **Reprint — no stock change** or **Add new stock**.
  New catalog items require Add new stock. Items already received through
  Purchases should normally use Reprint to avoid adding their stock twice.
- Two confirmations show the actual printer, quantity, prices, barcode, godown
  and stock change. Cancelling either sends nothing and adds no stock.
- One raw ZPL document carries `^PQ` for the selected label count; the spooler
  never multiplies that quantity with additional copies.
- **Export ZPL** saves the print data without sending it or adding stock.

### Printing on Linux

The app follows the supplied working command:

```bash
lp -d Citizen_CL_S621II -o raw < citizen-label.zpl
```

It sends ZPL bytes directly to the selected queue, preserving the fixed layout.
These labels do **not** go through PDF/raster conversion. Keep the Citizen in
**ZPL / Zebra emulation** (the mode in which the supplied script already works).
A raw CUPS queue is suitable for this mode; the OS queue must point to the actual
USB/network printer. Receipt printing remains A4 PDF/native-driver printing and
needs its own compatible driver/queue; do not send receipt PDFs to the raw label
queue.

The usual queue name **Citizen_CL_S621II** is preferred in the selector. Use
**Refresh printers** if setup happened while the app was open. **Queue status**
shows CUPS printer state and pending jobs. Submission returns a CUPS job id;
that acknowledgement confirms spooling, not physical paper output.

### Install / setup

Click **Install / setup** in Barcode Labels when printing support is missing.

- **Linux:** when PolicyKit (`pkexec`) is available, an OS administrator prompt
  opens and setup progress appears in the app. It installs missing CUPS tools
  using the detected Arch (`pacman`), Debian/Ubuntu (`apt-get`) or Fedora/Amazon
  Linux (`dnf`) package manager, then starts/enables CUPS with systemd. An existing
  Citizen queue is retained. If exactly one connected Citizen CL-S621II USB URI
  is discovered, it creates the `Citizen_CL_S621II` raw queue. Multiple devices,
  undiscovered USB devices, network addresses and ports require selection in
  Print Settings; setup does not guess a port. If `pkexec` is absent, the app
  shows the exact `sudo` command to run in a terminal.
- **Windows:** opens the system **Add Printer** wizard. Windows can install the
  matching device driver, or use the manufacturer installer. Select the actual
  USB/network port. The app then writes **RAW ZPL** through the Windows spooler;
  no Python `pywin32` package is required. This setup needs a Windows printer
  queue even though the label itself is already ZPL. OS administrator approval
  may be required; the app cannot grant that permission or guess your port.

Linux command-line setup is also available from the repository root:

```bash
sudo python3 app/printer_setup.py --install-citizen
```

On Arch, the manual printing-tool setup is:

```bash
sudo pacman -S cups cups-filters
sudo systemctl enable --now cups.service
lpstat -p -a
```

Connect/power on the Citizen and use the app's setup or Print Settings to add its
queue. If printing still produces no paper, check `lpstat -o`, the printer mode,
queue port and media. Do not blindly resend a job after a timeout.

### Print history and stock

Durable batches retain the layout version and label fields. Stock posts once
after acknowledged spool submission. If submission fails or times out, the batch
stays **uncertain** and stock is not confirmed. Check the queue and physical
output before resolving it under **Print history**. Confirming all printed posts
the original stock once; cancelling adds no stock. For partial output, cancel
queued output and reconcile the actual stock before reprinting missing labels.

**Reprint** uses the original saved label/price, asks for replacement quantity,
and never adds stock. Old designer batch history is preserved; create a new
fixed Citizen label for those items rather than reinterpreting an old drawing as
ZPL. Scanning a printed EAN-13 finds its catalog item even when its older catalog
code was alphanumeric; conflicting item codes are rejected.

### Run the update

Linux/source, after pulling the existing task branch:

```bash
python3 -m pip install -r app/requirements.txt
python3 app/main.py
```

Windows source:

```powershell
py -3.13 -m pip install -r app/requirements.txt
py -3.13 app/main.py
```

An existing Windows executable must be rebuilt using **Update & rebuild** or
`setup.bat`. Both app copies use the same ZPL generation and durable stock code.
Windows RAW spooler API behavior is tested with a stub on Linux; native Windows
execution and physical printer/media output still require checks on those devices.
The built-in Citizen font determines available glyphs; hex escaping preserves
UTF-8 data safely but does not install extra fonts on the printer.
