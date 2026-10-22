"""Read-only reporting queries with inclusive dates and no payment/item join fan-out."""
from collections import defaultdict
from decimal import Decimal
import statistics
from itertools import groupby

from money import money


def money_cents(value):
    return int(Decimal(str(money(value or 0))) * 100)


class MoneySum:
    """Round the exact sum once, including imported fractional-cent amounts."""
    def __init__(self):
        self.total = Decimal(0)

    def step(self, value):
        if value is not None:
            self.total += Decimal(str(value))

    def finalize(self):
        return money_cents(self.total)


def cents(column):
    value = f'COALESCE({column},0)'
    return (f'(CASE WHEN ABS({value}*100-ROUND({value}*100)) > 0.000001 '
            f'THEN money_cents({value}) ELSE CAST(ROUND({value}*100) AS INTEGER) END)')


def date_clause(column, start, end, params):
    clause = ''
    if start:
        clause += f' AND date({column}) >= date(?)'
        params.append(start)
    if end:
        clause += f' AND date({column}) <= date(?)'
        params.append(end)
    return clause


def bill_condition(start, end, search, params, customer=None):
    condition = date_clause('b.bill_date',start,end,params)
    if search:
        condition += " AND (b.bill_no LIKE ? OR COALESCE(c.name,'Walk-in') LIKE ? OR c.phone LIKE ?)"
        params.extend([f'%{search}%']*3)
    if customer is not None:
        if customer == 0:
            condition += ' AND b.customer_id IS NULL'
        else:
            condition += ' AND b.customer_id=?'
            params.append(customer)
    return condition


def bill_rows(conn, start, end, search='', limit=None, offset=0, customer=None, stream=False):
    params = []
    query = f'''SELECT b.*, c.name AS customer_name, c.phone AS customer_phone,
        (SELECT COALESCE(SUM(quantity),0) FROM bill_items WHERE bill_id=b.id) AS piece_count,
        (SELECT COALESCE(sum_money_cents(amount),0)/100.0 FROM payments WHERE bill_id=b.id) AS paid
        FROM bills b LEFT JOIN customers c ON c.id=b.customer_id WHERE 1=1'''
    query += bill_condition(start,end,search,params,customer)
    query += ' ORDER BY b.bill_date DESC,b.id DESC'
    if limit is not None:
        query += ' LIMIT ? OFFSET ?'
        params.extend([limit,offset])
    def rows():
        for row in conn.execute(query,params):
            row = dict(row)
            row['total'],row['paid'] = money(row['total']),money(row['paid'])
            row['balance'] = max(money(row['total']-row['paid']),0)
            yield row
    return rows() if stream else list(rows())


def payment_rows(conn, start, end, search='', limit=None, offset=0, customer=None, stream=False, count_only=False):
    params = []
    query = 'SELECT ' + ('COUNT(*)' if count_only else 'p.*, b.bill_no, c.name AS customer_name, c.phone AS customer_phone')
    query += ' FROM payments p JOIN bills b ON b.id=p.bill_id LEFT JOIN customers c ON c.id=b.customer_id WHERE 1=1'
    query += date_clause('p.payment_date',start,end,params)
    query += bill_condition(None,None,search,params,customer)
    if count_only:
        return conn.execute(query,params).fetchone()[0]
    query += ' ORDER BY p.payment_date DESC,p.id DESC'
    if limit is not None:
        query += ' LIMIT ? OFFSET ?'
        params.extend([limit,offset])
    rows = (dict(r) for r in conn.execute(query,params))
    return rows if stream else list(rows)


class ReportingQueries:
    def report_bills(self, date_from=None, date_to=None, search=''):
        with self._conn() as conn:
            return bill_rows(conn, date_from, date_to, search)

    def report_payments(self, date_from=None, date_to=None, search=''):
        with self._conn() as conn:
            return payment_rows(conn, date_from, date_to, search)

    def sales_page(self, start=None, end=None, search='', limit=200, offset=0, customer=None):
        with self._conn() as conn:
            conn.execute('BEGIN')
            params = []
            condition = bill_condition(start,end,search,params,customer)
            summary = conn.execute(f'''SELECT COUNT(*) AS count,
                COALESCE(SUM({cents('b.total')}),0)/100.0 AS revenue,
                COALESCE(SUM((SELECT SUM(quantity) FROM bill_items WHERE bill_id=b.id)),0) AS pieces
                FROM bills b LEFT JOIN customers c ON c.id=b.customer_id WHERE 1=1 {condition}''',params).fetchone()
            offset = min(offset,max(0,(summary['count']-1)//limit)*limit)
            return dict(summary= dict(summary), rows=bill_rows(conn,start,end,search,limit,offset,customer))

    def customer_page(self, search='', limit=200, offset=0):
        with self._conn() as conn:
            conn.execute('BEGIN')
            where = 'WHERE c.name LIKE ? OR c.phone LIKE ?' if search else ''
            args = [f'%{search}%']*2 if search else []
            count = conn.execute(f'SELECT COUNT(*) FROM customers c {where}',args).fetchone()[0]
            offset = min(offset,max(0,(count-1)//limit)*limit)
            rows = conn.execute(f'''SELECT c.*,
                COALESCE((SELECT SUM({cents('b.total')}) FROM bills b WHERE b.customer_id=c.id),0)/100.0 AS total_spent
                FROM customers c {where} ORDER BY c.name,c.id LIMIT ? OFFSET ?''',args+[limit,offset]).fetchall()
            return count, rows

    def expense_categories(self):
        with self._conn() as conn:
            return [r[0] for r in conn.execute('SELECT DISTINCT category FROM expenses ORDER BY category')]

    def expense_page(self, start=None, end=None, category=None, limit=200, offset=0):
        with self._conn() as conn:
            conn.execute('BEGIN')
            params = []
            where = date_clause('expense_date',start,end,params)
            if category:
                where += ' AND category=?'
                params.append(category)
            count,total = conn.execute(f'SELECT COUNT(*),COALESCE(sum_money_cents(amount),0)/100.0 FROM expenses WHERE 1=1 {where}',params).fetchone()
            offset = min(offset,max(0,(count-1)//limit)*limit)
            rows = conn.execute(f'SELECT expenses.*,(SELECT name FROM employees WHERE employees.id=expenses.employee_id) AS employee_name FROM expenses WHERE 1=1 {where} ORDER BY expense_date DESC,id DESC LIMIT ? OFFSET ?',params+[limit,offset]).fetchall()
            return dict(count=count,total=total,rows=rows)

    def balance_summary(self, start=None, end=None, search=''):
        with self._conn() as conn:
            conn.execute('BEGIN')
            params = []
            condition = bill_condition(start,end,search,params)
            query = f'''SELECT COALESCE(b.customer_id,0) AS id,
                COALESCE(c.name,'Walk-in') AS name, COALESCE(c.phone,'') AS phone,
                {cents('b.total')} AS billed,
                COALESCE((SELECT sum_money_cents(p.amount) FROM payments p WHERE p.bill_id=b.id),0) AS paid
                FROM bills b LEFT JOIN customers c ON c.id=b.customer_id WHERE 1=1 {condition}'''
            rows = conn.execute(f'''SELECT id,name,phone,COUNT(*) AS bill_count,
                SUM(billed)/100.0 AS billed,SUM(paid)/100.0 AS paid,
                SUM(MAX(billed-paid,0))/100.0 AS balance FROM ({query})
                GROUP BY id ORDER BY name,id''',params)
            groups = {r['id']:dict(r) for r in rows}
            args = []
            payment_where = date_clause('p.payment_date',start,end,args)
            if search:
                payment_where += " AND (b.bill_no LIKE ? OR COALESCE(c.name,'Walk-in') LIKE ? OR c.phone LIKE ?)"
                args.extend([f'%{search}%']*3)
            for r in conn.execute(f'''SELECT DISTINCT COALESCE(b.customer_id,0) AS id,
                COALESCE(c.name,'Walk-in') AS name,COALESCE(c.phone,'') AS phone
                FROM payments p JOIN bills b ON b.id=p.bill_id LEFT JOIN customers c ON c.id=b.customer_id
                WHERE 1=1 {payment_where}''',args):
                groups.setdefault(r['id'],dict(r, bill_count=0,billed=0,paid=0,balance=0))
            return groups

    def customer_ledger_page(self, customer, start=None, end=None, search='', limit=200, offset=0, payment_offset=0):
        with self._conn() as conn:
            conn.execute('BEGIN')
            args = []
            condition = bill_condition(start,end,search,args,customer)
            count = conn.execute(f'SELECT COUNT(*) FROM bills b LEFT JOIN customers c ON c.id=b.customer_id WHERE 1=1 {condition}',args).fetchone()[0]
            bills = bill_rows(conn,start,end,search,limit,min(offset,max(0,(count-1)//limit)*limit),customer)
            payments = payment_rows(conn,start,end,search,None,0,customer, count_only=True)
            rows = payment_rows(conn,start,end,search,limit,min(payment_offset,max(0,(payments-1)//limit)*limit),customer)
            return dict(bills=bills,bill_count=count,payments=rows,payment_count=payments)

    def export_bill_rows(self, start=None, end=None, search='', customer=None):
        # One read snapshot for the whole file; fetch incrementally, never skip pages.
        with self._conn() as conn:
            conn.execute('BEGIN')
            yield from bill_rows(conn,start,end,search,customer=customer, stream=True)

    def export_payment_rows(self, start=None, end=None, search='', customer=None):
        with self._conn() as conn:
            conn.execute('BEGIN')
            yield from payment_rows(conn,start,end,search,customer=customer, stream=True)

    def export_expense_rows(self, start=None, end=None, category=None):
        with self._conn() as conn:
            conn.execute('BEGIN')
            params = []
            where = date_clause('expense_date',start,end,params)
            if category:
                where += ' AND category=?'
                params.append(category)
            yield from conn.execute(f'SELECT expenses.*,(SELECT name FROM employees WHERE employees.id=expenses.employee_id) AS employee_name FROM expenses WHERE 1=1 {where} ORDER BY expense_date DESC,id DESC',params)

    def report_statistics(self, date_from=None, date_to=None):
        params = []
        where = date_clause('b.bill_date', date_from, date_to, params)
        # An indexed temporary classification avoids repeated line scans and
        # SQLite planner-dependent quadratic joins on large all-time reports.
        classification = f"""SELECT b.id,b.total,
            CASE WHEN {cents('b.total')} = (
                SELECT CASE WHEN MAX(CASE WHEN
                    ABS(bi.subtotal*100-ROUND(bi.subtotal*100)) > 0.000001 OR
                    ABS(bi.gst_amount*100-ROUND(bi.gst_amount*100)) > 0.000001 OR
                    ((bi.subtotal < 0 OR bi.gst_amount < 0) AND NOT EXISTS(SELECT 1 FROM exchanges e WHERE e.exchange_bill_id=b.id)) THEN 1 ELSE 0 END)=0
                    THEN SUM({cents('bi.subtotal')}+{cents('bi.gst_amount')}) END
                FROM bill_items bi WHERE bi.bill_id=b.id)
                THEN 0 ELSE 1 END AS legacy
            FROM bills b WHERE 1=1 {where}"""
        products = defaultdict(lambda: {'quantity': 0, 'cents': 0})
        with self._conn() as conn:
            conn.execute('BEGIN')
            days = [dict(r) for r in conn.execute(f"""
                SELECT date(b.bill_date) AS date, SUM({cents('b.total')}) AS revenue_cents,
                    COUNT(*) AS bill_count FROM bills b WHERE 1=1 {where}
                GROUP BY date(b.bill_date) ORDER BY date(b.bill_date)""", params)]
            summary = conn.execute(f"""SELECT
                COALESCE(SUM((SELECT SUM(quantity) FROM bill_items WHERE bill_id=b.id)),0) AS pieces,
                COALESCE(SUM(MAX({cents('b.total')} - COALESCE(
                    (SELECT sum_money_cents(p.amount) FROM payments p WHERE p.bill_id=b.id),0),0)),0) AS outstanding
                FROM bills b WHERE 1=1 {where}""", params).fetchone()
            def dated_sum(table, column):
                args = []
                condition = date_clause(column, date_from, date_to, args)
                return conn.execute(f'SELECT COALESCE(sum_money_cents(amount),0) FROM {table} WHERE 1=1 {condition}', args).fetchone()[0]
            expenses = dated_sum('expenses', 'expense_date') / 100
            collected = dated_sum('payments', 'payment_date') / 100
            conn.execute('CREATE TEMP TABLE report_bill_classes (id INTEGER PRIMARY KEY, total REAL, legacy INTEGER)')
            conn.execute('INSERT INTO report_bill_classes '+classification, params)
            conn.execute('CREATE INDEX report_bill_legacy ON report_bill_classes(legacy,id)')
            rows = conn.execute(f"""
                SELECT COALESCE(i.name,bi.item_name_snapshot) AS name,
                    COALESCE(c.name,bi.category_snapshot,'Uncategorised') AS category,
                    COALESCE(NULLIF(TRIM(st.name),''), CASE WHEN i.id IS NULL
                        THEN 'Unknown brand / style' ELSE 'No brand / style' END) AS brand,
                    SUM(bi.quantity) AS quantity,
                    SUM({cents('bi.subtotal')}+{cents('bi.gst_amount')}) AS revenue_cents
                FROM report_bill_classes s JOIN bill_items bi ON bi.bill_id=s.id
                LEFT JOIN items i ON i.id=bi.item_id LEFT JOIN categories c ON c.id=i.category_id
                LEFT JOIN subtypes st ON st.id=i.subtype_id
                WHERE s.legacy=0 GROUP BY 1, 2, 3""")
            for row in rows:
                products[(row['name'],row['category'],row['brand'])] = dict(quantity=row['quantity'], cents=row['revenue_cents'])
            legacy = conn.execute("""
                SELECT s.id AS report_bill_id, s.total AS report_total, bi.*,
                    COALESCE(i.name,bi.item_name_snapshot,'Unspecified items') AS name,
                    COALESCE(c.name,bi.category_snapshot,'Uncategorised') AS category,
                    COALESCE(NULLIF(TRIM(st.name),''), CASE WHEN i.id IS NULL
                        THEN 'Unknown brand / style' ELSE 'No brand / style' END) AS brand
                FROM report_bill_classes s LEFT JOIN bill_items bi ON bi.bill_id=s.id
                LEFT JOIN items i ON i.id=bi.item_id LEFT JOIN categories c ON c.id=i.category_id
                LEFT JOIN subtypes st ON st.id=i.subtype_id
                WHERE s.legacy=1 ORDER BY s.id, bi.id""")
            for _, group in groupby(legacy, key=lambda r: r['report_bill_id']):
                items = list(group)  # Bound memory to one historical bill.
                total = money(items[0]['report_total'])
                if items[0]['id'] is None:
                    if total:
                        products[('Unspecified items','Uncategorised','Unknown brand / style')]['cents'] += money_cents(total)
                    continue
                weights = [max(Decimal(str(i['subtotal'])) + Decimal(str(i['gst_amount'] or 0)), 0) for i in items]
                denominator = sum(weights)
                if not denominator:
                    weights = [Decimal(str(i['quantity'])) for i in items]
                    denominator = sum(weights)
                if not denominator:
                    weights = [Decimal(1)] * len(items)
                    denominator = len(items)
                target = money_cents(total)
                shares = [Decimal(target)*w/denominator for w in weights] if denominator else []
                allocated = [int(v) for v in shares]
                for idx in sorted(range(len(shares)), key=lambda i: shares[i]-allocated[i], reverse=True)[:target-sum(allocated)]:
                    allocated[idx] += 1
                for item, value in zip(items, allocated):
                    data = products[(item['name'],item['category'],item['brand'])]
                    data['quantity'] += item['quantity']
                    data['cents'] += value

        months = defaultdict(lambda: {'cents':0, 'bill_count':0})
        for day in days:
            month = months[day['date'][:7]]
            month['cents'] += day['revenue_cents']
            month['bill_count'] += day['bill_count']
        revenue = sum(d['revenue_cents'] for d in days)/100
        count = sum(d['bill_count'] for d in days)
        daily = [dict(date=d['date'],revenue=d['revenue_cents']/100,bill_count=d['bill_count']) for d in days]
        values = [d['revenue'] for d in daily]
        totals = dict(revenue=revenue, expenses=expenses, net_profit=money(revenue-expenses),
                      payments_collected=collected, outstanding=summary['outstanding']/100,
                      bill_count=count, pieces=summary['pieces'], avg_bill=money(revenue/count) if count else 0,
                      mean_daily=statistics.mean(values) if values else 0,
                      median_daily=statistics.median(values) if values else 0,
                      std_daily=statistics.pstdev(values) if values else 0, active_days=len(days))
        categories = defaultdict(lambda: {'quantity':0,'cents':0})
        brands = defaultdict(lambda: {'quantity':0,'cents':0})
        product_rows = []
        for (name,category,brand), data in products.items():
            product_rows.append(dict(name=name,category=category,brand=brand,quantity=data['quantity'],revenue=data['cents']/100))
            categories[category]['quantity'] += data['quantity']
            categories[category]['cents'] += data['cents']
            brands[(category,brand)]['quantity'] += data['quantity']
            brands[(category,brand)]['cents'] += data['cents']
        return dict(totals=totals, daily=daily,
                    monthly=[dict(month=m,revenue=d['cents']/100,bill_count=d['bill_count']) for m,d in sorted(months.items())],
                    products=sorted(product_rows,key=lambda r:(-r['revenue'],r['name'],r['category'],r['brand'])),
                    brands=sorted([dict(category=c,brand=b,quantity=d['quantity'],revenue=d['cents']/100)
                                   for (c,b),d in brands.items()],key=lambda r:(-r['revenue'],r['category'],r['brand'])),
                    categories=sorted([dict(category=n,quantity=d['quantity'],revenue=d['cents']/100)
                                       for n,d in categories.items()],key=lambda r:(-r['revenue'],r['category'])))
