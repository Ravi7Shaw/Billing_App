from datetime import datetime

"""
main.py
--------
FastAPI app for the Cloth Shop Billing Server.

- Exposes the same operations as the desktop app's tabs, as a REST API
  under /api/..., using the exact same database.py logic.
- Serves the frontend (frontend/) as static files, so a phone on the same
  Wi-Fi can open http://<server-ip>:<port>/ in a browser and use it --
  no app install needed.
- Logs every request to backend/logs/server.log (and the console) so you
  can see the server's status and diagnose problems after the fact.

Run directly with `python main.py` for local testing, or via the
run_server.py launcher in the project root for normal use (it also
prints the LAN URL and a QR code).
"""

import logging
import os
import time
from logging.handlers import RotatingFileHandler
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import Database

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.join(os.path.dirname(BASE_DIR), "frontend")
LOG_DIR = os.path.join(BASE_DIR, "logs")

# Matches the desktop app's DELETE_PASSWORD (widgets.py) -- deleting a bill
# or a customer requires this, checked here on the server as well as in the
# browser, since the API is reachable by anyone on the Wi-Fi.
DELETE_PASSWORD = "1852"


def setup_logging():
    os.makedirs(LOG_DIR, exist_ok=True)
    log_path = os.path.join(LOG_DIR, "server.log")
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = RotatingFileHandler(log_path, maxBytes=2_000_000, backupCount=5)
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers if this module is imported more than once
    # (e.g. by the reloader) -- only attach if not already present.
    if not root.handlers:
        root.addHandler(file_handler)
        root.addHandler(console_handler)
    return log_path


LOG_PATH = setup_logging()
log = logging.getLogger("server")

app = FastAPI(title="Cloth Shop Billing Server")
db = Database()


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    try:
        response = await call_next(request)
    except Exception:
        log.exception(f"{request.method} {request.url.path} -> unhandled error")
        raise
    duration_ms = (time.time() - start) * 1000
    log.info(
        f"{request.method} {request.url.path} -> {response.status_code} ({duration_ms:.0f}ms)"
    )
    return response


def row(r):
    return dict(r) if r is not None else None


def rows(rs):
    return [dict(r) for r in rs]


# ==================================================================
# Pydantic request bodies
# ==================================================================
class CategoryIn(BaseModel):
    name: str


class SubtypeIn(BaseModel):
    category_id: int
    name: str


class ItemIn(BaseModel):
    name: str
    category_id: int
    subtype_id: Optional[int] = None
    barcode: Optional[str] = None
    size: Optional[str] = None
    color: Optional[str] = None
    rate: float
    stock_qty: int = 0
    active: int = 1


class CustomerIn(BaseModel):
    name: str
    phone: Optional[str] = ""
    address: Optional[str] = ""
    notes: Optional[str] = ""


class WishlistIn(BaseModel):
    description: str


class WishlistUpdate(BaseModel):
    fulfilled: int = 1


class BillItemIn(BaseModel):
    item_id: Optional[int] = None
    name: str
    category: Optional[str] = None
    quantity: int
    rate: float
    subtotal: float


class BillIn(BaseModel):
    customer_id: Optional[int] = None
    items: List[BillItemIn]
    subtotal: float
    discount_percent: float = 0
    discount_amount: float = 0
    total: float
    payment_mode: str = "Cash"
    bill_date: Optional[str] = None


class DeleteAuth(BaseModel):
    password: str = ""


def _check_password(auth: DeleteAuth):
    if auth.password != DELETE_PASSWORD:
        raise HTTPException(status_code=403, detail="Incorrect password")


# ==================================================================
# Health
# ==================================================================
@app.get("/api/health")
def health():
    return {"status": "ok", "log_file": LOG_PATH}


# ==================================================================
# Categories
# ==================================================================
@app.get("/api/categories")
def get_categories():
    return rows(db.get_categories())


@app.post("/api/categories", status_code=201)
def add_category(body: CategoryIn):
    db.add_category(body.name)
    return {"ok": True}


@app.put("/api/categories/{cat_id}")
def rename_category(cat_id: int, body: CategoryIn):
    db.rename_category(cat_id, body.name)
    return {"ok": True}


@app.delete("/api/categories/{cat_id}")
def delete_category(cat_id: int):
    try:
        db.delete_category(cat_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Can't delete: {e}")
    return {"ok": True}


# ==================================================================
# Subtypes (brands / styles)
# ==================================================================
@app.get("/api/categories/{cat_id}/subtypes")
def get_subtypes(cat_id: int):
    return rows(db.get_subtypes(cat_id))


@app.post("/api/subtypes", status_code=201)
def add_subtype(body: SubtypeIn):
    db.add_subtype(body.category_id, body.name)
    return {"ok": True}


@app.put("/api/subtypes/{sub_id}")
def rename_subtype(sub_id: int, body: CategoryIn):
    db.rename_subtype(sub_id, body.name)
    return {"ok": True}


@app.delete("/api/subtypes/{sub_id}")
def delete_subtype(sub_id: int):
    db.delete_subtype(sub_id)
    return {"ok": True}


# ==================================================================
# Items
# ==================================================================
@app.get("/api/items")
def get_items(
    category_id: Optional[int] = None,
    subtype_id: Optional[int] = None,
    search: Optional[str] = None,
    active_only: bool = True,
):
    return rows(db.get_items(category_id, subtype_id, active_only, search))


@app.get("/api/items/barcode/{code}")
def get_item_by_barcode(code: str):
    item = db.get_item_by_barcode(code)
    if not item:
        return {"found": False}
    return {"found": True, "item": row(item)}


@app.get("/api/items/find")
def find_item_by_name(name: str, category_id: int, subtype_id: Optional[int] = None):
    return row(db.find_item_by_name(category_id, subtype_id, name))


@app.get("/api/items/{item_id}")
def get_item(item_id: int):
    return row(db.get_item_by_id(item_id))


@app.post("/api/items", status_code=201)
def add_item(body: ItemIn):
    item_id = db.add_item(
        body.name,
        body.category_id,
        body.subtype_id,
        body.barcode,
        body.size,
        body.color,
        body.rate,
        body.stock_qty,
    )
    return {"ok": True, "id": item_id}


@app.put("/api/items/{item_id}")
def update_item(item_id: int, body: ItemIn):
    db.update_item(
        item_id,
        body.name,
        body.category_id,
        body.subtype_id,
        body.barcode,
        body.size,
        body.color,
        body.rate,
        body.stock_qty,
        body.active,
    )
    return {"ok": True}


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int):
    db.delete_item(item_id)
    return {"ok": True}


# ==================================================================
# Customers
# ==================================================================
@app.get("/api/customers")
def search_customers(search: str = ""):
    return rows(db.search_customers(search))


@app.get("/api/customers/phone/{phone}")
def get_customer_by_phone(phone: str):
    return row(db.get_customer_by_phone(phone))


@app.get("/api/customers/{cid}")
def get_customer(cid: int):
    c = db.get_customer_by_id(cid)
    if not c:
        return None
    total_spent, visit_count = db.get_customer_total_spent(cid)
    result = dict(c)
    result["total_spent"] = total_spent
    result["visit_count"] = visit_count
    return result


@app.post("/api/customers", status_code=201)
def add_customer(body: CustomerIn):
    cid = db.add_customer(
        body.name, body.phone or "", body.address or "", body.notes or ""
    )
    return {"ok": True, "id": cid}


@app.put("/api/customers/{cid}")
def update_customer(cid: int, body: CustomerIn):
    db.update_customer(
        cid, body.name, body.phone or "", body.address or "", body.notes or ""
    )
    return {"ok": True}


@app.delete("/api/customers/{cid}")
def delete_customer(cid: int, auth: DeleteAuth):
    _check_password(auth)
    db.delete_customer(cid)
    return {"ok": True}


@app.get("/api/customers/{cid}/history")
def customer_history(cid: int):
    return rows(db.get_customer_purchase_history(cid))


# ==================================================================
# Wishlist
# ==================================================================
@app.get("/api/customers/{cid}/wishlist")
def get_wishlist(cid: int):
    return rows(db.get_wishlist(cid))


@app.post("/api/customers/{cid}/wishlist", status_code=201)
def add_wishlist(cid: int, body: WishlistIn):
    db.add_wishlist(cid, body.description)
    return {"ok": True}


@app.put("/api/wishlist/{wid}")
def set_wishlist_fulfilled(wid: int, body: WishlistUpdate):
    db.set_wishlist_fulfilled(wid, body.fulfilled)
    return {"ok": True}


@app.delete("/api/wishlist/{wid}")
def delete_wishlist(wid: int):
    db.delete_wishlist(wid)
    return {"ok": True}


@app.get("/api/wishlist")
def all_open_wishlist():
    return rows(db.all_open_wishlist())


# ==================================================================
# Bills
# ==================================================================
@app.get("/api/bills")
def search_bills(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    search: Optional[str] = None,
):
    return rows(db.search_bills(date_from, date_to, search))


@app.get("/api/bills/next-number")
def next_bill_no():
    return {"bill_no": db.next_bill_no()}


@app.post("/api/bills", status_code=201)
def save_bill(body: BillIn):
    if not body.items:
        raise HTTPException(
            status_code=400,
            detail="Bill has no items",
        )

    bill_date = body.bill_date or datetime.now().strftime("%Y-%m-%d")

    try:
        datetime.strptime(
            bill_date,
            "%Y-%m-%d",
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid bill date. Expected YYYY-MM-DD.",
        )

    items = [it.dict() for it in body.items]

    bill_id, bill_no = db.save_bill(
        body.customer_id,
        items,
        body.subtotal,
        body.discount_percent,
        body.discount_amount,
        body.total,
        body.payment_mode,
        bill_date,
    )

    return {
        "ok": True,
        "bill_id": bill_id,
        "bill_no": bill_no,
    }


@app.get("/api/bills/{bill_id}")
def get_bill(bill_id: int):
    bill, items = db.get_bill(bill_id)
    if not bill:
        raise HTTPException(status_code=404, detail="Bill not found")
    return {"bill": row(bill), "items": rows(items)}


@app.delete("/api/bills/{bill_id}")
def delete_bill(bill_id: int, auth: DeleteAuth):
    _check_password(auth)
    db.delete_bill(bill_id)
    return {"ok": True}


# ==================================================================
# Statistics
# ==================================================================
@app.get("/api/stats/totals")
def stat_totals(date_from: Optional[str] = None, date_to: Optional[str] = None):
    return db.stat_totals(date_from, date_to)


@app.get("/api/stats/daily")
def stat_daily(date_from: Optional[str] = None, date_to: Optional[str] = None):
    return rows(db.stat_daily_sales(date_from, date_to))


@app.get("/api/stats/monthly")
def stat_monthly():
    return rows(db.stat_monthly_sales())


@app.get("/api/stats/top-items")
def stat_top_items(
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 10,
    by: str = "quantity",
):
    return rows(db.stat_top_items(date_from, date_to, limit, by))


@app.get("/api/stats/category")
def stat_category(date_from: Optional[str] = None, date_to: Optional[str] = None):
    return rows(db.stat_category_sales(date_from, date_to))


@app.get("/api/stats/top-customers")
def stat_top_customers(
    date_from: Optional[str] = None, date_to: Optional[str] = None, limit: int = 10
):
    return rows(db.stat_top_customers(date_from, date_to, limit))


# ==================================================================
# Frontend (served last so it doesn't shadow /api/* routes above)
# ==================================================================
@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


app.mount("/", StaticFiles(directory=FRONTEND_DIR), name="frontend")


if __name__ == "__main__":
    import uvicorn

    log.info(
        "Starting in dev mode via `python main.py` -- for normal use, run run_server.py instead."
    )
    uvicorn.run(app, host="0.0.0.0", port=5000)
