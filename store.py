"""
Lightweight JSON-file persistence layer for Money Mafia — Kuri Ledger.

Replaces the old Flask-SQLAlchemy/SQLite models with a single human-readable
JSON file (data/money_mafia.json). Every entity is stored as a plain dict in
that file and handed back to the app as a SimpleNamespace so the existing
templates (which use dot-notation like `d.member.name`) keep working
unchanged.

This is intentionally simple, not a general-purpose ORM: it re-reads/re-scans
lists on every query, which is fine at this scale (a few dozen members and a
few hundred records at most).
"""
import os
import json
import threading
from datetime import date, datetime
from types import SimpleNamespace

from werkzeug.exceptions import NotFound
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_FILE = os.environ.get('DATA_FILE', os.path.join(BASE_DIR, 'data', 'money_mafia.json'))

_lock = threading.Lock()

DEFAULT_ADMIN_USERNAME = 'admin'
DEFAULT_ADMIN_PASSWORD = 'admin123'  # change this immediately after first login

DEFAULT_RULES = """MONEY MAFIA — KURI LEDGER — GROUP FUND RULES

1. Monthly Contribution
   Every member contributes a fixed amount each month toward the common savings fund.

2. Monthly Lottery
   On a fixed day each month, a lot is drawn among members. The winner receives the
   lottery prize. If the winner has pending debits, those may optionally be closed
   first, and the remaining balance is paid out in cash.

3. Debits (Loans from the Fund)
   - Any member may draw a debit from the cumulative savings balance.
   - A member may take one or more debits, but the total outstanding principal
     per member cannot exceed the group debit limit (see Settings).
   - Interest: for the first 30 days from the date of debit, normal interest applies.
     After each further month the debit remains unpaid, the interest rate doubles
     for that month's period, and each month's charge stacks on the previous ones.
   - If a debit remains unpaid beyond 2 months, it is flagged on the Dashboard.

4. Transparency
   The Dashboard shows the live fund balance, each member's contribution status,
   outstanding debit balance, and accrued interest.

Edit this page any time from the Rules tab.
"""


def _iso(d):
    if d is None:
        return None
    if isinstance(d, (date, datetime)):
        return d.isoformat()
    return str(d)


def _parse_date(s):
    if s is None or isinstance(s, date):
        return s
    return datetime.strptime(s, '%Y-%m-%d').date()


def _default_data():
    return {
        'settings': {
            'contribution': 1350,
            'lottery': 17500,
            'lottery_day': 10,
            'debit_limit': 7500,
            'interest_rate': 1.0,
            'fund_balance': 0,
            'rules_text': DEFAULT_RULES,
        },
        'members': [],
        'contributions': [],
        'debits': [],
        'debit_payments': [],
        'winners': [],
        'activity_log': [],
        'users': [],
        'next_id': {
            'members': 1, 'contributions': 1, 'debits': 1,
            'debit_payments': 1, 'winners': 1, 'activity_log': 1, 'users': 1,
        },
    }


class Store:
    def __init__(self, path=DATA_FILE):
        self.path = path
        self._data = None
        self._load()
        self._migrate()
        self._seed_members_if_empty()
        self._seed_admin_if_empty()

    # ---------------- file I/O ----------------
    def _load(self):
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path):
            with open(self.path, encoding='utf-8') as f:
                self._data = json.load(f)
        else:
            self._data = _default_data()
            self.save()

    def _migrate(self):
        """Backfill keys added after older data files were created."""
        changed = False
        if 'users' not in self._data:
            self._data['users'] = []
            changed = True
        if 'users' not in self._data['next_id']:
            self._data['next_id']['users'] = 1
            changed = True
        for m in self._data['members']:
            if 'debit_limit' not in m:
                m['debit_limit'] = None
                changed = True
        if changed:
            self.save()

    def save(self):
        with _lock:
            tmp = self.path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)

    def _next_id(self, table):
        nid = self._data['next_id'][table]
        self._data['next_id'][table] = nid + 1
        return nid

    def _seed_members_if_empty(self):
        if not self._data['members']:
            for i in range(1, 15):
                self.add_member(f'Member {i}', date.today())
            self.save()

    def _seed_admin_if_empty(self):
        if not self._data['users']:
            self.add_user(DEFAULT_ADMIN_USERNAME, DEFAULT_ADMIN_PASSWORD, role='admin')
            self.save()

    # ---------------- Settings ----------------
    def get_settings(self):
        return SimpleNamespace(**self._data['settings'])

    def update_settings(self, **kwargs):
        self._data['settings'].update(kwargs)

    def adjust_fund_balance(self, delta):
        self._data['settings']['fund_balance'] += delta

    def set_fund_balance(self, value):
        self._data['settings']['fund_balance'] = value

    # ---------------- Members ----------------
    def _raw_member(self, member_id):
        raw = next((m for m in self._data['members'] if m['id'] == member_id), None)
        if raw is None:
            raise NotFound()
        return raw

    def _member_ns(self, raw):
        d = dict(raw)
        d['join_date'] = _parse_date(d.get('join_date'))
        d['debit_limit'] = d.get('debit_limit')  # None = use the group default
        return SimpleNamespace(**d)

    def list_members(self):
        rows = sorted(self._data['members'], key=lambda m: m['name'].lower())
        return [self._member_ns(m) for m in rows]

    def get_member(self, member_id):
        return self._member_ns(self._raw_member(member_id))

    def add_member(self, name, join_date):
        mid = self._next_id('members')
        self._data['members'].append({
            'id': mid, 'name': name, 'join_date': _iso(join_date), 'debit_limit': None,
        })
        return mid

    def rename_member(self, member_id, name):
        self._raw_member(member_id)['name'] = name

    def set_member_debit_limit(self, member_id, limit):
        """limit=None clears the override and falls back to the group default."""
        self._raw_member(member_id)['debit_limit'] = limit

    def delete_member(self, member_id):
        self._raw_member(member_id)  # 404 if missing
        debit_ids = {d['id'] for d in self._data['debits'] if d['member_id'] == member_id}
        self._data['members'] = [m for m in self._data['members'] if m['id'] != member_id]
        self._data['debits'] = [d for d in self._data['debits'] if d['member_id'] != member_id]
        self._data['debit_payments'] = [p for p in self._data['debit_payments'] if p['debit_id'] not in debit_ids]
        self._data['contributions'] = [c for c in self._data['contributions'] if c['member_id'] != member_id]
        self._data['winners'] = [w for w in self._data['winners'] if w['member_id'] != member_id]

    # ---------------- Contributions ----------------
    def get_contribution(self, member_id, month):
        raw = next((c for c in self._data['contributions']
                    if c['member_id'] == member_id and c['month'] == month), None)
        return SimpleNamespace(**raw) if raw else None

    def set_contribution_paid(self, member_id, month, paid):
        raw = next((c for c in self._data['contributions']
                    if c['member_id'] == member_id and c['month'] == month), None)
        if raw is None:
            raw = {'id': self._next_id('contributions'), 'member_id': member_id, 'month': month, 'paid': paid}
            self._data['contributions'].append(raw)
        else:
            raw['paid'] = paid

    def count_paid_contributions(self, month=None):
        rows = self._data['contributions']
        if month is not None:
            rows = [c for c in rows if c['month'] == month]
        return sum(1 for c in rows if c['paid'])

    # ---------------- Debits ----------------
    def _payment_ns(self, raw):
        d = dict(raw)
        d['date'] = _parse_date(d['date'])
        return SimpleNamespace(**d)

    def _debit_ns(self, raw):
        d = dict(raw)
        d['date'] = _parse_date(d['date'])
        d['payments'] = [self._payment_ns(p) for p in self._data['debit_payments'] if p['debit_id'] == raw['id']]
        d['member'] = self.get_member(raw['member_id'])
        return SimpleNamespace(**d)

    def list_debits(self, member_id=None, closed=None):
        rows = self._data['debits']
        if member_id is not None:
            rows = [d for d in rows if d['member_id'] == member_id]
        if closed is not None:
            rows = [d for d in rows if d['closed'] == closed]
        return [self._debit_ns(d) for d in rows]

    def get_debit(self, debit_id):
        raw = next((d for d in self._data['debits'] if d['id'] == debit_id), None)
        if raw is None:
            raise NotFound()
        return self._debit_ns(raw)

    def add_debit(self, member_id, amount, debit_date, closed=False):
        did = self._next_id('debits')
        self._data['debits'].append({
            'id': did, 'member_id': member_id, 'amount': amount,
            'date': _iso(debit_date), 'closed': closed, 'closed_reason': None,
        })
        return did

    def add_debit_payment(self, debit_id, payment_date, amount):
        pid = self._next_id('debit_payments')
        self._data['debit_payments'].append({
            'id': pid, 'debit_id': debit_id, 'date': _iso(payment_date), 'amount': amount,
        })
        return pid

    def close_debit(self, debit_id, reason):
        raw = next(d for d in self._data['debits'] if d['id'] == debit_id)
        raw['closed'] = True
        raw['closed_reason'] = reason

    def update_debit(self, debit_id, amount=None, debit_date=None):
        """Correct a mis-entered debit's amount and/or date. Returns the old
        (amount, date) tuple so the caller can log what changed."""
        raw = next((d for d in self._data['debits'] if d['id'] == debit_id), None)
        if raw is None:
            raise NotFound()
        old_amount, old_date = raw['amount'], raw['date']
        if amount is not None:
            raw['amount'] = amount
        if debit_date is not None:
            raw['date'] = _iso(debit_date)
        return old_amount, old_date

    # ---------------- Winners ----------------
    def _winner_ns(self, raw):
        d = dict(raw)
        d['date'] = _parse_date(d['date'])
        d['member'] = self.get_member(raw['member_id'])
        return SimpleNamespace(**d)

    def list_winners(self, limit=None):
        rows = sorted(self._data['winners'], key=lambda w: w['date'], reverse=True)
        if limit:
            rows = rows[:limit]
        return [self._winner_ns(w) for w in rows]

    def add_winner(self, member_id, month, amount, debit_closed, cash_given, winner_date):
        wid = self._next_id('winners')
        self._data['winners'].append({
            'id': wid, 'member_id': member_id, 'month': month, 'amount': amount,
            'debit_closed': debit_closed, 'cash_given': cash_given, 'date': _iso(winner_date),
        })
        return wid

    # ---------------- Activity Log ----------------
    def log_activity(self, description):
        aid = self._next_id('activity_log')
        self._data['activity_log'].append({
            'id': aid, 'timestamp': datetime.utcnow().isoformat(), 'description': description,
        })

    def list_activity(self, q=None, limit=500):
        rows = sorted(self._data['activity_log'], key=lambda a: a['timestamp'], reverse=True)
        if q:
            ql = q.lower()
            rows = [a for a in rows if ql in a['description'].lower()]
        rows = rows[:limit]
        out = []
        for a in rows:
            d = dict(a)
            d['timestamp'] = datetime.fromisoformat(d['timestamp'])
            out.append(SimpleNamespace(**d))
        return out

    # ---------------- Users (auth) ----------------
    def _user_ns(self, raw):
        d = dict(raw)
        d.pop('password_hash', None)
        return SimpleNamespace(**d)

    def list_users(self):
        rows = sorted(self._data['users'], key=lambda u: u['username'].lower())
        return [self._user_ns(u) for u in rows]

    def _raw_user_by_username(self, username):
        uname = (username or '').strip().lower()
        return next((u for u in self._data['users'] if u['username'].lower() == uname), None)

    def get_user(self, user_id):
        raw = next((u for u in self._data['users'] if u['id'] == user_id), None)
        if raw is None:
            raise NotFound()
        return self._user_ns(raw)

    def username_taken(self, username):
        return self._raw_user_by_username(username) is not None

    def add_user(self, username, password, role='viewer', member_id=None):
        uid = self._next_id('users')
        self._data['users'].append({
            'id': uid,
            'username': username.strip(),
            'password_hash': generate_password_hash(password),
            'role': role,
            'member_id': member_id,
        })
        return uid

    def verify_login(self, username, password):
        """Returns the user namespace on success, else None."""
        raw = self._raw_user_by_username(username)
        if raw is None:
            return None
        from werkzeug.security import check_password_hash
        if not check_password_hash(raw['password_hash'], password):
            return None
        return self._user_ns(raw)

    def set_password(self, user_id, new_password):
        raw = next((u for u in self._data['users'] if u['id'] == user_id), None)
        if raw is None:
            raise NotFound()
        raw['password_hash'] = generate_password_hash(new_password)

    def delete_user(self, user_id):
        self._data['users'] = [u for u in self._data['users'] if u['id'] != user_id]

    def count_admins(self):
        return sum(1 for u in self._data['users'] if u['role'] == 'admin')
