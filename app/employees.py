"""Indexed employee records, one attendance entry per day, and linked cash expenses."""
import calendar
from datetime import date

ATTENDANCE_STATUSES = ('Full Day', 'Half Day', 'Absent')


def month_bounds(month):
    try:
        start = date.fromisoformat(month+'-01')
        if start.strftime('%Y-%m') != month:
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError('Choose a valid month (YYYY-MM).') from None
    end = start.replace(day=calendar.monthrange(start.year,start.month)[1])
    return start.isoformat(),end.isoformat()


class EmployeeQueries:
    def _ensure_employee_schema(self):
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Extend the repository's existing employee/attendance tables;
            # preserve IDs, role/phone fields and all historical attendance.
            if not self._column_exists(conn,'employees','name_key'):
                conn.execute('ALTER TABLE employees ADD COLUMN name_key TEXT')
                cursor = conn.execute('SELECT id,name FROM employees')
                while True:
                    rows = cursor.fetchmany(1000)
                    if not rows:
                        break
                    conn.executemany('UPDATE employees SET name_key=? WHERE id=?',
                        [(' '.join(r['name'].split()).casefold(),r['id']) for r in rows])
            conn.execute('CREATE INDEX IF NOT EXISTS idx_employee_name ON employees(name_key COLLATE NOCASE,id)')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_attendance_employee_day ON attendance(employee_id,date(attendance_date))')
            if not self._column_exists(conn,'expenses','employee_id'):
                conn.execute('ALTER TABLE expenses ADD COLUMN employee_id INTEGER REFERENCES employees(id) ON DELETE RESTRICT')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_employee_expenses ON expenses(employee_id,date(expense_date),id)')

    @staticmethod
    def _require_employee(conn,employee_id,active=False):
        row = conn.execute('SELECT * FROM employees WHERE id=?',(employee_id,)).fetchone()
        if row is None:
            raise ValueError('Employee not found. Select the employee again.')
        if active and not row['active']:
            raise ValueError('This employee is archived. Restore the employee before adding or changing records.')
        return row

    def save_employee(self,name,phone='',employee_id=None,active=True):
        name = ' '.join(name.split())
        if not name or len(name)>150:
            raise ValueError('Enter an employee name (up to 150 characters).')
        phone = phone.strip()
        if len(phone)>40:
            raise ValueError('Phone must be at most 40 characters.')
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if employee_id is None:
                return conn.execute('INSERT INTO employees(name,name_key,phone,active) VALUES (?,?,?,?)',
                    (name,name.casefold(),phone or None,int(bool(active)))).lastrowid
            self._require_employee(conn,employee_id)
            conn.execute('UPDATE employees SET name=?,name_key=?,phone=?,active=? WHERE id=?',
                         (name,name.casefold(),phone or None,int(bool(active)),employee_id))
            return employee_id

    def employee_page(self,search='',include_archived=False,limit=100,offset=0):
        term = ' '.join(search.split()).casefold().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        limit = max(1,min(int(limit),200))
        offset = max(0,int(offset))
        where = "name_key LIKE ? ESCAPE '\\'" + ('' if include_archived else ' AND active=1')
        with self._conn() as conn:
            conn.execute('BEGIN')
            total = conn.execute('SELECT COUNT(*) FROM employees WHERE '+where,(term+'%',)).fetchone()[0]
            offset = min(offset,max(0,(total-1)//limit)*limit)
            rows = conn.execute('SELECT * FROM employees WHERE '+where+' ORDER BY name_key COLLATE NOCASE,id LIMIT ? OFFSET ?',
                                (term+'%',limit,offset)).fetchall()
            return total,rows

    def set_employee_attendance(self,employee_id,attendance_date,status,notes=''):
        try:
            day = date.fromisoformat(attendance_date)
            if day.isoformat()!=attendance_date or day>date.today():
                raise ValueError
        except (TypeError,ValueError):
            raise ValueError('Attendance must use a valid date, today or earlier.') from None
        if status not in ATTENDANCE_STATUSES+('Not marked',):
            raise ValueError('Choose a valid attendance status.')
        if len(notes)>1000:
            raise ValueError('Attendance notes must be at most 1,000 characters.')
        with self._conn() as conn:
            conn.execute('BEGIN IMMEDIATE')
            self._require_employee(conn,employee_id,active=True)
            if status=='Not marked':
                conn.execute('DELETE FROM attendance WHERE employee_id=? AND attendance_date=?',(employee_id,attendance_date))
            else:
                conn.execute('''INSERT INTO attendance(employee_id,attendance_date,status,notes) VALUES (?,?,?,?)
                    ON CONFLICT(employee_id,attendance_date) DO UPDATE SET status=excluded.status,notes=excluded.notes''',
                    (employee_id,attendance_date,status,notes.strip()))

    def employee_month(self,employee_id,month,limit=100,offset=0):
        start,end = month_bounds(month)
        limit = max(1,min(int(limit),200))
        offset = max(0,int(offset))
        with self._conn() as conn:
            conn.execute('BEGIN')
            employee = dict(self._require_employee(conn,employee_id))
            attendance = [dict(r) for r in conn.execute('''SELECT * FROM attendance
                WHERE employee_id=? AND date(attendance_date)>=? AND date(attendance_date)<=? ORDER BY attendance_date''',(employee_id,start,end))]
            where = 'employee_id=? AND date(expense_date)>=? AND date(expense_date)<=?'
            params = (employee_id,start,end)
            count,total = conn.execute('SELECT COUNT(*),COALESCE(sum_money_cents(amount),0)/100.0 FROM expenses WHERE '+where,params).fetchone()
            offset = min(offset,max(0,(count-1)//limit)*limit)
            expenses = [dict(r) for r in conn.execute('SELECT * FROM expenses WHERE '+where+' ORDER BY date(expense_date) DESC,id DESC LIMIT ? OFFSET ?',params+(limit,offset))]
            categories = [dict(r) for r in conn.execute('SELECT category,COUNT(*) AS count,sum_money_cents(amount)/100.0 AS total FROM expenses WHERE '+where+' GROUP BY category ORDER BY category',params)]
        counts = {status:sum(r['status']==status for r in attendance) for status in ATTENDANCE_STATUSES}
        elapsed = max(0,(min(date.fromisoformat(end),date.today())-date.fromisoformat(start)).days+1)
        return dict(employee=employee,attendance=attendance,expenses=expenses,expense_count=count,
            expense_total=total,expense_categories=categories,counts=counts,
            days_attended=counts['Full Day']+counts['Half Day'],day_equivalents=counts['Full Day']+counts['Half Day']/2,
            unmarked=max(0,elapsed-len(attendance)),start=start,end=end)

    def export_employee_expenses(self,employee_id,month):
        start,end = month_bounds(month)
        with self._conn() as conn:
            conn.execute('BEGIN')
            employee = self._require_employee(conn,employee_id)
            for row in conn.execute('''SELECT * FROM expenses WHERE employee_id=? AND date(expense_date)>=? AND date(expense_date)<=?
                    ORDER BY date(expense_date) DESC,id DESC''',(employee_id,start,end)):
                yield dict(row,employee_name=employee['name'])
