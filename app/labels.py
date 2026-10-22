"""Durable label batches: no automatic retries after uncertain printer output.

Catalog snapshots and barcode aliases are frozen per job. Stock is posted once
when the spool submission succeeds (or the admin reconciles an uncertain job).
PDFs and reprints never add stock. No database transaction spans printer I/O.
"""
import hashlib
import json
from datetime import datetime

from commerce import cents, pieces
from label_design import validate_design


class LabelQueries:
    def _ensure_label_schema(self):
        with self._conn() as c:
            c.executescript('''
            CREATE TABLE IF NOT EXISTS label_templates(
                id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, document TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS barcode_aliases(
                code TEXT PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id));
            CREATE TABLE IF NOT EXISTS label_batches(
                id INTEGER PRIMARY KEY AUTOINCREMENT, request_key TEXT NOT NULL UNIQUE, fingerprint TEXT NOT NULL,
                item_id INTEGER NOT NULL REFERENCES items(id), godown_id INTEGER NOT NULL REFERENCES godowns(id),
                quantity INTEGER NOT NULL CHECK(quantity>0), add_stock INTEGER NOT NULL,
                cost_cents INTEGER, document TEXT NOT NULL, metadata TEXT NOT NULL, printer TEXT NOT NULL,
                dpi INTEGER NOT NULL, offset_x REAL NOT NULL, offset_y REAL NOT NULL,
                status TEXT NOT NULL, stock_posted INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')), notes TEXT NOT NULL DEFAULT '');
            ''')

    def _check_barcode_alias(self,c,code,item_id=None):
        if code:
            row=c.execute('SELECT item_id FROM barcode_aliases WHERE code=?',(code.strip(),)).fetchone()
            if row and row['item_id']!=item_id:
                raise ValueError('This barcode belongs to another item in label history.')

    def get_label_templates(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM label_templates ORDER BY name')]

    def save_label_template(self,name,document):
        validate_design(document)
        if not name.strip():
            raise ValueError('Enter a template name.')
        encoded=json.dumps(document,sort_keys=True)
        if len(encoded)>5_000_000:
            raise ValueError('Template is too large.')
        with self._conn() as c:
            c.execute('INSERT INTO label_templates(name,document) VALUES(?,?) ON CONFLICT(name) DO UPDATE SET document=excluded.document',(name.strip(),encoded))

    def prepare_label_batch(self,request_key,item_id,new_item,godown_id,quantity,add_stock,unit_cost,
                            document,metadata,printer,dpi,offsets=(0,0)):
        quantity=pieces(quantity)
        if quantity>10000 or not request_key or len(request_key)>100:
            raise ValueError('A batch supports 1–10,000 labels and requires a request reference.')
        if dpi not in (203,300,600):
            raise ValueError('Choose 203, 300 or 600 DPI to match the printer.')
        payloads=validate_design(document,metadata,dpi,offsets)
        if not payloads:
            raise ValueError('Add at least one barcode or QR element.')
        cost=None if unit_cost is None else cents(unit_cost)
        serialized=json.dumps([item_id,new_item,godown_id,quantity,bool(add_stock),cost,document,metadata,printer,dpi,offsets],sort_keys=True)
        fingerprint=hashlib.sha256(serialized.encode()).hexdigest()
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            previous=c.execute('SELECT id,fingerprint FROM label_batches WHERE request_key=?',(request_key,)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint:
                    raise ValueError('This print request has changed. Prepare a new batch.')
                return previous['id']
            self._location(c,godown_id)
            if item_id is None:
                if not add_stock or not new_item or not new_item.get('name','').strip():
                    raise ValueError('A new item needs a name and Add new stock mode.')
                category=new_item.get('category_id');subtype=new_item.get('subtype_id')
                if not c.execute('SELECT 1 FROM categories WHERE id=? AND active=1',(category,)).fetchone():
                    raise ValueError('Choose an active category.')
                if subtype is not None and not c.execute('SELECT 1 FROM subtypes WHERE id=? AND category_id=?',(subtype,category)).fetchone():
                    raise ValueError('Brand/style does not belong to this category.')
                # A brand typed in the fixed form is created only in this confirmed transaction.
                # Export/cancellation never creates a master, and item failures roll it back.
                brand_name=new_item.get('new_subtype_name')
                if subtype is None and brand_name:
                    brand_name=str(brand_name).strip()
                    if not 1<=len(brand_name)<=100:raise ValueError('Enter a brand/style up to 100 characters.')
                    existing=c.execute('SELECT id FROM subtypes WHERE category_id=? AND name=? COLLATE NOCASE',(category,brand_name)).fetchone()
                    subtype=existing['id'] if existing else c.execute('INSERT INTO subtypes(category_id,name) VALUES(?,?)',(category,brand_name)).lastrowid
                rate=cents(new_item.get('rate',0))/100
                code=str(new_item.get('barcode','')).strip()
                if not code:
                    raise ValueError('A catalog barcode is required.')
                if c.execute('SELECT 1 FROM barcode_aliases WHERE code=?',(code,)).fetchone():
                    raise ValueError('This barcode already belongs to a label item.')
                item_id=c.execute('INSERT INTO items(name,category_id,subtype_id,barcode,size,color,rate,stock_qty) VALUES(?,?,?,?,?,?,?,0)',
                    (new_item['name'].strip(),category,subtype,code,new_item.get('size',''),new_item.get('color',''),rate)).lastrowid
                c.execute('INSERT INTO godown_stock(godown_id,item_id,quantity,value_cents) VALUES(?,?,0,0)',(godown_id,item_id))
            item=c.execute('SELECT i.*,ca.name AS category_name,s.name AS subtype_name FROM items i JOIN categories ca ON ca.id=i.category_id LEFT JOIN subtypes s ON s.id=i.subtype_id WHERE i.id=? AND i.active=1',(item_id,)).fetchone()
            if not item:
                raise ValueError('Item is inactive or missing. Refresh the catalog.')
            # Confirmations were for these catalog facts. Recheck under the write lock.
            for key in ('name','size','color'):
                if metadata.get(key,'')!=str(item[key] or ''):
                    raise ValueError('Item details changed. Review the preview and confirm again.')
            if metadata.get('rate')!=f"{float(item['rate']):.2f}" or metadata.get('category')!=item['category_name'] or metadata.get('brand')!=str(item['subtype_name'] or ''):
                raise ValueError('Catalog pricing/group changed. Review the preview and confirm again.')
            if item['barcode'] and metadata.get('barcode')!=item['barcode']:
                raise ValueError('Item barcode changed. Review the preview again.')
            if add_stock and c.execute("SELECT 1 FROM label_batches WHERE item_id=? AND godown_id=? AND add_stock=1 AND status IN ('prepared','sending','uncertain')",(item_id,godown_id)).fetchone():
                raise ValueError('Resolve the earlier new-stock print batch for this item/godown before starting another.')
            for code in list(dict.fromkeys(payloads+[metadata['barcode']])):
                if not code or len(code.encode('utf-8'))>2000 or code!=code.strip() or any(ord(ch)<32 for ch in code):
                    raise ValueError('Barcode data must be nonempty without control characters or surrounding whitespace. Use {metadata} for JSON text.')
                conflict=c.execute('SELECT id FROM items WHERE barcode=? AND id<>?',(code,item_id)).fetchone()
                alias=c.execute('SELECT item_id FROM barcode_aliases WHERE code=?',(code,)).fetchone()
                if conflict or (alias and alias['item_id']!=item_id):
                    raise ValueError('This barcode payload belongs to a different item. Include {barcode} to make it unique.')
                c.execute('INSERT OR IGNORE INTO barcode_aliases(code,item_id) VALUES(?,?)',(code,item_id))
            return c.execute('''INSERT INTO label_batches(request_key,fingerprint,item_id,godown_id,quantity,add_stock,cost_cents,
                document,metadata,printer,dpi,offset_x,offset_y,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'prepared')''',
                (request_key,fingerprint,item_id,godown_id,quantity,bool(add_stock),cost,json.dumps(document),json.dumps(metadata),printer,dpi,*offsets)).lastrowid

    def prepare_label_reprint(self,source_id,request_key,quantity,printer):
        quantity=pieces(quantity)
        if quantity>10000:
            raise ValueError('Maximum 10,000 labels per batch.')
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            source=c.execute('SELECT * FROM label_batches WHERE id=?',(source_id,)).fetchone()
            if not source or source['status'] not in ('submitted','cancelled'):
                raise ValueError('Resolve the source job before reprinting.')
            fingerprint=hashlib.sha256(json.dumps([source_id,quantity,printer]).encode()).hexdigest()
            previous=c.execute('SELECT id,fingerprint FROM label_batches WHERE request_key=?',(request_key,)).fetchone()
            if previous:
                if previous['fingerprint']!=fingerprint:raise ValueError('Print request changed.')
                return previous['id']
            return c.execute("""INSERT INTO label_batches(request_key,fingerprint,item_id,godown_id,quantity,add_stock,cost_cents,
                document,metadata,printer,dpi,offset_x,offset_y,status,notes) VALUES(?,?,?,?,?,0,NULL,?,?,?,?,?,?,'prepared',?)""",
                (request_key,fingerprint,source['item_id'],source['godown_id'],quantity,source['document'],source['metadata'],printer,
                 source['dpi'],source['offset_x'],source['offset_y'],'Reprint of L-'+str(source_id))).lastrowid

    def get_label_batch(self,batch_id):
        with self._conn() as c:
            row=c.execute('SELECT * FROM label_batches WHERE id=?',(batch_id,)).fetchone()
            if not row:raise ValueError('Label batch not found.')
            return dict(row)

    def get_label_batches(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute('''SELECT b.*,i.name,g.name AS godown FROM label_batches b
                JOIN items i ON i.id=b.item_id JOIN godowns g ON g.id=b.godown_id ORDER BY b.id DESC LIMIT 200''')]

    def claim_label_batch(self,batch_id):
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            changed=c.execute("UPDATE label_batches SET status='sending' WHERE id=? AND status='prepared'",(batch_id,))
            if changed.rowcount!=1:
                raise ValueError('This batch was already attempted. Check its output in History before reprinting.')

    def settle_label_batch(self,batch_id,printed,notes=''):
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM label_batches WHERE id=?',(batch_id,)).fetchone()
            if not row:raise ValueError('Label batch not found.')
            if row['status']=='submitted':
                if printed:return
                raise ValueError('A submitted batch cannot be cancelled; use an inventory correction if needed.')
            if row['status']=='cancelled':
                raise ValueError('This batch was cancelled. Create a new batch.')
            if printed and row['status'] not in ('sending','uncertain'):
                raise ValueError('The batch has not been sent to a printer.')
            if printed and row['add_stock'] and not row['stock_posted']:
                value=None if row['cost_cents'] is None else row['cost_cents']*row['quantity']
                self._stock_delta(c,row['item_id'],row['godown_id'],row['quantity'],value,'Label batch L-'+str(batch_id))
            c.execute('UPDATE label_batches SET status=?,stock_posted=?,notes=? WHERE id=?',
                ('submitted' if printed else 'cancelled',int(bool(printed and row['add_stock'])),notes,batch_id))

    def mark_label_uncertain(self,batch_id,notes):
        with self._conn() as c:
            c.execute("UPDATE label_batches SET status='uncertain',notes=? WHERE id=? AND status='sending'",(notes,batch_id))
