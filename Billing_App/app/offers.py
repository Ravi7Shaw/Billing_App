"""Admin-defined bundles, indexed by category and brand; exact cent allocation."""
from collections import defaultdict
from bisect import bisect_left
from datetime import date
import math
import sqlite3

from money import money, line_amount, tax_amount, sum_money
from reporting import money_cents


OFFER_LOOKUP = '''SELECT o.* FROM offer_scopes s JOIN offers o ON o.id=s.offer_id
    WHERE s.enabled=1 AND s.category_id=? AND s.brand_key=?'''


def allocate_cents(total, weights):
    """Largest remainders with stable input-order ties and no float arithmetic."""
    denominator = sum(weights)
    if not denominator:
        return [0] * len(weights)
    allocated = [total*w//denominator for w in weights]
    order = sorted(range(len(weights)), key=lambda i: (total*weights[i]) % denominator, reverse=True)
    for i in order[:total-sum(allocated)]:
        allocated[i] += 1
    return allocated


def offer_taxes(net_lines):
    """Round a bundle's GST once per tax rate, then allocate exact cents to lines.

    Each entry has net, gst_rate and offer_id. Ordinary lines retain their
    existing per-line rounding. Splitting a bundle among products cannot lose GST.
    """
    taxes = [tax_amount(r['net'],r['gst_rate']) for r in net_lines]
    groups = defaultdict(list)
    for i,row in enumerate(net_lines):
        if row.get('offer_id'):
            groups[(row['offer_id'],float(row['gst_rate']))].append(i)
    for (_,rate),indexes in groups.items():
        weights = [money_cents(net_lines[i]['net']) for i in indexes]
        total = money_cents(tax_amount(sum(weights)/100,rate))
        for i,cents in zip(indexes,allocate_cents(total,weights)):
            taxes[i] = cents/100
    return taxes


class OfferQueries:
    def _ensure_offer_schema(self):
        with self._conn() as conn:
            conn.executescript('''
                CREATE TABLE IF NOT EXISTS offers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    bundle_quantity INTEGER NOT NULL CHECK(bundle_quantity>=2),
                    bundle_cents INTEGER NOT NULL CHECK(bundle_cents>0),
                    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1))
                );
                CREATE INDEX IF NOT EXISTS idx_offers_active ON offers(active,id);
                CREATE INDEX IF NOT EXISTS idx_offers_name ON offers(active,name COLLATE NOCASE,id);
                CREATE TABLE IF NOT EXISTS offer_scopes (
                    offer_id INTEGER NOT NULL REFERENCES offers(id) ON DELETE CASCADE,
                    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE RESTRICT,
                    brand_key INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    PRIMARY KEY(offer_id,category_id,brand_key)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_offer_scope_active
                    ON offer_scopes(category_id,brand_key) WHERE enabled=1;
            ''')
            conn.execute('BEGIN IMMEDIATE')
            columns = {r['name'] for r in conn.execute('PRAGMA table_info(bill_items)')}
            for name, declaration in (
                ('offer_id_snapshot', 'INTEGER'),
                ('offer_name_snapshot', 'TEXT'),
                ('offer_discount', 'REAL NOT NULL DEFAULT 0'),
            ):
                if name not in columns:
                    conn.execute(f'ALTER TABLE bill_items ADD COLUMN {name} {declaration}')

    def save_offer(self, name, quantity, bundle_price, scopes, offer_id=None, active=True):
        name = name.strip()
        if not name or len(name)>120:
            raise ValueError('Enter an offer name (up to 120 characters).')
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 2<=quantity<=999999:
            raise ValueError('Bundle quantity must be a whole number from 2 to 999999.')
        price = money(bundle_price)
        if not 0<price<=999999999:
            raise ValueError('Enter a bundle price greater than zero, up to 999999999, before GST.')
        scopes = sorted(set((int(c),int(b or 0)) for c,b in scopes))
        if not scopes:
            raise ValueError('Choose at least one category / brand combination.')
        if len(scopes)>100:
            raise ValueError('Use up to 100 category / brand combinations per offer.')
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            for category,brand in scopes:
                row = conn.execute('SELECT active FROM categories WHERE id=?',(category,)).fetchone()
                if not row or (active and not row['active']):
                    raise ValueError('Choose an active category.')
                if brand and not conn.execute('SELECT 1 FROM subtypes WHERE id=? AND category_id=?',(brand,category)).fetchone():
                    historical = offer_id is not None and conn.execute(
                        'SELECT 1 FROM offer_scopes WHERE offer_id=? AND category_id=? AND brand_key=?',
                        (offer_id,category,brand)).fetchone()
                    if active or not historical:
                        raise ValueError('The selected brand does not belong to this category.')
            if offer_id is None:
                offer_id = conn.execute('INSERT INTO offers(name,bundle_quantity,bundle_cents,active) VALUES (?,?,?,?)',
                    (name,quantity,money_cents(price),int(active))).lastrowid
            else:
                if not conn.execute('SELECT 1 FROM offers WHERE id=?',(offer_id,)).fetchone():
                    raise ValueError('Offer no longer exists. Refresh the list.')
                conn.execute('UPDATE offers SET name=?,bundle_quantity=?,bundle_cents=?,active=? WHERE id=?',
                    (name,quantity,money_cents(price),int(active),offer_id))
                conn.execute('DELETE FROM offer_scopes WHERE offer_id=?',(offer_id,))
            try:
                conn.executemany('INSERT INTO offer_scopes(offer_id,category_id,brand_key,enabled) VALUES (?,?,?,?)',
                    [(offer_id,c,b,int(active)) for c,b in scopes])
            except sqlite3.IntegrityError as exc:
                raise ValueError('A selected category / brand already has an active offer. Edit or disable that offer first.') from exc
            return offer_id

    def get_offer(self, offer_id):
        with self._conn() as conn:
            conn.execute('BEGIN')
            row = conn.execute('SELECT * FROM offers WHERE id=?',(offer_id,)).fetchone()
            if not row:
                return None
            return dict(row, scopes=[tuple(r) for r in conn.execute(
                'SELECT category_id,brand_key FROM offer_scopes WHERE offer_id=? ORDER BY category_id,brand_key',(offer_id,))])

    def set_offer_active(self, offer_id, active):
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if not conn.execute('SELECT 1 FROM offers WHERE id=?',(offer_id,)).fetchone():
                raise ValueError('Offer no longer exists.')
            if active and conn.execute('''SELECT 1 FROM offer_scopes s JOIN categories c ON c.id=s.category_id
                    WHERE s.offer_id=? AND (c.active!=1 OR (s.brand_key!=0 AND NOT EXISTS(
                        SELECT 1 FROM subtypes b WHERE b.id=s.brand_key AND b.category_id=s.category_id))) LIMIT 1''',
                    (offer_id,)).fetchone():
                raise ValueError('Replace archived categories or removed brands before enabling this offer.')
            try:
                conn.execute('UPDATE offer_scopes SET enabled=? WHERE offer_id=?',(int(active),offer_id))
            except sqlite3.IntegrityError as exc:
                raise ValueError('Another active offer uses this category / brand. Disable it first.') from exc
            conn.execute('UPDATE offers SET active=? WHERE id=?',(int(active),offer_id))

    def offer_page(self, active=True, limit=200, offset=0, search=''):
        with self._conn() as conn:
            conn.execute('BEGIN')
            where = 'active=?'
            params = [int(active)]
            if search:
                where += " AND name LIKE ? ESCAPE '\\'"
                params.append(search.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%')
            total = conn.execute(f'SELECT COUNT(*) FROM offers WHERE {where}',params).fetchone()[0]
            offset = min(offset,max(0,(total-1)//limit)*limit)
            rows = conn.execute(f'SELECT * FROM offers WHERE {where} ORDER BY name COLLATE NOCASE,id LIMIT ? OFFSET ?',
                                params+[limit,offset]).fetchall()
            return total,rows

    def quote_offers(self, cart):
        with self._conn() as conn:
            conn.execute('BEGIN')
            return self._quote_offers_conn(conn,cart)

    def _quote_offers_conn(self, conn, cart):
        result = [dict(offer_id=None,offer_name='',offer_discount=0.0) for _ in cart]
        catalog = {}
        ids = sorted({r['item_id'] for r in cart if r.get('item_id')})
        for start in range(0,len(ids),500):
            chunk = ids[start:start+500]
            query = f'''SELECT i.*,c.active AS category_active FROM items i
                JOIN categories c ON c.id=i.category_id WHERE i.id IN ({','.join('?' for _ in chunk)})'''
            catalog.update((r['id'],r) for r in conn.execute(query,chunk))
        matches = {}
        groups = defaultdict(list)
        rules = {}
        for index,row in enumerate(cart):
            item = catalog.get(row.get('item_id'))
            if not item or not item['active'] or not item['category_active']:
                continue
            # Admin custom prices retain their manual pricing; never stack a bundle onto them.
            if money(row['rate']) != money(item['rate']):
                continue
            qty = row['qty']
            if qty<=0 or int(qty)!=qty:
                raise ValueError('Offer quantities must be positive whole numbers.')
            key = (item['category_id'],item['subtype_id'] or 0)
            if key not in matches:
                rule = conn.execute(OFFER_LOOKUP,key).fetchone()
                if rule is None and key[1]:
                    rule = conn.execute(OFFER_LOOKUP,(key[0],0)).fetchone()
                matches[key] = rule
            rule = matches[key]
            if rule is not None:
                rules[rule['id']] = rule
                groups[rule['id']].append(index)
        for rule_id,indexes in groups.items():
            indexes.sort(key=lambda i:(cart[i].get('item_id') or 0,i))
            rule = rules[rule_id]
            quantity = sum(int(cart[i]['qty']) for i in indexes)
            bundle_count = quantity//rule['bundle_quantity']
            if not bundle_count:
                continue
            # Highest priced eligible pieces enter bundles first; leftover pieces
            # remain at their regular rates. Work per cart line, never per piece.
            ordered = sorted(indexes,key=lambda i:(-money_cents(cart[i]['rate']),cart[i].get('item_id') or 0,i))
            counts, costs = [0], [0]
            for i in ordered:
                counts.append(counts[-1]+int(cart[i]['qty']))
                costs.append(costs[-1]+int(cart[i]['qty'])*money_cents(cart[i]['rate']))
            def cost(pieces):
                index = bisect_left(counts,pieces)
                if counts[index]==pieces:
                    return costs[index]
                return costs[index-1]+(pieces-counts[index-1])*money_cents(cart[ordered[index-1]]['rate'])
            # Do not cancel a useful bundle just because cheaper extra pieces
            # would form another bundle costing more than their normal price.
            low, high = 0,bundle_count
            while low<high:
                middle = (low+high+1)//2
                if cost(middle*rule['bundle_quantity'])-cost((middle-1)*rule['bundle_quantity'])>rule['bundle_cents']:
                    low = middle
                else:
                    high = middle-1
            bundle_count = low
            remaining = bundle_count*rule['bundle_quantity']
            chosen = {}
            for i in ordered:
                take = min(int(cart[i]['qty']),remaining)
                chosen[i] = take*money_cents(cart[i]['rate'])
                remaining -= take
            weights = [chosen[i] for i in indexes]
            saving = max(0,sum(weights)-bundle_count*rule['bundle_cents'])
            if not saving:
                continue
            for i,discount in zip(indexes,allocate_cents(saving,weights)):
                # A participating line can round to zero saving. Keep its
                # bundle identity so GST is still rounded with the whole group.
                if chosen[i]:
                    result[i] = dict(offer_id=rule_id,offer_name=rule['name'],offer_discount=discount/100)
        return result

    def _validate_offer_bill(self, conn, values):
        """Requote inside BEGIN IMMEDIATE, before writing stock, bill or payment."""
        employee = values['employee_pricing']
        items = values['items']
        cart = [dict(item_id=i.get('item_id'),qty=i['quantity'],rate=i['rate']) for i in items]
        expected = self._quote_offers_conn(conn,cart)
        expected_taxes = offer_taxes([dict(net=i['subtotal'],gst_rate=i.get('gst_rate',0),offer_id=q['offer_id']) for i,q in zip(items,expected)])
        for item,quote,line_tax in zip(items,expected,expected_taxes):
            if employee:
                catalog = conn.execute('''SELECT i.*,c.active AS category_active FROM items i
                    JOIN categories c ON c.id=i.category_id WHERE i.id=?''',(item.get('item_id'),)).fetchone()
                if (not catalog or not catalog['active'] or not catalog['category_active']
                        or item['quantity']<=0 or int(item['quantity'])!=item['quantity']
                        or money(item['rate'])!=money(catalog['rate'])
                        or float(item.get('gst_rate',0))!=float(catalog['gst_rate'])):
                    raise PermissionError('Catalog price changed or item is unavailable. Remove it and add it again.')
            if (money(item.get('offer_discount',0))!=quote['offer_discount']
                    or item.get('offer_id_snapshot')!=quote['offer_id']
                    or (item.get('offer_name_snapshot') or '')!=quote['offer_name']):
                raise PermissionError('Offers changed. Refresh offers and check the total before saving.')
            gross = line_amount(item['quantity'],item['rate'])
            if employee and money(item['subtotal']) != money(gross-quote['offer_discount']):
                raise PermissionError('Employees can only apply automatic offers, not manual discounts.')
            if money(item.get('gst_amount',0)) != line_tax:
                raise PermissionError('Line GST does not match the final offer price.')
        if employee:
            gross = sum_money(line_amount(i['quantity'],i['rate']) for i in items)
            discount = sum_money(q['offer_discount'] for q in expected)
            net = sum_money(i['subtotal'] for i in items)
            gst = sum_money(i.get('gst_amount',0) for i in items)
            rates = {float(i.get('gst_rate',0)) for i in items}
            bill_rate = next(iter(rates)) if len(rates)==1 else 0.0
            total = sum_money([net,gst])
            if (values['bill_date'] != date.today().isoformat()
                    or values['payment_mode'] not in ('Cash','Card','UPI','Other')
                    or money(values['subtotal'])!=gross or money(values['discount_amount'])!=discount
                    or not math.isclose(float(values['discount_percent']),discount/gross*100 if gross else 0,abs_tol=1e-8)
                    or money(values['taxable_amount'])!=net or float(values['gst_rate'])!=bill_rate
                    or money(values['gst_amount'])!=gst or money(values['total'])!=total
                    or money(values['initial_payment_amount'])!=total):
                raise PermissionError('Employees must collect the full approved price including GST.')
