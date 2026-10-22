# Regression checks

From the repository root, install the chosen desktop copy's requirements, then run:

```bash
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 BILLING_APP_DIR=app python -m unittest discover -s tests -v
QT_QPA_PLATFORM=offscreen PYTHONDONTWRITEBYTECODE=1 BILLING_APP_DIR=Billing_App/app python -m unittest discover -s tests -v
```

Checks use temporary synthetic SQLite databases. The printing flow is exercised
with PDF output instead of a physical printer. No running services are needed.
On Linux, Qt's shared libraries must be available even with the offscreen platform.

Windows release validation also requires running `Billing_App\setup.bat` and
both desktop `build_windows.bat` scripts on Windows. A failed dependency install
or PyInstaller build must return a nonzero exit code and must not print setup
success. Run a packaged executable, print a receipt and save a PDF there as well.

## Existing data

Migration backfill applies only to databases that have never had a payments
table. Existing payments are preserved: the app cannot determine which past
"Migrated from historical bill" entries were correct versus created by the old
bug. Reconcile those entries against shop records if affected.

New bill lines record the actual stock deducted. Old lines lack that information
and retain their historical quantity-based reversal. Original deductions and
invoice numbers already deleted before this update cannot be reconstructed from
the remaining database. The new persistent invoice sequence starts above the
surviving historical invoice numbers and does not decrease after future deletions.

## Windows setup checks

The setup helper has a standard-library unit suite (no Qt required):

```bash
python -m unittest discover -s tests -p test_windows_setup.py -v
```

On Windows, run setup/build through the batch files, execute both full desktop
suites using each app's `.venv-windows\Scripts\python.exe`, and verify that each
packaged main window opens and closes. These checks complement Linux/offscreen
tests; a wheel-resolution check alone does not establish that a Windows
executable runs successfully.

The source startup probe can also be run in a prepared environment:

```bash
python tools/smoke_desktop.py --app-dir Billing_App/app
```

It uses a temporary database and verifies populated startup, tab navigation,
QR generation and PDF output without opening the user's shop database.

## Role permissions

`test_access.py` runs with the same `BILLING_APP_DIR` selector and temporary fixtures.
It checks employee startup, password rejection/cancellation, admin login/logout,
fixed catalog pricing, GST, full payments, admin discounts/credit, restricted
management commands, stale callbacks after logout and receipt access. Existing
administrator regression tests now authenticate explicitly. The setup smoke also
opens both dashboards and checks that logout returns to employee mode.

## Reporting checks

`test_reporting.py` uses dated synthetic bills/payments/expenses to verify:
month boundaries and leap years, custom and all-time filters, timestamped bills,
late collections on older bills, outstanding balances, revenue allocation and
rounding, mean/median/population deviation, CSV contents and error paths, receipt
navigation, empty/zero-sales charts and the Balances payment button layout.
Run it for either copy through the existing `BILLING_APP_DIR` selector. No customer
production data is used. Reporting screens are also rendered offscreen for visual
inspection; this complements rather than replaces native Windows validation.

`test_reporting_scale.py` adds multi-page fixtures, stable ordering, complete
exports, receipt identity on later pages, last-page deletion, historical
fractional-cent payments, zero-weight historical lines, CSV failure recovery,
read snapshots during writes, indexed date queries, FULL synchronization,
fresh reports after navigation, and labelled aggregation of long chart periods.

## Offer checks

`test_offers.py` covers configurable mixed category/brand bundles, repeated bundles
and remainders, exact discount/GST allocation, mixed tax rates, unequal prices,
large quantities, employee automatic pricing and management restrictions,
transactional repricing checks, receipt snapshots, report reconciliation,
GUI create/edit/disable, prefix search/paging, migration preservation and indexed
lookup plans. Randomized small carts are compared with a simple expanded-piece
reference calculation. The one-million-rule benchmark is a separate synthetic
stress probe, not part of ordinary unittest discovery.

## Exchange checks

`test_exchanges.py` exercises admin-only lookup/cart flow, indexed phone and bill
queries, walk-in receipts, original-price valuation, discounts and GST, legacy
bill allocation, partial return cents, strictly higher purchase values/full
settlement, concurrent duplicate-return prevention, idempotent save retries,
transaction rollback, current offer checks, restocking, subsequent exchanges,
linked receipt/deletion rules, signed reporting and additive schema preservation.
The same tests run against both desktop copies with `BILLING_APP_DIR` above.
Offscreen screen/PDF rendering and a 100,000-bill synthetic lookup benchmark
complement the suite; native Windows and physical printing still need Windows.

## Employee attendance checks

`test_employees.py` verifies existing-schema preservation, optional fields,
name search and duplicate identities, paging, archival/history, one mark per day,
concurrent corrections, leap months, future dates, full/half-day counts, unmarked
days, linked expense/report reconciliation, complete CSV exports, indexed query
plans, admin-only access and stale callbacks, and the employee/attendance/expense
GUI flows. The existing expense deletion regression targets the action column
after the added Employee column. Run the suite against both app copies with the
commands above; all data is synthetic.

## Final audit regressions

`test_final_audit.py` covers fractional historical payment totals across receipts,
collection limits and reports; per-bill customer balances; invalid cart edits;
GST for bundle participants with zero allocated discount cents; distinct backup
files created in the same second; and whole-database CSV consistency during a
concurrent sale. Backup and whole-database export cases run only for the
recommended copy that provides those features. The concurrency payment test
instruments the payment read without depending on a specific aggregate name.

## Adaptive desktop and updater checks

`test_desktop_adaptive.py` runs a fresh production-theme process at 640×480,
800×600, 1024×600, 1366×768 and 1920×1080. It checks populated employee/admin
bills, discount controls, receipt button visibility, repeated barcode scans,
explicit printer selection, error handling and backup integrity. Print actions
use a PDF device; physical printers still require Windows verification.

`test_updater.py` creates local temporary Git remotes and synthetic build outputs
to check branch-preserving fast-forwards, dirty/divergent checkout rejection,
helper startup and staging/replacement failures. `test_windows_setup.py` checks
that staging leaves the installed executable intact. Windows process waiting,
actual executable rebuilding/replacement/relaunch and printer drivers require
a native Windows check; these platform-independent tests do not establish that.

## Purchases and accounting

`test_commerce.py` covers GST/no-GST purchases, ₹50,000/₹25,000 partial payment,
unit conversion and snapshots, transaction rollback, duplicate supplier invoices,
weighted costs, sale deletion/exchange reversals, per-godown stock/expenses,
advances without duplicate cash, balanced journals, unknown historical costs,
repeatable migrations, role checks, real purchase-form submission, PDF and backup.
The existing legacy migration, sales, offers, reporting and access suites also
cover the shared database paths.

Run the new screen geometry probe for either copy:

```bash
QT_QPA_PLATFORM=offscreen python tests/commerce_layout_probe.py app
QT_QPA_PLATFORM=offscreen python tests/commerce_layout_probe.py Billing_App/app
```

It checks all new sections with the production stylesheet from 640×480 through
1920×1080, including visibility of purchase Save/Clear actions.

## Barcode label verification

Install independent decoder/PDF inspection tools in the test environment:

```bash
python -m pip install -r tests/requirements-labels.txt
```

`test_labels.py` covers Code128 and Unicode QR decoding at 203/300 DPI, custom
75×35 mm PDF pages and exact page counts, clipped/obscured symbol rejection,
metadata lookup in billing, alias collision rollback, twice-confirmed printing,
cancellation, failed spool submission, durable stock posting and reprint
idempotence, templates/calibration, permissions and existing accounting data.
Without these test-only packages, only independent decode/PDF measurement tests
are skipped; production requires python-barcode and existing qrcode/PySide6.

Additional production-styled geometry probes:

```bash
QT_QPA_PLATFORM=offscreen python tests/label_layout_probe.py app
QT_QPA_PLATFORM=offscreen python tests/label_layout_probe.py Billing_App/app
```

On Windows PowerShell set `$env:QT_QPA_PLATFORM='offscreen'` before the equivalent
Python commands. Real release checks on both OSes require configuring a native
printer, printing the calibration square and measuring paper output. Verify that
cancelling either confirmation adds no stock and that reprinting a submitted
batch adds no stock. Stop/cancel the OS print queue before reconciling a partial
or interrupted job; automated tests never send paper jobs to a real printer.
