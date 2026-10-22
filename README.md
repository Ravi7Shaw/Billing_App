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

## Barcode Labels — Linux and Windows

**Admin → Barcode Labels** provides a label designer and stock-linked print
batches in both app copies. Install the updated requirements before running from
source; Windows setup/update also installs the new `python-barcode` dependency.
The Citizen CL-S621II photo is the target device. Windows uses its installed
printer driver through Qt. Linux uses CUPS command tools (`lp` / `lpstat`) when
available, submitting an actual-size PDF and showing the CUPS job id. This also
works when Qt cannot discover the CUPS printer. Without the command tools, the
app uses Qt's native printer support. Both paths need a compatible printer driver.

### Design and metadata

- Width and height are **user-entered millimetres**, initially **100 × 50 mm**.
  Choose **Design label** for a live white-paper preview with a shadow. It fits
  the screen without changing physical print size. The preview uses selected
  printer-dot geometry, including X/Y correction offsets.
- Select an existing catalog item or create a new one with category, brand/style,
  size, colour, selling price and barcode. Categories/brands can be added from
  this screen; they are the same masters used by Inventory and billing.
- Add and drag text, Code 128, QR, rectangles, lines and logo images. Drag the
  selected element's lower-right handle to resize, or enter exact X/Y/width/height
  in millimetres under **Selected element properties**. Rotate, align, duplicate,
  delete, reorder layers, undo or redo. **Double-click text on the paper** or
  select a text/barcode/QR box and click **Edit / style** to type into a multiline
  writing box, choose **font, size, bold, italic, underline, alignment and box dimensions**, then **Save**. Adding a Text box opens this editor automatically.
  Choose **Rectangle, Ellipse, Triangle or Line** and click **Add** to insert a shape.
  **Edit / style** or double-click opens shape properties for dimensions, rotation,
  border/fill colours and shape type; click **Apply properties**.
- Font selection includes every font installed on the current computer, with
  point size, bold, italic, underline and colour controls. Install the same fonts
  on Linux and Windows for matching typography. Thermal printers print in their
  supported monochrome mode; decorative colour depends on the printer driver.
- Text and barcode content bind to fields such as `{name}`, `{category}`,
  `{brand}`, `{style}`, `{size}`, `{color}`, `{rate}`, `{godown}` and `{barcode}`.
  Additional metadata accepts `field=value`, one field per line. Use `{{` and `}}`
  to print literal braces. The expanded values are frozen in the saved batch.
- `{metadata}` is a JSON **string containing all catalog and custom metadata**.
  Use the **QR — full metadata string** starting layout to encode it directly.
  Code 128 is available for compact ASCII strings, including a catalog SKU or
  short custom field combination. Long data cannot be made readable by shrinking
  bars indefinitely: enlarge the barcode or use QR. QR requires a 2D scanner;
  both barcode types resolve to the same existing catalog item in New Bill.
- Templates save in the shop database (included in Backup), or import/export as
  JSON with embedded logos. Physical dimensions, elements, fonts, DPI and printer
  correction offsets are saved. Starting layouts and imported templates replace
  the current design; Undo restores the previous design.

### Printer setup and accuracy

1. **Windows:** install the printer's Windows driver and add it under Printers &
   scanners. **Linux:** configure the printer through CUPS / Print Settings with
   a driver that supports its custom label size. The printer must appear in the
   app's printer selector. The desktop, template and stock code is shared across
   both OSes; the updater/build helper remains specifically for Windows `.exe`.
2. Set the actual roll width/height and gap or black-mark media mode in the driver.
   Match the app's DPI to the device/driver; **203 DPI** is the starting setting,
   with 300/600 options for other compatible printers. Enter the actual label
   width/height in the app; these are not fixed to 100 × 50 mm.
3. Use **Calibration** to print a 20 × 20 mm reference square. Measure it. If its
   size is wrong, fix driver scaling/media configuration; do not compensate with
   offsets. Use X/Y offsets only to correct placement. Calibrate the printer's
   media/gap sensor according to its manual if feeding is wrong.
4. The application requests the entered custom paper size, zero application
   margins, full-page drawing, one driver copy and **100% scale**. Requested
   label count is emitted as separate pages. It rejects a driver-reported paper
   size mismatch on the native driver path, elements outside the paper, clipped text, obscured symbols,
   and barcodes whose narrow modules would be under two device dots. Barcode/QR
   rotations use 90-degree steps, with quiet zones preserved.
5. **PDF preview** saves one actual-size label and never changes stock. When
   printing a PDF externally, select actual size / 100%, not fit-to-page.
   PDF page dimensions are quantized to points; tests allow 0.3 mm rounding.

Physical alignment, media feeding and paper output still depend on the installed
driver, calibration and hardware. Automated tests verify encoding, rendering,
page size and the print workflow; they cannot certify a particular printer's
physical output. A missing printer produces an actionable dialog, not a silent
print button.

### Arch Linux printing setup

Run these commands on the Arch computer (they install/start the OS printing service):

```bash
sudo pacman -S cups cups-filters
sudo systemctl enable --now cups.service
lpstat -r
```

Add the printer in your desktop Print Settings or [CUPS administration](http://localhost:631).
Install a driver for the printer model that accepts PDF documents and supports
custom label dimensions. A raw queue cannot convert the PDF to Citizen printer
commands. Choose the correct driver/language mode for the CL-S621II and print a
system test page before using the app. A printer being plugged in alone does not
create a working queue. Driverless setup applies only if the device supports it.

In the app, open Print → **Refresh printers**, select the configured queue, and
print **Calibration** first. The app rejects a paused or rejecting CUPS queue
before creating a stock batch. After sending, it shows a job id such as
`Citizen-42`; the label Print history also keeps this id. Check queued jobs with:

```bash
lpstat -p -a
lpstat -o
```

For a filter/driver error, inspect `journalctl -u cups.service -n 80 --no-pager`
and the CUPS error log on that machine. Resume or cancel jobs in Print Settings.
Do not resubmit a timed-out job before checking the queue and physical output.
**Setup / help** in both receipt and label dialogs includes OS-specific
instructions. **Queue status** shows the selected Linux printer state and pending
jobs, including driver/filter errors reported by CUPS. PDFs can be saved even
when no printer is configured.

On **Windows**, install the manufacturer's driver, print a Windows test page,
then refresh/select the printer in the app. Source launches use the pulled code;
an existing `.exe` needs **Update & rebuild** or the repository's `setup.bat` to
include fixes. Linux CUPS commands are never used by the Windows print path.

### Quantity, confirmations and stock

- Choose the receiving **godown** and the number of labels. **Add new stock and
  print** adds that many **pieces** after successful submission to the OS printer.
  Optional purchase cost per piece provides stock valuation; unknown costs remain
  unknown. This is stock introduced directly, with known value balanced against
  equity in accounts. Use Purchases for supplier debt/payment accounting.
- **Reprint labels — no stock change** is the default for existing items. Labels
  for purchases already received should use reprint mode, avoiding a second stock
  addition. A new item requires Add new stock mode.
- Two separate confirmations show printer, dimensions, DPI, **label count**,
  selling price, receiving godown, stock increase, cost and correction offsets.
  Cancelling either confirmation creates no batch and adds no stock.
- Each job has a durable `L-` batch reference and an immutable design/data snapshot.
  Stock posts once, atomically. **Print history → Reprint saved batch** uses the
  original label and a user-entered replacement-label count, adding no stock even
  if the catalog price has since changed.
- Printer submission is not proof of paper output. A jam/error or interruption
  leaves a job **uncertain/sending**, with no automatic retry. Check the printer
  and queue, then either confirm that all labels printed (post the recorded stock
  once), or cancel the unresolved batch with no stock change. Resolve an earlier
  new-stock job before starting another for that item/godown. For partial output,
  cancel queued pages and reconcile the actual pieces manually before reprinting
  only the missing labels. Submitted stock is never silently reversed on reprint.

### Connected accounting and primary view

Chart of Accounts now opens reports on **All time**, so existing historical
sales/expenses are visible immediately. Its top godown selector is shared across
Account master statements, Inventory master quantities, Godowns and Reports.
**Primary — all godowns** is the default combined view; actual shops retain their
own quantities, stock movements and transactions. It is not an extra stock
location and does not double-count the other shops. **Default shop** remains the
real location for previously unallocated stock, older sales and employee sales.
Catalog definitions (category/brand/item) are shared; per-shop inventory lists only
items allocated to that shop. Known-value label stock additions also appear in
accounts. A compact section chooser makes all admin tabs reachable on small screens.

### Run from Python

Windows, from the repository root:

```bat
py -m venv .venv
.venv\Scripts\python.exe -m pip install -r app\requirements.txt
.venv\Scripts\python.exe app\main.py
```

Linux, from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r app/requirements.txt
.venv/bin/python app/main.py
```

Linux needs a graphical desktop with Qt's system libraries and a configured CUPS
printer for physical printing. Both source copies use the same user-profile shop
database; take a Backup before testing with real shop data.
