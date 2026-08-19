from datetime import date, datetime


def _as_date(d):
    if isinstance(d, str):
        return datetime.strptime(d, '%Y-%m-%d').date()
    return d


def days_between(a, b):
    return (_as_date(b) - _as_date(a)).days


def months_elapsed(debit_date, as_of=None):
    """Month 1 starts immediately; a new month begins every 30 days."""
    as_of = as_of or date.today()
    d = days_between(debit_date, as_of)
    return max(1, d // 30 + 1)


def interest_accrued(principal, debit_date, rate_percent, as_of=None):
    """Interest doubles every month the debit remains unpaid, and each
    month's charge stacks on top of the previous ones.
    Month 1 = base rate, month 2 = 2x, month 3 = 4x, etc. — e.g. a ₹1,000
    debit at 1% owes ₹10 + ₹20 + ₹40 = ₹70 total interest by month 3."""
    rate = rate_percent / 100
    n = months_elapsed(debit_date, as_of)
    # sum of rate * 2^(k-1) for k = 1..n == rate * (2^n - 1)
    total = principal * rate * (2 ** n - 1)
    return round(total, 2)


def paid_total(payments):
    return sum(p.amount for p in payments)


def debit_outstanding(debit, rate_percent):
    interest = interest_accrued(debit.amount, debit.date, rate_percent)
    owed = debit.amount + interest
    paid = paid_total(debit.payments)
    return max(0.0, round(owed - paid, 2))


def debit_interest_outstanding(debit, rate_percent):
    """Unpaid interest only (payments are assumed to settle interest first)."""
    interest = interest_accrued(debit.amount, debit.date, rate_percent)
    paid = paid_total(debit.payments)
    interest_paid = min(paid, interest)
    return max(0.0, round(interest - interest_paid, 2))


def is_overdue(debit, rate_percent):
    return (
        months_elapsed(debit.date) > 2
        and not debit.closed
        and debit_outstanding(debit, rate_percent) > 0
    )


def member_open_debits(debits):
    return [d for d in debits if not d.closed]


def member_open_principal(debits, rate_percent):
    """Principal still owed across a member's debits (payments assumed to settle interest first)."""
    total = 0.0
    for d in member_open_debits(debits):
        interest = interest_accrued(d.amount, d.date, rate_percent)
        paid = paid_total(d.payments)
        principal_paid = max(0.0, paid - interest)
        total += max(0.0, d.amount - principal_paid)
    return total


def member_total_outstanding(debits, rate_percent):
    return sum(debit_outstanding(d, rate_percent) for d in member_open_debits(debits))
