"""Purchases, godown inventory and a document-backed double-entry ledger.

Amounts in new tables are integer paise. Stock is stored in base pieces;
unit multipliers and costs are snapshotted at posting, never recalculated
from later master edits. Existing sales/payment tables remain authoritative.
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
import re

from money import money
from reporting import money_cents


def day(value=None):
    value = value or date.today().isoformat()
    date.fromisoformat(value)
    return value


def pieces(value):
    if isinstance(value, bool) or int(value) != value or value <= 0:
        raise ValueError('Quantity and unit size must be positive whole numbers.')
    return int(value)


def cents(value):
    result = money_cents(money(value))
    if result < 0:
        raise ValueError('Amounts cannot be negative.')
    return result


class CommerceQueries:
    def _ensure_commerce_schema(self):
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS godowns(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
            INSERT OR IGNORE INTO godowns(id,name) VALUES(1,'Default shop');
            CREATE TABLE IF NOT EXISTS stock_units(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                pieces INTEGER NOT NULL CHECK(pieces>0));
            INSERT OR IGNORE INTO stock_units(id,name,pieces) VALUES(1,'Piece',1);
            CREATE TABLE IF NOT EXISTS stock_groups(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                specification TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS stock_meta(item_id INTEGER PRIMARY KEY REFERENCES items(id),
                group_id INTEGER REFERENCES stock_groups(id), unit_id INTEGER NOT NULL DEFAULT 1 REFERENCES stock_units(id));
            CREATE TABLE IF NOT EXISTS godown_stock(godown_id INTEGER REFERENCES godowns(id), item_id INTEGER REFERENCES items(id),
                quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0), value_cents INTEGER,
                PRIMARY KEY(godown_id,item_id));
            CREATE TABLE IF NOT EXISTS opening_stock_values(godown_id INTEGER REFERENCES godowns(id), item_id INTEGER REFERENCES items(id),
                value_cents INTEGER NOT NULL, entry_date TEXT NOT NULL, PRIMARY KEY(godown_id,item_id));
            CREATE TABLE IF NOT EXISTS stock_movements(id INTEGER PRIMARY KEY, movement_date TEXT NOT NULL,
                godown_id INTEGER NOT NULL REFERENCES godowns(id), item_id INTEGER NOT NULL REFERENCES items(id),
                quantity INTEGER NOT NULL, value_cents INTEGER, reference TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_movement_day ON stock_movements(movement_date,godown_id);
            CREATE TABLE IF NOT EXISTS account_groups(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                nature TEXT NOT NULL CHECK(nature IN ('Asset','Liability','Income','Expense','Equity')));
            CREATE TABLE IF NOT EXISTS account_ledgers(id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                group_id INTEGER NOT NULL REFERENCES account_groups(id), kind TEXT NOT NULL CHECK(kind IN ('Supplier','General')),
                phone TEXT NOT NULL DEFAULT '', gst TEXT NOT NULL DEFAULT '', address TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS purchases(id INTEGER PRIMARY KEY AUTOINCREMENT, purchase_date TEXT NOT NULL,
                invoice_no TEXT NOT NULL, supplier_id INTEGER NOT NULL REFERENCES account_ledgers(id),
                seller_json TEXT NOT NULL, buyer_json TEXT NOT NULL, gst_enabled INTEGER NOT NULL,
                godown_id INTEGER NOT NULL REFERENCES godowns(id), net_cents INTEGER NOT NULL, gst_cents INTEGER NOT NULL,
                total_cents INTEGER NOT NULL, UNIQUE(supplier_id,invoice_no));
            CREATE TABLE IF NOT EXISTS purchase_lines(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL REFERENCES purchases(id),
                item_id INTEGER NOT NULL REFERENCES items(id), name TEXT NOT NULL, quantity INTEGER NOT NULL,
                unit_name TEXT NOT NULL, unit_pieces INTEGER NOT NULL, rate_cents INTEGER NOT NULL,
                discount_cents INTEGER NOT NULL, net_cents INTEGER NOT NULL, gst_rate REAL NOT NULL, gst_cents INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS purchase_payments(id INTEGER PRIMARY KEY, purchase_id INTEGER NOT NULL REFERENCES purchases(id),
                payment_date TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0), mode TEXT NOT NULL,
                advance_id INTEGER REFERENCES vouchers(id));
            CREATE TABLE IF NOT EXISTS vouchers(id INTEGER PRIMARY KEY AUTOINCREMENT, voucher_date TEXT NOT NULL,
                kind TEXT NOT NULL, debit_key TEXT NOT NULL, credit_key TEXT NOT NULL, amount_cents INTEGER NOT NULL CHECK(amount_cents>0),
                godown_id INTEGER NOT NULL REFERENCES godowns(id), notes TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_purchase_day ON purchases(purchase_date,godown_id);
            CREATE INDEX IF NOT EXISTS idx_purchase_payment ON purchase_payments(purchase_id);
            """)
            for table, name, definition in (
                ('bills','godown_id','INTEGER NOT NULL DEFAULT 1'),
                ('bills','sale_channel',"TEXT NOT NULL DEFAULT 'Retail'"),
                ('bill_items','cost_cents','INTEGER'),
                ('payments','advance_id','INTEGER REFERENCES vouchers(id)'),
                ('expenses','godown_id','INTEGER NOT NULL DEFAULT 1'),
            ):
                if not self._column_exists(c, table, name):
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
            # Only previously unallocated stock is migrated, without inventing its cost.
            c.execute("""INSERT INTO godown_stock(godown_id,item_id,quantity,value_cents)
                SELECT 1,id,MAX(stock_qty,0),CASE WHEN stock_qty<=0 THEN 0 ELSE NULL END FROM items i
                WHERE NOT EXISTS(SELECT 1 FROM godown_stock s WHERE s.item_id=i.id)""")
            for name,nature in [('Suppliers','Liability'),('General assets','Asset'),('General expenses','Expense'),('General income','Income'),('Capital','Equity')]:
                c.execute('INSERT OR IGNORE INTO account_groups(name,nature) VALUES(?,?)',(name,nature))

    def get_godowns(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM godowns ORDER BY id')]

    def get_stock_units(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM stock_units ORDER BY id')]

    def get_stock_groups(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM stock_groups ORDER BY name')]

    def get_account_groups(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute('SELECT * FROM account_groups ORDER BY name')]

    def save_master(self, kind, name, extra=None, record_id=None):
        tables = {'Godown':'godowns','Unit':'stock_units','Stock group':'stock_groups','Account group':'account_groups'}
        if kind not in tables or not name.strip():
            raise ValueError('Enter a master name.')
        name = name.strip()
        if kind=='Godown' and name.casefold()=='main godown':
            raise ValueError('Main Godown is the combined view, not a stock location.')
        fields, values = ['name'], [name]
        if kind=='Unit':
            fields.append('pieces'); values.append(pieces(extra))
            if record_id==1 and (values[1]!=1 or name!='Piece'):
                raise ValueError('The base Piece unit cannot be changed.')
        elif kind=='Stock group':
            fields.append('specification'); values.append(str(extra or '').strip())
        elif kind=='Account group':
            if extra not in ('Asset','Liability','Income','Expense','Equity'):
                raise ValueError('Choose the account nature.')
            fields.append('nature'); values.append(extra)
        with self._conn() as c:
            if record_id:
                c.execute(f"UPDATE {tables[kind]} SET "+','.join(f'{f}=?' for f in fields)+' WHERE id=?',values+[record_id])
                return record_id
            return c.execute(f"INSERT INTO {tables[kind]} ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",values).lastrowid

    def save_account_ledger(self, name, group_id, kind='Supplier', phone='', gst='', address='', ledger_id=None):
        if not name.strip() or kind not in ('Supplier','General'):
            raise ValueError('Enter a ledger name and valid type.')
        with self._conn() as c:
            values=(name.strip(),group_id,kind,phone.strip(),gst.strip().upper(),address.strip())
            if ledger_id:
                old=c.execute('SELECT kind FROM account_ledgers WHERE id=?',(ledger_id,)).fetchone()
                if not old or old['kind']!=kind:
                    raise ValueError('Ledger type cannot be changed after creation.')
                c.execute('UPDATE account_ledgers SET name=?,group_id=?,kind=?,phone=?,gst=?,address=? WHERE id=?',values+(ledger_id,))
                return ledger_id
            return c.execute('INSERT INTO account_ledgers(name,group_id,kind,phone,gst,address) VALUES(?,?,?,?,?,?)',values).lastrowid

    def get_suppliers(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM account_ledgers WHERE kind='Supplier' ORDER BY name")]

    def configure_stock_item(self, item_id, group_id, unit_id):
        with self._conn() as c:
            c.execute('INSERT INTO stock_meta(item_id,group_id,unit_id) VALUES(?,?,?) ON CONFLICT(item_id) DO UPDATE SET group_id=excluded.group_id,unit_id=excluded.unit_id',
                      (item_id,group_id,unit_id))

    def _location(self,c,godown_id):
        if not c.execute('SELECT 1 FROM godowns WHERE id=?',(godown_id,)).fetchone():
            raise ValueError('Choose a stock godown; Main Godown is a summary only.')

    def _stock_delta(self,c,item_id,godown_id,delta,value,reference,when=None):
        self._location(c,godown_id)
        if isinstance(delta, bool) or int(delta) != delta:
            raise ValueError('Stock movements must use whole pieces.')
        delta = int(delta)
        row=c.execute('SELECT * FROM godown_stock WHERE godown_id=? AND item_id=?',(godown_id,item_id)).fetchone()
        qty=row['quantity'] if row else 0
        old_value=row['value_cents'] if row else 0
        if qty+delta<0:
            raise ValueError('Not enough stock in the selected godown.')
        new_value=None if old_value is None or value is None else old_value+value
        if qty+delta==0:
            new_value=0
        c.execute('INSERT INTO godown_stock(godown_id,item_id,quantity,value_cents) VALUES(?,?,?,?)\n            ON CONFLICT(godown_id,item_id) DO UPDATE SET quantity=excluded.quantity,value_cents=excluded.value_cents',
            (godown_id,item_id,qty+delta,new_value))
        c.execute('UPDATE items SET stock_qty=(SELECT COALESCE(SUM(quantity),0) FROM godown_stock WHERE item_id=?) WHERE id=?',(item_id,item_id))
        c.execute('INSERT INTO stock_movements(movement_date,godown_id,item_id,quantity,value_cents,reference) VALUES(?,?,?,?,?,?)',
            (day(when),godown_id,item_id,delta,value,reference))

    def _stock_out(self,c,item_id,godown_id,quantity,reference,when):
        row=c.execute('SELECT * FROM godown_stock WHERE godown_id=? AND item_id=?',(godown_id,item_id)).fetchone()
        available=row['quantity'] if row else 0
        deducted=min(quantity,available)
        value=None if not row or row['value_cents'] is None or available<quantity else int((Decimal(row['value_cents'])*quantity/available).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
        removed=None if not row or row['value_cents'] is None else (row['value_cents'] if deducted==available else int((Decimal(row['value_cents'])*deducted/available).quantize(Decimal('1'), rounding=ROUND_HALF_UP)))
        if deducted:
            self._stock_delta(c,item_id,godown_id,-deducted,-removed if removed is not None else None,reference,when)
        return deducted,value

    def _catalog_stock(self,c,item_id,quantity):
        if isinstance(quantity, bool) or int(quantity) != quantity or quantity < 0:
            raise ValueError('Stock must be whole pieces, zero or greater.')
        # Legacy Inventory edits are aggregate totals: apply their difference to Default shop.
        row=c.execute('SELECT COALESCE(SUM(quantity),0) AS n FROM godown_stock WHERE item_id=?',(item_id,)).fetchone()
        delta=quantity-row['n']
        if delta:
            self._stock_delta(c,item_id,1,delta,None,'Inventory adjustment')
        else:
            c.execute('INSERT OR IGNORE INTO godown_stock(godown_id,item_id,quantity,value_cents) VALUES(1,?,0,0)',(item_id,))

    def set_opening_stock(self,item_id,godown_id,quantity,unit_cost):
        if int(quantity)!=quantity or quantity<0:
            raise ValueError('Opening stock must be whole pieces, zero or greater.')
        cost=cents(unit_cost)
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            self._location(c,godown_id)
            # Avoid rewriting the cost basis of a location after transactions.
            if c.execute("SELECT 1 FROM stock_movements WHERE item_id=? AND godown_id=? AND reference NOT IN ('Inventory adjustment','Opening stock')",(item_id,godown_id)).fetchone():
                raise ValueError('Opening stock can only be set before purchases, sales or transfers for this item and godown.')
            old=c.execute('SELECT quantity FROM godown_stock WHERE item_id=? AND godown_id=?',(item_id,godown_id)).fetchone()
            self._stock_delta(c,item_id,godown_id,int(quantity)-(old['quantity'] if old else 0),None,'Opening stock')
            c.execute('UPDATE godown_stock SET value_cents=? WHERE item_id=? AND godown_id=?',(int(quantity)*cost,item_id,godown_id))
            c.execute('INSERT INTO opening_stock_values(godown_id,item_id,value_cents,entry_date) VALUES(?,?,?,?) ON CONFLICT(godown_id,item_id) DO UPDATE SET value_cents=excluded.value_cents,entry_date=excluded.entry_date',
                      (godown_id,item_id,int(quantity)*cost,day()))

    def transfer_stock(self,item_id,source,destination,quantity):
        quantity=pieces(quantity)
        if source==destination:
            raise ValueError('Choose two different godowns.')
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT * FROM godown_stock WHERE item_id=? AND godown_id=?',(item_id,source)).fetchone()
            if not row or row['quantity']<quantity:
                raise ValueError('Not enough stock in the source godown.')
            _,value=self._stock_out(c,item_id,source,quantity,f'Transfer to godown {destination}',day())
            self._stock_delta(c,item_id,destination,quantity,value,f'Transfer from godown {source}')

    def stock_report(self,godown_id=None):
        with self._conn() as c:
            return [dict(r) for r in c.execute("""SELECT i.id,i.name,c.name AS category,g.name AS stock_group,
                g.specification,u.name AS unit,u.pieces AS unit_pieces,
                COALESCE(SUM(s.quantity),0) AS quantity,
                CASE WHEN SUM(CASE WHEN s.quantity>0 AND s.value_cents IS NULL THEN 1 ELSE 0 END)>0 THEN NULL
                     ELSE COALESCE(SUM(s.value_cents),0) END AS value_cents
                FROM items i JOIN categories c ON c.id=i.category_id
                LEFT JOIN stock_meta m ON m.item_id=i.id LEFT JOIN stock_groups g ON g.id=m.group_id
                LEFT JOIN stock_units u ON u.id=COALESCE(m.unit_id,1)
                LEFT JOIN godown_stock s ON s.item_id=i.id AND (? IS NULL OR s.godown_id=?)
                WHERE i.active=1 GROUP BY i.id ORDER BY c.name,i.name""",(godown_id,godown_id))]

    def quote_purchase(self,lines,gst_enabled):
        if not lines:
            raise ValueError('Add at least one purchase item.')
        result=[]
        with self._conn() as c:
            for line in lines:
                item=c.execute('SELECT name FROM items WHERE id=? AND active=1',(line['item_id'],)).fetchone()
                unit=c.execute('SELECT * FROM stock_units WHERE id=?',(line['unit_id'],)).fetchone()
                if not item or not unit:
                    raise ValueError('Choose an active item and valid unit.')
                qty=pieces(line['quantity']); rate=cents(line['rate']); discount=cents(line.get('discount',0))
                net=qty*rate-discount
                if net<0:
                    raise ValueError('Discount exceeds the line value.')
                gst=money(line.get('gst_rate',0)) if gst_enabled else 0
                if not 0<=gst<=100:
                    raise ValueError('GST must be between 0 and 100 percent.')
                tax=money_cents(Decimal(net)*Decimal(str(gst))/10000)
                result.append(dict(item_id=line['item_id'],name=item['name'],quantity=qty,unit_name=unit['name'],
                    unit_pieces=unit['pieces'],rate_cents=rate,discount_cents=discount,net_cents=net,gst_rate=gst,gst_cents=tax))
        return result

    def save_purchase(self,supplier_id,seller,buyer,invoice_no,lines,gst_enabled,godown_id=1,paid=0,mode='Cash',purchase_date=None):
        when=day(purchase_date)
        invoice_no=invoice_no.strip()
        if not invoice_no:
            raise ValueError('Supplier invoice number is required.')
        parties=[]
        for label,party in [('Seller',seller),('Buyer',buyer)]:
            party={k:str(party.get(k,'')).strip() for k in ('name','phone','gst','address')}
            party['gst']=party['gst'].upper()
            if not party['name'] or not party['phone']:
                raise ValueError(label+' name and phone are required.')
            if gst_enabled and not re.fullmatch(r'[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]',party['gst']):
                raise ValueError(label+' needs a valid 15-character GSTIN in With GST mode.')
            parties.append(party)
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            self._location(c,godown_id)
            if not c.execute("SELECT 1 FROM account_ledgers WHERE id=? AND kind='Supplier'",(supplier_id,)).fetchone():
                raise ValueError('Choose a supplier ledger.')
            rows=self.quote_purchase(lines,gst_enabled)
            net=sum(r['net_cents'] for r in rows); gst=sum(r['gst_cents'] for r in rows); total=net+gst
            paid=cents(paid)
            if paid>total:
                raise ValueError('Paid amount exceeds purchase total.')
            pid=c.execute('INSERT INTO purchases(purchase_date,invoice_no,supplier_id,seller_json,buyer_json,gst_enabled,godown_id,net_cents,gst_cents,total_cents) VALUES(?,?,?,?,?,?,?,?,?,?)',
                (when,invoice_no,supplier_id,json.dumps(parties[0]),json.dumps(parties[1]),bool(gst_enabled),godown_id,net,gst,total)).lastrowid
            for r in rows:
                c.execute('INSERT INTO purchase_lines(purchase_id,item_id,name,quantity,unit_name,unit_pieces,rate_cents,discount_cents,net_cents,gst_rate,gst_cents) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (pid,)+tuple(r[k] for k in ('item_id','name','quantity','unit_name','unit_pieces','rate_cents','discount_cents','net_cents','gst_rate','gst_cents')))
                self._stock_delta(c,r['item_id'],godown_id,r['quantity']*r['unit_pieces'],r['net_cents'],f'Purchase P-{pid}',when)
            if paid:
                c.execute('INSERT INTO purchase_payments(purchase_id,payment_date,amount_cents,mode) VALUES(?,?,?,?)',(pid,when,paid,mode))
            return pid

    def get_purchase(self,purchase_id):
        with self._conn() as c:
            row=c.execute("""SELECT p.*,g.name AS godown,
                (SELECT COALESCE(SUM(amount_cents),0) FROM purchase_payments WHERE purchase_id=p.id) AS paid_cents
                FROM purchases p JOIN godowns g ON g.id=p.godown_id WHERE p.id=?""",(purchase_id,)).fetchone()
            if not row:
                raise ValueError('Purchase not found.')
            return dict(row),[dict(r) for r in c.execute('SELECT * FROM purchase_lines WHERE purchase_id=? ORDER BY id',(purchase_id,))]

    def add_purchase_payment(self,purchase_id,amount,mode='Cash',payment_date=None):
        amount=cents(amount)
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            self._purchase_payment(c,purchase_id,amount,mode,day(payment_date))

    def _purchase_payment(self,c,purchase_id,amount,mode,when,advance_id=None):
        row=c.execute('SELECT purchase_date,total_cents-(SELECT COALESCE(SUM(amount_cents),0) FROM purchase_payments WHERE purchase_id=p.id) AS balance FROM purchases p WHERE id=?',(purchase_id,)).fetchone()
        if not row or amount<=0 or amount>row['balance'] or when<row['purchase_date']:
            raise ValueError('Payment must be positive, no more than the purchase balance, and dated on or after the purchase.')
        c.execute('INSERT INTO purchase_payments(purchase_id,payment_date,amount_cents,mode,advance_id) VALUES(?,?,?,?,?)',(purchase_id,when,amount,mode,advance_id))

    def _ledger_list(self,c):
        rows=[dict(key=k,name=n,nature=t,group=n,kind='System') for k,n,t in (
            ('cash','Cash / bank','Asset'),('inventory','Inventory','Asset'),('sales','Sales','Income'),
            ('cost','Cost of sales','Expense'),('input_gst','Input GST','Asset'),('output_gst','Output GST','Liability'),
            ('expenses','Operating expenses','Expense'),('equity','Opening stock equity','Equity'),('customer:0','Walk-in customers','Asset'))]
        rows += [dict(key='customer:'+str(r['id']),name=r['name']+' / '+(r['phone'] or ''),nature='Asset',group='Customers',kind='Customer') for r in c.execute('SELECT id,name,phone FROM customers ORDER BY name')]
        rows += [dict(r,key='ledger:'+str(r['id'])) for r in c.execute('SELECT l.*,g.name AS "group",g.nature FROM account_ledgers l JOIN account_groups g ON g.id=l.group_id ORDER BY l.name')]
        return rows

    def get_account_ledgers(self):
        with self._conn() as c:
            return self._ledger_list(c)

    def save_voucher(self,kind,debit_key,credit_key,amount,notes,godown_id=1,voucher_date=None):
        amount=cents(amount)
        if kind not in ('Payment','Receipt','Journal','Credit note','Debit note','Opening balance') or not notes.strip() or not amount:
            raise ValueError('Choose a voucher type, positive amount and narration.')
        if debit_key==credit_key:
            raise ValueError('Debit and credit accounts must differ.')
        if (kind=='Payment' and credit_key!='cash') or (kind=='Receipt' and debit_key!='cash'):
            raise ValueError('Payment credits Cash / bank; Receipt debits Cash / bank.')
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            keys={r['key'] for r in self._ledger_list(c)}
            if debit_key not in keys or credit_key not in keys:
                raise ValueError('Choose valid debit and credit ledgers.')
            self._location(c,godown_id)
            return c.execute('INSERT INTO vouchers(voucher_date,kind,debit_key,credit_key,amount_cents,godown_id,notes) VALUES(?,?,?,?,?,?,?)',
                (day(voucher_date),kind,debit_key,credit_key,amount,godown_id,notes.strip())).lastrowid

    def get_advances(self):
        with self._conn() as c:
            names={r['key']:r['name'] for r in self._ledger_list(c)}
            rows=c.execute("""SELECT v.*,amount_cents
                -(SELECT COALESCE(SUM(amount_cents),0) FROM purchase_payments WHERE advance_id=v.id)
                -(SELECT COALESCE(sum_money_cents(amount),0) FROM payments WHERE advance_id=v.id) AS remaining_cents
                FROM vouchers v WHERE kind IN ('Payment','Receipt') ORDER BY voucher_date,id""").fetchall()
            return [dict(r,party=names.get(r['debit_key'] if r['kind']=='Payment' else r['credit_key'],''))
                    for r in rows if r['remaining_cents']>0 and
                    (r['debit_key'].startswith('ledger:') if r['kind']=='Payment' else r['credit_key'].startswith('customer:'))]

    def apply_advance(self,voucher_id,document_type,document_id,amount,when=None):
        amount=cents(amount); when=day(when)
        with self._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            v=c.execute('SELECT * FROM vouchers WHERE id=?',(voucher_id,)).fetchone()
            used=c.execute('SELECT COALESCE(SUM(amount_cents),0) FROM purchase_payments WHERE advance_id=?',(voucher_id,)).fetchone()[0]
            used+=c.execute('SELECT COALESCE(sum_money_cents(amount),0) FROM payments WHERE advance_id=?',(voucher_id,)).fetchone()[0]
            if not v or amount<=0 or amount>v['amount_cents']-used or when<v['voucher_date']:
                raise ValueError('Advance is unavailable, insufficient, or later than the allocation date.')
            if document_type=='Purchase':
                p=c.execute('SELECT supplier_id,purchase_date FROM purchases WHERE id=?',(document_id,)).fetchone()
                if not p or v['kind']!='Payment' or v['debit_key']!='ledger:'+str(p['supplier_id']) or when<p['purchase_date']:
                    raise ValueError('Advance must belong to this supplier and precede allocation.')
                self._purchase_payment(c,document_id,amount,'Advance applied',when,voucher_id)
            elif document_type=='Sale':
                b=c.execute('SELECT * FROM bills WHERE id=?',(document_id,)).fetchone()
                paid=c.execute('SELECT COALESCE(sum_money_cents(amount),0) FROM payments WHERE bill_id=?',(document_id,)).fetchone()[0]
                if not b or v['kind']!='Receipt' or v['credit_key']!='customer:'+str(b['customer_id'] or 0) or amount>money_cents(b['total'])-paid or when<b['bill_date'][:10]:
                    raise ValueError('Advance must match the customer and outstanding sale balance.')
                c.execute('INSERT INTO payments(bill_id,customer_id,payment_date,amount,payment_mode,notes,advance_id) VALUES(?,?,?,?,?,?,?)',
                    (document_id,b['customer_id'],when,amount/100,'Advance applied',f'Voucher V-{voucher_id}',voucher_id))
            else:
                raise ValueError('Choose Purchase or Sale.')

    def _journal(self,c):
        # Derive postings from committed source documents. Deleting a legacy sale or
        # editing an expense cannot leave an orphan journal or duplicate payment.
        entries=[]
        def post(when,ref,debit,credit,amount,location,notes=''):
            if amount<0:
                debit,credit,amount=credit,debit,-amount
            if amount:
                entries.append(dict(date=when[:10],reference=ref,debit=debit,credit=credit,amount_cents=amount,godown_id=location,notes=notes))
        for r in c.execute('SELECT * FROM opening_stock_values'):
            post(r['entry_date'],'Opening stock '+str(r['item_id']),'inventory','equity',r['value_cents'],r['godown_id'])
        for r in c.execute('SELECT * FROM bills'):
            party='customer:'+str(r['customer_id'] or 0)
            post(r['bill_date'],r['bill_no'],party,'sales',money_cents(r['total'])-money_cents(r['gst_amount']),r['godown_id'])
            post(r['bill_date'],r['bill_no'],party,'output_gst',money_cents(r['gst_amount']),r['godown_id'])
        for r in c.execute('SELECT p.*,b.godown_id,b.bill_no FROM payments p JOIN bills b ON b.id=p.bill_id WHERE p.advance_id IS NULL'):
            post(r['payment_date'],'Receipt '+r['bill_no'],'cash','customer:'+str(r['customer_id'] or 0),money_cents(r['amount']),r['godown_id'])
        for r in c.execute('SELECT b.bill_date,b.bill_no,b.godown_id,i.cost_cents FROM bill_items i JOIN bills b ON b.id=i.bill_id WHERE i.cost_cents IS NOT NULL'):
            post(r['bill_date'],r['bill_no'],'cost','inventory',r['cost_cents'],r['godown_id'])
        for r in c.execute('SELECT * FROM purchases'):
            party='ledger:'+str(r['supplier_id'])
            post(r['purchase_date'],'P-'+str(r['id']),'inventory',party,r['net_cents'],r['godown_id'])
            post(r['purchase_date'],'P-'+str(r['id']),'input_gst',party,r['gst_cents'],r['godown_id'])
        for r in c.execute('SELECT pp.*,p.supplier_id,p.godown_id FROM purchase_payments pp JOIN purchases p ON p.id=pp.purchase_id WHERE pp.advance_id IS NULL'):
            post(r['payment_date'],'Payment P-'+str(r['purchase_id']),'ledger:'+str(r['supplier_id']),'cash',r['amount_cents'],r['godown_id'])
        for r in c.execute('SELECT * FROM expenses'):
            post(r['expense_date'],'Expense '+str(r['id']),'expenses','cash',money_cents(r['amount']),r['godown_id'],r['description'] or '')
        for r in c.execute('SELECT * FROM vouchers'):
            post(r['voucher_date'],'V-'+str(r['id']),r['debit_key'],r['credit_key'],r['amount_cents'],r['godown_id'],r['kind']+': '+r['notes'])
        return sorted(entries,key=lambda r:(r['date'],r['reference']))

    def account_statement(self,key=None,date_from='0001-01-01',date_to='9999-12-31',godown_id=None):
        day(date_from); day(date_to)
        with self._conn() as c:
            names={r['key']:r['name'] for r in self._ledger_list(c)}
            rows=[]; opening=0; balance=0
            for r in self._journal(c):
                if godown_id is not None and r['godown_id']!=godown_id:
                    continue
                if key and key not in (r['debit'],r['credit']):
                    continue
                signed=(r['amount_cents'] if r['debit']==key else -r['amount_cents']) if key else 0
                if r['date']<date_from:
                    opening+=signed; balance+=signed
                elif r['date']<=date_to:
                    balance+=signed
                    rows.append(dict(r,debit_name=names.get(r['debit'],r['debit']),credit_name=names.get(r['credit'],r['credit']),balance_cents=balance))
            return dict(opening_cents=opening,balance_cents=balance,rows=rows)

    def commerce_report(self,date_from,date_to,godown_id=None,channel=None):
        day(date_from); day(date_to)
        if date_from>date_to:
            raise ValueError('From date must not be after To date.')
        with self._conn() as c:
            c.execute('BEGIN')
            sales=[dict(r) for r in c.execute("""SELECT b.*,g.name AS godown,cu.name AS customer,
                (SELECT COALESCE(sum_money_cents(amount),0) FROM payments WHERE bill_id=b.id) AS paid_cents,
                (SELECT COALESCE(SUM(cost_cents),0) FROM bill_items WHERE bill_id=b.id) AS cost_cents,
                (CASE WHEN NOT EXISTS(SELECT 1 FROM bill_items WHERE bill_id=b.id) THEN 1 ELSE (SELECT COUNT(*) FROM bill_items WHERE bill_id=b.id AND cost_cents IS NULL) END) AS missing_cost
                FROM bills b JOIN godowns g ON g.id=b.godown_id LEFT JOIN customers cu ON cu.id=b.customer_id
                WHERE date(b.bill_date) BETWEEN ? AND ? AND (? IS NULL OR b.godown_id=?) AND (? IS NULL OR b.sale_channel=?)
                ORDER BY b.bill_date,b.id""",(date_from,date_to,godown_id,godown_id,channel,channel))]
            purchases=[dict(r) for r in c.execute("""SELECT p.*,l.name AS supplier,g.name AS godown,
                (SELECT COALESCE(SUM(amount_cents),0) FROM purchase_payments WHERE purchase_id=p.id) AS paid_cents
                FROM purchases p JOIN account_ledgers l ON l.id=p.supplier_id JOIN godowns g ON g.id=p.godown_id
                WHERE purchase_date BETWEEN ? AND ? AND (? IS NULL OR p.godown_id=?) ORDER BY purchase_date,p.id""",(date_from,date_to,godown_id,godown_id))]
            expenses=c.execute('SELECT COALESCE(sum_money_cents(amount),0) FROM expenses WHERE date(expense_date) BETWEEN ? AND ? AND (? IS NULL OR godown_id=?)',(date_from,date_to,godown_id,godown_id)).fetchone()[0]
            movements=[dict(r) for r in c.execute("""SELECT m.*,i.name,g.name AS godown FROM stock_movements m
                JOIN items i ON i.id=m.item_id JOIN godowns g ON g.id=m.godown_id WHERE movement_date BETWEEN ? AND ?
                AND (? IS NULL OR godown_id=?) ORDER BY m.id""",(date_from,date_to,godown_id,godown_id))]
            net_sales=sum(money_cents(r['total'])-money_cents(r['gst_amount']) for r in sales)
            cost=sum(r['cost_cents'] for r in sales)
            missing=sum(r['missing_cost'] for r in sales)
            # General journal income/expense adjustments contribute to the period result.
            kinds={r['key']:r['nature'] for r in self._ledger_list(c)}
            adjustment=0
            for r in c.execute("SELECT * FROM vouchers WHERE voucher_date BETWEEN ? AND ? AND kind!='Opening balance' AND (? IS NULL OR godown_id=?)",(date_from,date_to,godown_id,godown_id)):
                for key,sign in [(r['credit_key'],1),(r['debit_key'],-1)]:
                    if kinds.get(key) in ('Income','Expense'):
                        adjustment+=sign*r['amount_cents']
            return dict(sales=sales,purchases=purchases,movements=movements,net_sales_cents=net_sales,
                purchases_cents=sum(r['net_cents'] for r in purchases),cost_cents=cost,expenses_cents=expenses,
                adjustment_cents=adjustment,missing_cost=missing,profit_cents=None if missing else net_sales-cost-expenses+adjustment)
