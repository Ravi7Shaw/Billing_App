"""Admin exchanges: immutable source bills, prorated credits and atomic settlement."""
from datetime import date
import hashlib
import json

from money import money, line_amount
from reporting import money_cents
from offers import allocate_cents


class ExchangeQueries:
    def _ensure_exchange_schema(self):
        with self._conn() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS exchanges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_bill_id INTEGER NOT NULL REFERENCES bills(id) ON DELETE RESTRICT,
                    exchange_bill_id INTEGER NOT NULL UNIQUE REFERENCES bills(id) ON DELETE RESTRICT,
                    request_key TEXT NOT NULL UNIQUE,
                    request_hash TEXT NOT NULL,
                    returned_cents INTEGER NOT NULL CHECK(returned_cents>=0),
                    replacement_cents INTEGER NOT NULL CHECK(replacement_cents>returned_cents),
                    exchange_date TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_exchanges_source ON exchanges(source_bill_id);
                CREATE TABLE IF NOT EXISTS exchange_returns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange_id INTEGER NOT NULL REFERENCES exchanges(id) ON DELETE RESTRICT,
                    source_line_id INTEGER NOT NULL REFERENCES bill_items(id) ON DELETE RESTRICT,
                    quantity INTEGER NOT NULL CHECK(quantity>0),
                    net_cents INTEGER NOT NULL CHECK(net_cents>=0),
                    gst_cents INTEGER NOT NULL CHECK(gst_cents>=0),
                    UNIQUE(exchange_id,source_line_id)
                );
                CREATE INDEX IF NOT EXISTS idx_exchange_returns_line ON exchange_returns(source_line_id);
                CREATE INDEX IF NOT EXISTS idx_bills_number_lookup ON bills(bill_no COLLATE NOCASE);
            ''')

    def find_exchange_bills(self, search, by_phone=True, limit=100, offset=0):
        search = search.strip()
        if not search:
            return 0,[]
        with self._conn() as conn:
            conn.execute('BEGIN')
            condition = 'c.phone=?' if by_phone else 'b.bill_no=? COLLATE NOCASE'
            query = f'''FROM bills b LEFT JOIN customers c ON c.id=b.customer_id WHERE {condition}'''
            count = conn.execute('SELECT COUNT(*) '+query,(search,)).fetchone()[0]
            offset = min(offset,max(0,(count-1)//limit)*limit)
            rows = conn.execute('''SELECT b.*,c.name AS customer_name,c.phone AS customer_phone,
                COALESCE((SELECT sum_money_cents(amount) FROM payments WHERE bill_id=b.id),0)/100.0 AS paid '''
                +query+' ORDER BY b.bill_date DESC,b.id DESC LIMIT ? OFFSET ?',(search,limit,offset)).fetchall()
            return count,rows

    def _exchange_source_conn(self, conn, bill_id):
        bill = conn.execute('''SELECT b.*,c.name AS customer_name,c.phone AS customer_phone,
                COALESCE((SELECT sum_money_cents(amount) FROM payments WHERE bill_id=b.id),0)/100.0 AS paid
                FROM bills b LEFT JOIN customers c ON c.id=b.customer_id WHERE b.id=?''',(bill_id,)).fetchone()
        if bill is None:
            raise ValueError('Bill not found. Search again.')
        rows = [dict(r) for r in conn.execute('''SELECT bi.*,
                COALESCE((SELECT SUM(quantity) FROM exchange_returns WHERE source_line_id=bi.id),0) AS returned_qty
                FROM bill_items bi WHERE bi.bill_id=? AND bi.quantity>0 ORDER BY bi.id''',(bill_id,))]
        is_exchange = conn.execute('SELECT 1 FROM exchanges WHERE exchange_bill_id=?',(bill_id,)).fetchone() is not None
        weights = [max(0,money_cents(r['subtotal'])+money_cents(r['gst_amount'])) for r in rows]
        if is_exchange or sum(weights)==money_cents(bill['total']):
            nets = [money_cents(r['subtotal']) for r in rows]
            taxes = [money_cents(r['gst_amount']) for r in rows]
        else:
            # Historical bill-level discounts/taxes may not be in line snapshots.
            weights = weights if sum(weights) else [int(r['quantity']) for r in rows]
            totals = allocate_cents(money_cents(bill['total']),weights)
            tax_total = min(max(0,money_cents(bill['gst_amount'])),money_cents(bill['total']))
            taxes = allocate_cents(tax_total,totals)
            nets = [total-tax for total,tax in zip(totals,taxes)]
        for row,net,tax in zip(rows,nets,taxes):
            row.update(available_qty=max(0,row['quantity']-row['returned_qty']),net_cents=net,tax_cents=tax)
        return dict(bill=dict(bill),lines=rows,
                    outstanding=max(0,money(bill['total']-bill['paid'])))

    def exchange_source(self, bill_id):
        with self._conn() as conn:
            conn.execute('BEGIN')
            return self._exchange_source_conn(conn,bill_id)

    def _quote_exchange_conn(self, conn, bill_id, selections):
        source = self._exchange_source_conn(conn,bill_id)
        if source['outstanding']:
            raise ValueError('This bill has an outstanding balance. Collect its payment in Balances before exchanging items.')
        if not selections:
            raise ValueError('Select at least one item and quantity to exchange.')
        by_id = {r['id']:r for r in source['lines']}
        selected = []
        for line_id,quantity in sorted(selections.items()):
            row = by_id.get(line_id)
            if row is None:
                raise ValueError('A selected item does not belong to this bill.')
            if (isinstance(quantity,bool) or not isinstance(quantity,int) or quantity<=0
                    or quantity>row['available_qty'] or int(row['quantity'])!=row['quantity']):
                raise ValueError('Exchange quantity exceeds the pieces still available. Reload the bill.')
            qty,before = int(row['quantity']),int(row['returned_qty'])
            # Cumulative allocation keeps all partial exchanges equal to the
            # original paid value, including the final rounding cent.
            total = row['net_cents']+row['tax_cents']
            if row['net_cents']<0 or row['tax_cents']<0:
                raise ValueError('This historical line needs a pricing correction before it can be exchanged.')
            gross_before = total*before//qty
            gross_after = total*(before+quantity)//qty
            # Split GST from the allocated gross value so an exact per-piece
            # paid price stays exact even when net and tax each have fractions.
            tax = (row['tax_cents']*gross_after//total-row['tax_cents']*gross_before//total) if total else 0
            net = gross_after-gross_before-tax
            if net<0 or tax<0:
                raise ValueError('This historical line needs a pricing correction before it can be exchanged.')
            selected.append(dict(row,return_qty=quantity,return_net=net,return_tax=tax))
        net = sum(r['return_net'] for r in selected)
        tax = sum(r['return_tax'] for r in selected)
        return dict(source=source['bill'],lines=selected,net_cents=net,gst_cents=tax,credit_cents=net+tax)

    def quote_exchange(self, bill_id, selections):
        with self._conn() as conn:
            conn.execute('BEGIN')
            return self._quote_exchange_conn(conn,bill_id,selections)

    def save_exchange(self, source_bill_id, selections, items, expected_credit, paid_now,
                      payment_mode, request_key):
        if not request_key or len(request_key)>80:
            raise ValueError('An exchange reference is required.')
        if not items:
            raise ValueError('Add the replacement items or new purchases.')
        if payment_mode not in ('Cash','Card','UPI','Other'):
            raise ValueError('Choose a payment method for the difference.')
        fingerprint = hashlib.sha256(json.dumps([source_bill_id,sorted(selections.items()),items,
            money(expected_credit),money(paid_now),payment_mode],sort_keys=True).encode()).hexdigest()
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            previous = conn.execute('''SELECT b.id,b.bill_no,e.request_hash FROM exchanges e
                JOIN bills b ON b.id=e.exchange_bill_id WHERE e.request_key=?''',(request_key,)).fetchone()
            if previous:
                if previous['request_hash']!=fingerprint:
                    raise ValueError('This exchange reference was already used for a different bill.')
                return previous['id'],previous['bill_no']
            quote = self._quote_exchange_conn(conn,source_bill_id,selections)
            if money_cents(expected_credit)!=quote['credit_cents']:
                raise ValueError('The return value changed. Reload the original bill and review the exchange.')
            for item in items:
                qty = item['quantity']
                if isinstance(qty,bool) or not isinstance(qty,int) or qty<=0:
                    raise ValueError('Replacement quantities must be positive whole numbers.')
                if (money(item['rate'])<0 or not 0<=money(item['subtotal'])<=line_amount(qty,item['rate'])
                        or not 0<=float(item.get('gst_rate',0))<=100 or money(item.get('gst_amount',0))<0):
                    raise ValueError('Invalid replacement price or GST.')
            # Check current automatic offers and GST with the write lock held.
            self._validate_offer_bill(conn,dict(employee_pricing=False,items=items))
            gross = sum(money_cents(line_amount(i['quantity'],i['rate'])) for i in items)
            net = sum(money_cents(i['subtotal']) for i in items)
            gst = sum(money_cents(i.get('gst_amount',0)) for i in items)
            replacement = net+gst
            difference = replacement-quote['credit_cents']
            if difference<=0:
                raise ValueError('New purchases must cost more than the returned items. Add items or choose a higher-value replacement. No refund or store credit is issued.')
            if money_cents(paid_now)!=difference:
                raise ValueError('Collect the full difference shown for this exchange.')
            day = date.today().isoformat()
            bill_no = self._next_bill_no_conn(conn,day)
            conn.execute('''INSERT INTO bill_sequences(date_key,last_seq) VALUES (?,?)
                ON CONFLICT(date_key) DO UPDATE SET last_seq=excluded.last_seq''',
                (day.replace('-',''),int(bill_no.rsplit('-',1)[1])))
            discount = gross-net
            subtotal = gross-quote['net_cents']
            total_gst = gst-quote['gst_cents']
            rates = {float(i.get('gst_rate',0)) for i in items} | {float(r['gst_rate']) for r in quote['lines']}
            rate = next(iter(rates)) if len(rates)==1 else 0.0
            bill_id = conn.execute('''INSERT INTO bills(bill_no,customer_id,bill_date,subtotal,discount_percent,
                discount_amount,total,payment_mode,taxable_amount,gst_rate,gst_amount)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(bill_no,quote['source']['customer_id'],day,subtotal/100,
                discount/subtotal*100 if subtotal>0 else 0,discount/100,difference/100,payment_mode,
                (net-quote['net_cents'])/100,rate,total_gst/100)).lastrowid
            godown_id = quote['source']['godown_id']
            conn.execute('UPDATE bills SET godown_id=?,sale_channel=? WHERE id=?',
                         (godown_id, quote['source']['sale_channel'], bill_id))
            exchange_id = conn.execute('''INSERT INTO exchanges(source_bill_id,exchange_bill_id,request_key,
                request_hash,returned_cents,replacement_cents,exchange_date) VALUES (?,?,?,?,?,?,?)''',
                (source_bill_id,bill_id,request_key,fingerprint,quote['credit_cents'],replacement,day)).lastrowid
            # Restock first: a same-product exchange can use the pieces brought back.
            for row in quote['lines']:
                qty = row['return_qty']
                source_cost = row.get('cost_cents')
                cost = (source_cost * (row['returned_qty'] + qty) // row['quantity'] - source_cost * row['returned_qty'] // row['quantity']) if source_cost is not None else None
                if row['item_id']:
                    self._stock_delta(conn, row['item_id'], godown_id, qty, cost, 'Return '+bill_no, day)
                conn.execute('''INSERT INTO bill_items(bill_id,item_id,item_name_snapshot,category_snapshot,
                    quantity,rate,subtotal,gst_rate,gst_amount,stock_deducted,cost_cents) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (bill_id,row['item_id'],row['item_name_snapshot'],row['category_snapshot'],-qty,
                     row['rate'],-row['return_net']/100,row['gst_rate'],-row['return_tax']/100,
                     -qty if row['item_id'] else 0, -cost if cost is not None else None))
                conn.execute('''INSERT INTO exchange_returns(exchange_id,source_line_id,quantity,net_cents,gst_cents)
                    VALUES (?,?,?,?,?)''',(exchange_id,row['id'],qty,row['return_net'],row['return_tax']))
            for item in items:
                stock = None
                if item.get('item_id'):
                    stock = conn.execute('SELECT stock_qty FROM items WHERE id=?',(item['item_id'],)).fetchone()
                    if stock is None:
                        raise ValueError('A replacement item no longer exists. Reload the catalog.')
                deducted, cost = self._stock_out(conn, item['item_id'], godown_id, item['quantity'], bill_no, day) if stock else (0, None)
                conn.execute('''INSERT INTO bill_items(bill_id,item_id,item_name_snapshot,category_snapshot,
                    quantity,rate,subtotal,gst_rate,gst_amount,stock_deducted,cost_cents,
                    offer_id_snapshot,offer_name_snapshot,offer_discount) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (bill_id,item.get('item_id'),item['name'],item.get('category',''),item['quantity'],
                     money(item['rate']),money(item['subtotal']),float(item.get('gst_rate',0)),money(item.get('gst_amount',0)),
                     deducted,cost,item.get('offer_id_snapshot'),item.get('offer_name_snapshot'),money(item.get('offer_discount',0))))
            conn.execute('''INSERT INTO payments(bill_id,customer_id,payment_date,amount,payment_mode,notes)
                VALUES (?,?,?,?,?,?)''',(bill_id,quote['source']['customer_id'],day,difference/100,payment_mode,
                                       'Exchange difference from '+quote['source']['bill_no']))
            return bill_id,bill_no
