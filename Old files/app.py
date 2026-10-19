import os
from functools import wraps
from datetime import date, datetime

from flask import Flask, render_template, request, redirect, url_for, flash, session, abort

from store import Store
from logic import (
    months_elapsed, interest_accrued, debit_outstanding, debit_interest_outstanding, is_overdue,
    member_open_debits, member_open_principal, member_total_outstanding, paid_total,
)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-secret-change-me')

store = Store()


# ---------------- Auth helpers ----------------
def current_user():
    uid = session.get('user_id')
    if uid is None:
        return None
    try:
        return store.get_user(uid)
    except Exception:
        return None


def log(description):
    """Log an activity entry, automatically attributing it to whoever is signed in."""
    u = current_user()
    store.log_activity(description, actor=u.username if u else 'system')


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for('login', next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if u is None:
            return redirect(url_for('login', next=request.path))
        if u.role != 'admin':
            abort(403)
        return view(*args, **kwargs)
    return wrapped


@app.context_processor
def inject_auth():
    u = current_user()
    return dict(current_user=u, is_admin=bool(u and u.role == 'admin'))


@app.errorhandler(403)
def forbidden(e):
    return render_template('error.html', code=403,
                            message="You have view-only access — this action is restricted to admins."), 403


# ---------------- Helpers ----------------
def month_key(d=None):
    d = d or date.today()
    return d.strftime('%Y-%m')


def shift_month(month_str, delta):
    y, m = map(int, month_str.split('-'))
    m += delta
    while m < 1:
        m += 12
        y -= 1
    while m > 12:
        m -= 12
        y += 1
    return f'{y:04d}-{m:02d}'


def month_label(month_str):
    y, m = map(int, month_str.split('-'))
    return date(y, m, 1).strftime('%B %Y')


def parse_date_input(s, fallback=None):
    if not s:
        return fallback or date.today()
    return datetime.strptime(s, '%Y-%m-%d').date()


def effective_debit_limit(member, settings):
    """A member's own debit_limit overrides the group default when set."""
    return member.debit_limit if member.debit_limit is not None else settings.debit_limit


# ---------------- Auth routes ----------------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user() is not None:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        user = store.verify_login(username, password)
        if user is None:
            flash('Incorrect username or password.')
            return redirect(url_for('login'))
        session.clear()
        session['user_id'] = user.id
        session.permanent = True
        next_url = request.args.get('next') or url_for('dashboard')
        return redirect(next_url)
    return render_template('login.html')


@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    u = current_user()
    if request.method == 'POST':
        current_pw = request.form.get('current_password', '')
        new_pw = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')
        if store.verify_login(u.username, current_pw) is None:
            flash('Current password is incorrect.')
            return redirect(url_for('account'))
        if len(new_pw) < 6:
            flash('New password must be at least 6 characters.')
            return redirect(url_for('account'))
        if new_pw != confirm_pw:
            flash('New password and confirmation do not match.')
            return redirect(url_for('account'))
        store.set_password(u.id, new_pw)
        log(f'{u.username} changed their own password.')
        store.save()
        flash('Password updated.')
        return redirect(url_for('account'))
    return render_template('account.html', settings=store.get_settings(), active='account')


@app.route('/users')
@admin_required
def users_view():
    all_users = store.list_users()
    all_members = store.list_members()
    return render_template(
        'users.html', users=all_users, members=all_members,
        settings=store.get_settings(), active='users',
    )


@app.route('/users/add', methods=['POST'])
@admin_required
def add_user():
    username = request.form.get('username', '').strip()
    password = request.form.get('password', '')
    role = request.form.get('role', 'viewer')
    member_id = request.form.get('member_id') or None
    if role not in ('admin', 'viewer'):
        role = 'viewer'
    if not username or len(password) < 6:
        flash('Username is required and password must be at least 6 characters.')
        return redirect(url_for('users_view'))
    if store.username_taken(username):
        flash(f'Username "{username}" is already in use.')
        return redirect(url_for('users_view'))
    store.add_user(username, password, role=role, member_id=int(member_id) if member_id else None)
    log(f'Created {role} login for "{username}".')
    store.save()
    return redirect(url_for('users_view'))


@app.route('/users/<int:user_id>/delete', methods=['POST'])
@admin_required
def delete_user(user_id):
    target = store.get_user(user_id)
    if target.role == 'admin' and store.count_admins() <= 1:
        flash('Cannot remove the last remaining admin account.')
        return redirect(url_for('users_view'))
    if target.id == current_user().id:
        flash('You cannot remove your own account while logged in.')
        return redirect(url_for('users_view'))
    store.delete_user(user_id)
    log(f'Removed login "{target.username}".')
    store.save()
    return redirect(url_for('users_view'))


@app.route('/users/<int:user_id>/reset-password', methods=['POST'])
@admin_required
def reset_user_password(user_id):
    target = store.get_user(user_id)
    new_password = request.form.get('password', '')
    if len(new_password) < 6:
        flash('New password must be at least 6 characters.')
        return redirect(url_for('users_view'))
    store.set_password(user_id, new_password)
    log(f'Reset password for login "{target.username}".')
    store.save()
    return redirect(url_for('users_view'))


# ---------------- App routes ----------------
@app.route('/')
@login_required
def index():
    return redirect(url_for('dashboard'))


@app.route('/dashboard')
@login_required
def dashboard():
    s = store.get_settings()
    members = store.list_members()
    this_month = month_key()
    rate = s.interest_rate

    member_rows = []
    overdue_count = 0
    for m in members:
        contrib = store.get_contribution(m.id, this_month)
        paid = bool(contrib and contrib.paid)
        debits = store.list_debits(member_id=m.id)
        outstanding = member_total_outstanding(debits, rate)
        overdue = any(is_overdue(d, rate) for d in member_open_debits(debits))
        if overdue:
            overdue_count += 1
        member_rows.append(dict(member=m, paid=paid, outstanding=outstanding, overdue=overdue))

    paid_contribs = store.count_paid_contributions()
    total_contrib_amount = paid_contribs * s.contribution
    open_debits = store.list_debits(closed=False)
    total_outstanding = sum(debit_outstanding(d, rate) for d in open_debits)
    total_interest_outstanding = sum(debit_interest_outstanding(d, rate) for d in open_debits)
    paid_this_month = store.count_paid_contributions(month=this_month)
    recent_winners = store.list_winners(limit=5)

    return render_template(
        'dashboard.html', settings=s, member_rows=member_rows,
        total_contrib_amount=total_contrib_amount, total_outstanding=total_outstanding,
        total_interest_outstanding=total_interest_outstanding,
        overdue_count=overdue_count, this_month=this_month,
        paid_this_month=paid_this_month, total_members=len(members),
        recent_winners=recent_winners, active='dashboard',
    )


@app.route('/members')
@login_required
def members():
    s = store.get_settings()
    all_members = store.list_members()
    rows = [
        dict(
            member=m,
            outstanding=member_total_outstanding(store.list_debits(member_id=m.id), s.interest_rate),
            limit=effective_debit_limit(m, s),
        )
        for m in all_members
    ]
    return render_template('members.html', rows=rows, settings=s, today=date.today().isoformat(), active='members')


@app.route('/members/add', methods=['POST'])
@admin_required
def add_member():
    name = request.form.get('name', '').strip()
    join_date = parse_date_input(request.form.get('join_date'))
    if name:
        store.add_member(name, join_date)
        log(f'Added member "{name}" (joined {join_date}).')
        store.save()
    return redirect(url_for('members'))


@app.route('/members/<int:member_id>/rename', methods=['POST'])
@admin_required
def rename_member(member_id):
    m = store.get_member(member_id)
    name = request.form.get('name', '').strip()
    if name:
        old_name = m.name
        store.rename_member(member_id, name)
        log(f'Renamed member "{old_name}" to "{name}".')
        store.save()
    return redirect(url_for('members'))


@app.route('/members/<int:member_id>/delete', methods=['POST'])
@admin_required
def delete_member(member_id):
    m = store.get_member(member_id)
    store.delete_member(member_id)
    log(f'Removed member "{m.name}".')
    store.save()
    return redirect(url_for('members'))


@app.route('/members/<int:member_id>/set-limit', methods=['POST'])
@admin_required
def set_member_limit(member_id):
    m = store.get_member(member_id)
    raw = (request.form.get('debit_limit') or '').strip()
    if raw == '':
        store.set_member_debit_limit(member_id, None)
        log(f'Cleared custom debit limit for {m.name} (now uses the group default).')
    else:
        try:
            limit = float(raw)
        except ValueError:
            flash('Enter a valid number for the debit limit.')
            return redirect(url_for('members'))
        if limit < 0:
            flash('Debit limit cannot be negative.')
            return redirect(url_for('members'))
        store.set_member_debit_limit(member_id, limit)
        log(f'Set a custom debit limit of ₹{limit:,.2f} for {m.name}.')
    store.save()
    return redirect(url_for('members'))


@app.route('/contributions')
@login_required
def contributions():
    s = store.get_settings()
    month = request.args.get('month') or month_key()
    all_members = store.list_members()
    rows = []
    collected = 0.0
    for m in all_members:
        c = store.get_contribution(m.id, month)
        paid = bool(c and c.paid)
        if paid:
            collected += s.contribution
        rows.append(dict(member=m, paid=paid))

    return render_template(
        'contributions.html', rows=rows, month=month,
        prev_month=shift_month(month, -1), next_month=shift_month(month, 1),
        month_label=month_label(month), collected=collected, settings=s, active='contributions',
    )


@app.route('/contributions/toggle', methods=['POST'])
@admin_required
def toggle_contribution():
    member_id = int(request.form['member_id'])
    month = request.form['month']
    s = store.get_settings()
    m = store.get_member(member_id)
    c = store.get_contribution(member_id, month)
    was_paid = bool(c and c.paid)
    store.set_contribution_paid(member_id, month, not was_paid)
    store.adjust_fund_balance(-s.contribution if was_paid else s.contribution)
    if was_paid:
        log(f'Marked {m.name} unpaid for {month} contribution (₹{s.contribution:,.2f} reversed).')
    else:
        log(f'Collected ₹{s.contribution:,.2f} contribution from {m.name} for {month}.')
    store.save()
    return redirect(url_for('contributions', month=month))


@app.route('/debits')
@login_required
def debits():
    s = store.get_settings()
    all_members = store.list_members()
    q = (request.args.get('q') or '').strip().lower()

    all_debits = store.list_debits()
    # Sort: most recent first, then push fully-closed debits below open ones (stable sort).
    all_debits = sorted(all_debits, key=lambda d: d.date, reverse=True)
    all_debits = sorted(all_debits, key=lambda d: d.closed)

    debit_rows = []
    for d in all_debits:
        if q:
            haystack = f'{d.member.name} {d.amount} {d.date}'.lower()
            if q not in haystack:
                continue
        debit_rows.append(dict(
            debit=d, member=d.member, months=months_elapsed(d.date),
            interest=interest_accrued(d.amount, d.date, s.interest_rate),
            outstanding=debit_outstanding(d, s.interest_rate),
            paid=paid_total(d.payments),
            overdue=is_overdue(d, s.interest_rate),
        ))
    member_options = []
    for m in all_members:
        limit = effective_debit_limit(m, s)
        remaining = limit - member_open_principal(store.list_debits(member_id=m.id), s.interest_rate)
        member_options.append(dict(member=m, remaining=max(0.0, remaining)))

    return render_template(
        'debits.html', debit_rows=debit_rows, member_options=member_options,
        settings=s, today=date.today().isoformat(), active='debits', q=request.args.get('q', ''),
    )


@app.route('/debits/add', methods=['POST'])
@admin_required
def add_debit():
    s = store.get_settings()
    member_id = int(request.form['member_id'])
    amount = float(request.form['amount'])
    d = parse_date_input(request.form.get('date'))
    member = store.get_member(member_id)

    limit = effective_debit_limit(member, s)
    remaining = limit - member_open_principal(store.list_debits(member_id=member_id), s.interest_rate)
    if amount <= 0:
        flash('Enter a valid debit amount.')
        return redirect(url_for('debits'))
    if amount > remaining + 0.001:
        flash(f'This exceeds the remaining debit limit of ₹{remaining:,.2f} for {member.name}.')
        return redirect(url_for('debits'))

    store.add_debit(member_id, amount, d, closed=False)
    store.adjust_fund_balance(-amount)
    log(f'Issued debit of ₹{amount:,.2f} to {member.name} on {d}.')
    store.save()
    return redirect(url_for('debits'))


@app.route('/debits/<int:debit_id>/edit', methods=['GET', 'POST'])
@admin_required
def edit_debit(debit_id):
    s = store.get_settings()
    d = store.get_debit(debit_id)
    if request.method == 'POST':
        try:
            amount = float(request.form['amount'])
        except (KeyError, ValueError):
            flash('Enter a valid amount.')
            return redirect(url_for('edit_debit', debit_id=debit_id))
        if amount <= 0:
            flash('Amount must be greater than zero.')
            return redirect(url_for('edit_debit', debit_id=debit_id))
        new_date = parse_date_input(request.form.get('date'), fallback=d.date)

        old_amount, old_date = store.update_debit(debit_id, amount=amount, debit_date=new_date)
        if old_amount != amount:
            # Fund balance was reduced by the original amount when issued — true up the difference.
            store.adjust_fund_balance(old_amount - amount)
        log(
            f'Corrected debit for {d.member.name}: amount ₹{old_amount:,.2f} → ₹{amount:,.2f}, '
            f'date {old_date} → {new_date}.'
        )
        store.save()
        return redirect(url_for('debits'))
    return render_template('edit_debit.html', debit=d, settings=s, active='debits')


@app.route('/debits/<int:debit_id>/partial-pay', methods=['POST'])
@admin_required
def partial_pay(debit_id):
    s = store.get_settings()
    d = store.get_debit(debit_id)
    try:
        payment_amount = float(request.form['amount'])
    except (KeyError, ValueError):
        payment_amount = 0
    if payment_amount <= 0:
        flash('Enter a valid payment amount.')
        return redirect(url_for('debits'))

    today = date.today()
    interest_due = interest_accrued(d.amount, d.date, s.interest_rate, as_of=today)
    outstanding_total = debit_outstanding(d, s.interest_rate)

    if payment_amount >= outstanding_total:
        # Covers everything — same as a full repayment, no split needed.
        applied = outstanding_total
        store.add_debit_payment(d.id, today, applied)
        store.adjust_fund_balance(applied)
        store.close_debit(d.id, 'fully_repaid')
        if payment_amount > outstanding_total + 0.01:
            flash(f'Payment exceeded the outstanding balance of ₹{outstanding_total:,.2f} — '
                  f'only that amount was applied and the debit was closed as fully repaid.')
        log(f'{d.member.name} paid ₹{applied:,.2f}, fully clearing debit dated {d.date}.')
        store.save()
        return redirect(url_for('debits'))

    if payment_amount < interest_due - 0.01:
        flash(f'Payment must at least cover the total interest accrued so far (₹{interest_due:,.2f}).')
        return redirect(url_for('debits'))

    principal_portion = round(payment_amount - interest_due, 2)
    remaining_principal = round(d.amount - principal_portion, 2)

    store.add_debit_payment(d.id, today, payment_amount)
    store.adjust_fund_balance(payment_amount)
    store.close_debit(d.id, 'partial_split')
    store.add_debit(d.member_id, remaining_principal, today, closed=False)

    log(
        f'{d.member.name} partially paid ₹{payment_amount:,.2f} (₹{interest_due:,.2f} interest + '
        f'₹{principal_portion:,.2f} principal) on debit dated {d.date}. '
        f'Remaining ₹{remaining_principal:,.2f} rolled into a new debit dated {today}.'
    )
    store.save()
    return redirect(url_for('debits'))


@app.route('/debits/<int:debit_id>/mark-repaid', methods=['POST'])
@admin_required
def mark_fully_repaid(debit_id):
    s = store.get_settings()
    d = store.get_debit(debit_id)
    outstanding = debit_outstanding(d, s.interest_rate)
    if outstanding > 0:
        store.add_debit_payment(d.id, date.today(), outstanding)
        store.adjust_fund_balance(outstanding)
    store.close_debit(d.id, 'fully_repaid')
    log(f'Marked debit for {d.member.name} (dated {d.date}) fully repaid — ₹{outstanding:,.2f} added to fund.')
    store.save()
    return redirect(url_for('debits'))


@app.route('/winners')
@login_required
def winners():
    s = store.get_settings()
    all_members = store.list_members()
    all_winners = store.list_winners()
    return render_template(
        'winners.html', winners=all_winners, members=all_members, settings=s,
        today=date.today().isoformat(), this_month=month_key(), active='winners',
    )


@app.route('/winners/add', methods=['POST'])
@admin_required
def add_winner():
    s = store.get_settings()
    member_id = int(request.form['member_id'])
    month = request.form.get('month') or month_key()
    amount = float(request.form.get('amount') or s.lottery)
    d = parse_date_input(request.form.get('date'))
    close_debit_flag = request.form.get('close_debit') == 'on'
    member = store.get_member(member_id)

    debit_closed = 0.0
    if close_debit_flag:
        remaining_prize = amount
        open_debits = sorted(member_open_debits(store.list_debits(member_id=member_id)), key=lambda x: x.date)
        for deb in open_debits:
            if remaining_prize <= 0:
                break
            owed = debit_outstanding(deb, s.interest_rate)
            pay = min(owed, remaining_prize)
            if pay > 0:
                store.add_debit_payment(deb.id, d, pay)
                debit_closed += pay
                remaining_prize -= pay
                updated = store.get_debit(deb.id)
                if debit_outstanding(updated, s.interest_rate) <= 0:
                    store.close_debit(deb.id, 'fully_repaid')

    cash_given = max(0.0, amount - debit_closed)
    store.add_winner(member_id, month, amount, debit_closed, cash_given, d)
    store.adjust_fund_balance(-amount)
    log(f'{member.name} won the {month} lottery — ₹{amount:,.2f} prize '
                        f'(₹{debit_closed:,.2f} closed debits, ₹{cash_given:,.2f} cash given).')
    store.save()
    return redirect(url_for('winners'))


@app.route('/settings', methods=['GET', 'POST'])
@admin_required
def settings_view():
    s = store.get_settings()
    if request.method == 'POST':
        store.update_settings(
            contribution=float(request.form.get('contribution') or s.contribution),
            lottery=float(request.form.get('lottery') or s.lottery),
            lottery_day=int(request.form.get('lottery_day') or s.lottery_day),
            debit_limit=float(request.form.get('debit_limit') or s.debit_limit),
            interest_rate=float(request.form.get('interest_rate') or s.interest_rate),
        )
        log('Updated fund settings (contribution, lottery, debit limit, or interest rate).')
        store.save()
        return redirect(url_for('settings_view'))
    return render_template('settings.html', settings=s, active='settings')


@app.route('/settings/fund-balance', methods=['POST'])
@admin_required
def update_fund_balance():
    s = store.get_settings()
    new_balance = float(request.form.get('fund_balance') or 0)
    log(f'Manually adjusted fund balance from ₹{s.fund_balance:,.2f} to ₹{new_balance:,.2f}.')
    store.set_fund_balance(new_balance)
    store.save()
    return redirect(url_for('settings_view'))


@app.route('/rules', methods=['GET', 'POST'])
@login_required
def rules_view():
    s = store.get_settings()
    if request.method == 'POST':
        if not (current_user() and current_user().role == 'admin'):
            abort(403)
        store.update_settings(rules_text=request.form.get('rules_text', s.rules_text))
        log('Edited the group rules.')
        store.save()
        return redirect(url_for('rules_view'))
    return render_template('rules.html', settings=s, active='rules')


@app.route('/activity')
@login_required
def activity():
    q = (request.args.get('q') or '').strip()
    logs = store.list_activity(q=q, limit=500)
    return render_template('activity.html', logs=logs, q=q, active='activity', settings=store.get_settings())


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)
