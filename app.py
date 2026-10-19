import os
from functools import wraps
from datetime import datetime, date
from calendar import month_name

from flask import (
    Flask, render_template, request, redirect, url_for, session, flash, g
)

import db as dbm

app = Flask(__name__)
app.secret_key = os.environ.get("CHITFUND_SECRET_KEY", "change-this-secret-key")

ADMIN_PASSWORD = os.environ.get("CHITFUND_ADMIN_PASSWORD", "admin123")


dbm.init_db()

# ---------------------------------------------------------------- helpers --

def get_db():
    if "db" not in g:
        g.db = dbm.get_db()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def money(v):
    try:
        return "₹{:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return v


app.jinja_env.filters["money"] = money


def loan_interest_tiers(principal, rate):
    i1 = round(principal * rate, 2)
    i2 = round(i1 * 2, 2)
    i3 = round(i2 * 2, 2)
    return [i1, i2, i3]


def next_month_label(label, offset):
    y, m = [int(x) for x in label.split("-")]
    total = (y * 12 + (m - 1)) + offset
    ny, nm = divmod(total, 12)
    return "{:04d}-{:02d}".format(ny, nm + 1)


# ------------------------------------------------------------------ auth --

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        pwd = request.form.get("password", "")
        if pwd == ADMIN_PASSWORD:
            session["logged_in"] = True
            nxt = request.args.get("next") or url_for("dashboard")
            return redirect(nxt)
        flash("Incorrect password.", "danger")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------- dashboard --

@app.route("/")
@login_required
def dashboard():
    conn = get_db()
    members = conn.execute(
        "SELECT * FROM members WHERE active=1 ORDER BY name"
    ).fetchall()
    total_members = conn.execute("SELECT COUNT(*) c FROM members WHERE active=1").fetchone()["c"]

    current_month = conn.execute(
        "SELECT * FROM months ORDER BY draw_date DESC LIMIT 1"
    ).fetchone()

    paid_count = 0
    if current_month:
        paid_count = conn.execute(
            "SELECT COUNT(*) c FROM contributions WHERE month_id=? AND paid=1",
            (current_month["id"],),
        ).fetchone()["c"]

    balance = dbm.savings_balance(conn)

    active_loans = conn.execute(
        "SELECT l.*, m.name as member_name FROM loans l JOIN members m ON m.id=l.member_id "
        "WHERE l.status='active' ORDER BY l.taken_date DESC"
    ).fetchall()
    outstanding_principal = sum(l["principal"] for l in active_loans)

    recent_activity = conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT 12"
    ).fetchall()

    winners = conn.execute(
        "SELECT mo.label, m.name FROM months mo JOIN members m ON m.id = mo.winner_member_id "
        "WHERE mo.winner_member_id IS NOT NULL ORDER BY mo.draw_date DESC LIMIT 6"
    ).fetchall()

    return render_template(
        "dashboard.html",
        members=members,
        total_members=total_members,
        current_month=current_month,
        paid_count=paid_count,
        balance=balance,
        active_loans=active_loans,
        outstanding_principal=outstanding_principal,
        recent_activity=recent_activity,
        winners=winners,
    )


# ----------------------------------------------------------------members --

@app.route("/members")
@login_required
def members():
    conn = get_db()
    all_members = conn.execute("SELECT * FROM members ORDER BY active DESC, name").fetchall()
    return render_template("members.html", members=all_members)


@app.route("/members/add", methods=["POST"])
@login_required
def add_member():
    conn = get_db()
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    join_date = request.form.get("join_date") or date.today().isoformat()
    if not name:
        flash("Name is required.", "danger")
        return redirect(url_for("members"))
    conn.execute(
        "INSERT INTO members (name, phone, join_date, active) VALUES (?, ?, ?, 1)",
        (name, phone, join_date),
    )
    dbm.log_activity(conn, "member_added", f"Added member {name}")
    conn.commit()
    flash(f"{name} added to the chit fund.", "success")
    return redirect(url_for("members"))


@app.route("/members/<int:member_id>/toggle", methods=["POST"])
@login_required
def toggle_member(member_id):
    conn = get_db()
    m = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
    if not m:
        flash("Member not found.", "danger")
        return redirect(url_for("members"))
    new_active = 0 if m["active"] else 1
    left_date = date.today().isoformat() if new_active == 0 else None
    conn.execute(
        "UPDATE members SET active=?, left_date=? WHERE id=?",
        (new_active, left_date, member_id),
    )
    dbm.log_activity(
        conn,
        "member_deactivated" if new_active == 0 else "member_reactivated",
        f"{m['name']}",
    )
    conn.commit()
    return redirect(url_for("members"))


# ----------------------------------------------------------------- months --

@app.route("/months")
@login_required
def months():
    conn = get_db()
    all_months = conn.execute(
        "SELECT mo.*, m.name as winner_name FROM months mo "
        "LEFT JOIN members m ON m.id = mo.winner_member_id "
        "ORDER BY mo.draw_date DESC"
    ).fetchall()
    return render_template("months.html", months=all_months)


@app.route("/months/new", methods=["GET", "POST"])
@login_required
def new_month():
    conn = get_db()
    if request.method == "POST":
        label = request.form.get("label", "").strip()  # YYYY-MM
        draw_date = request.form.get("draw_date", "").strip()
        if not label or not draw_date:
            flash("Month label and draw date are required.", "danger")
            return redirect(url_for("new_month"))

        existing = conn.execute("SELECT id FROM months WHERE label=?", (label,)).fetchone()
        if existing:
            flash("That month already exists.", "warning")
            return redirect(url_for("months"))

        chit_amount = float(dbm.get_setting(conn, "chit_total_amount", 17500))
        cur = conn.execute(
            "INSERT INTO months (label, draw_date, status, chit_amount, created_at) "
            "VALUES (?, ?, 'open', ?, ?)",
            (label, draw_date, chit_amount, datetime.now().isoformat(timespec="seconds")),
        )
        month_id = cur.lastrowid

        chit_portion = float(dbm.get_setting(conn, "chit_portion", 1250))
        savings_portion = float(dbm.get_setting(conn, "savings_portion", 100))
        amount_expected = float(dbm.get_setting(conn, "monthly_contribution", 1350))

        active_members = conn.execute("SELECT id FROM members WHERE active=1").fetchall()
        for mem in active_members:
            conn.execute(
                "INSERT INTO contributions (month_id, member_id, amount_expected, "
                "chit_portion, savings_portion, paid) VALUES (?, ?, ?, ?, ?, 0)",
                (month_id, mem["id"], amount_expected, chit_portion, savings_portion),
            )

        dbm.log_activity(conn, "month_opened", f"Kuri month {label} opened, draw date {draw_date}")
        conn.commit()
        flash(f"Month {label} created with {len(active_members)} members.", "success")
        return redirect(url_for("month_detail", month_id=month_id))

    suggested_label = ""
    last = conn.execute("SELECT label FROM months ORDER BY draw_date DESC LIMIT 1").fetchone()
    if last:
        suggested_label = next_month_label(last["label"], 1)
    return render_template("month_form.html", suggested_label=suggested_label)


@app.route("/months/<int:month_id>")
@login_required
def month_detail(month_id):
    conn = get_db()
    mo = conn.execute("SELECT * FROM months WHERE id=?", (month_id,)).fetchone()
    if not mo:
        flash("Month not found.", "danger")
        return redirect(url_for("months"))
    contribs = conn.execute(
        "SELECT c.*, m.name as member_name FROM contributions c "
        "JOIN members m ON m.id = c.member_id WHERE c.month_id=? ORDER BY m.name",
        (month_id,),
    ).fetchall()
    eligible_members = conn.execute(
        "SELECT * FROM members WHERE active=1 ORDER BY name"
    ).fetchall()
    winner = None
    if mo["winner_member_id"]:
        winner = conn.execute("SELECT * FROM members WHERE id=?", (mo["winner_member_id"],)).fetchone()

    paid_total = sum(c["amount_expected"] for c in contribs if c["paid"])
    savings_total = sum(c["savings_portion"] for c in contribs if c["paid"])

    return render_template(
        "month_detail.html",
        mo=mo,
        contribs=contribs,
        eligible_members=eligible_members,
        winner=winner,
        paid_total=paid_total,
        savings_total=savings_total,
    )


@app.route("/months/<int:month_id>/contribution/<int:contrib_id>/toggle", methods=["POST"])
@login_required
def toggle_contribution(month_id, contrib_id):
    conn = get_db()
    c = conn.execute("SELECT * FROM contributions WHERE id=?", (contrib_id,)).fetchone()
    if not c:
        flash("Contribution not found.", "danger")
        return redirect(url_for("month_detail", month_id=month_id))

    mo = conn.execute("SELECT * FROM months WHERE id=?", (month_id,)).fetchone()
    member = conn.execute("SELECT * FROM members WHERE id=?", (c["member_id"],)).fetchone()

    if c["paid"]:
        # un-mark as paid, reverse the savings ledger entry
        conn.execute("UPDATE contributions SET paid=0, paid_date=NULL, payment_mode=NULL WHERE id=?", (contrib_id,))
        conn.execute(
            "DELETE FROM savings_ledger WHERE type='monthly_savings' AND month_id=? AND related_member_id=?",
            (month_id, c["member_id"]),
        )
        dbm.log_activity(conn, "contribution_unpaid", f"{member['name']} - {mo['label']} marked unpaid")
    else:
        payment_mode = request.form.get("payment_mode", "online")
        conn.execute(
            "UPDATE contributions SET paid=1, paid_date=?, payment_mode=? WHERE id=?",
            (date.today().isoformat(), payment_mode, contrib_id),
        )
        conn.execute(
            "INSERT INTO savings_ledger (date, type, description, amount, month_id, related_member_id) "
            "VALUES (?, 'monthly_savings', ?, ?, ?, ?)",
            (
                date.today().isoformat(),
                f"Savings portion - {member['name']} - {mo['label']}",
                c["savings_portion"],
                month_id,
                c["member_id"],
            ),
        )
        dbm.log_activity(
            conn, "contribution_paid",
            f"{member['name']} paid ₹{c['amount_expected']} for {mo['label']} ({payment_mode})",
        )
    conn.commit()
    return redirect(url_for("month_detail", month_id=month_id))


@app.route("/months/<int:month_id>/draw", methods=["POST"])
@login_required
def record_draw(month_id):
    conn = get_db()
    winner_id = request.form.get("winner_member_id")
    if not winner_id:
        flash("Select a winner.", "danger")
        return redirect(url_for("month_detail", month_id=month_id))
    mo = conn.execute("SELECT * FROM months WHERE id=?", (month_id,)).fetchone()
    winner = conn.execute("SELECT * FROM members WHERE id=?", (winner_id,)).fetchone()
    conn.execute(
        "UPDATE months SET winner_member_id=?, status='drawn' WHERE id=?",
        (winner_id, month_id),
    )
    dbm.log_activity(conn, "draw_completed", f"{winner['name']} won the {mo['label']} kuri draw (₹{mo['chit_amount']})")
    conn.commit()
    flash(f"{winner['name']} recorded as the winner for {mo['label']}.", "success")
    return redirect(url_for("month_detail", month_id=month_id))


@app.route("/months/<int:month_id>/payout", methods=["POST"])
@login_required
def record_payout(month_id):
    conn = get_db()
    mo = conn.execute("SELECT * FROM months WHERE id=?", (month_id,)).fetchone()
    winner = conn.execute("SELECT * FROM members WHERE id=?", (mo["winner_member_id"],)).fetchone()
    conn.execute(
        "UPDATE months SET payout_done=1, payout_date=?, status='closed' WHERE id=?",
        (date.today().isoformat(), month_id),
    )
    dbm.log_activity(conn, "payout_transferred", f"₹{mo['chit_amount']} transferred to {winner['name']} for {mo['label']}")
    conn.commit()
    flash("Payout recorded.", "success")
    return redirect(url_for("month_detail", month_id=month_id))


# ------------------------------------------------------------------ loans --

@app.route("/loans")
@login_required
def loans():
    conn = get_db()
    all_loans = conn.execute(
        "SELECT l.*, m.name as member_name FROM loans l "
        "JOIN members m ON m.id = l.member_id ORDER BY l.status='active' DESC, l.taken_date DESC"
    ).fetchall()
    return render_template("loans.html", loans=all_loans)


@app.route("/loans/new", methods=["GET", "POST"])
@login_required
def new_loan():
    conn = get_db()
    max_loan = float(dbm.get_setting(conn, "max_loan_amount", 7500))
    rate = float(dbm.get_setting(conn, "loan_base_interest_rate", 0.01))

    if request.method == "POST":
        member_id = request.form.get("member_id")
        principal = float(request.form.get("principal", 0))
        purpose = request.form.get("purpose", "").strip()
        month_id = request.form.get("month_id")

        member = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
        mo = conn.execute("SELECT * FROM months WHERE id=?", (month_id,)).fetchone()

        if principal <= 0 or principal > max_loan:
            flash(f"Loan amount must be between ₹1 and ₹{max_loan:,.0f}.", "danger")
            return redirect(url_for("new_loan"))

        existing_active = conn.execute(
            "SELECT id FROM loans WHERE member_id=? AND status='active'", (member_id,)
        ).fetchone()
        if existing_active:
            flash(f"{member['name']} already has an active loan. Close it before issuing a new one.", "warning")
            return redirect(url_for("new_loan"))

        balance = dbm.savings_balance(conn)
        if principal > balance:
            flash(f"Fund balance (₹{balance:,.2f}) is lower than the requested loan amount.", "warning")
            return redirect(url_for("new_loan"))

        cur = conn.execute(
            "INSERT INTO loans (member_id, month_id, principal, purpose, taken_date, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')",
            (member_id, month_id, principal, purpose, date.today().isoformat()),
        )
        loan_id = cur.lastrowid

        tiers = loan_interest_tiers(principal, rate)
        for i, interest in enumerate(tiers, start=1):
            due_label = next_month_label(mo["label"], i)
            conn.execute(
                "INSERT INTO loan_installments (loan_id, installment_no, interest_amount, due_label) "
                "VALUES (?, ?, ?, ?)",
                (loan_id, i, interest, due_label),
            )

        conn.execute(
            "INSERT INTO savings_ledger (date, type, description, amount, month_id, related_member_id, related_loan_id) "
            "VALUES (?, 'loan_disbursed', ?, ?, ?, ?, ?)",
            (
                date.today().isoformat(),
                f"Loan disbursed to {member['name']}",
                -principal,
                month_id,
                member_id,
                loan_id,
            ),
        )
        dbm.log_activity(conn, "loan_issued", f"₹{principal:,.0f} loan issued to {member['name']}")
        conn.commit()
        flash(f"Loan of ₹{principal:,.0f} issued to {member['name']}.", "success")
        return redirect(url_for("loan_detail", loan_id=loan_id))

    eligible_members = conn.execute("SELECT * FROM members WHERE active=1 ORDER BY name").fetchall()
    all_months = conn.execute("SELECT * FROM months ORDER BY draw_date DESC").fetchall()
    balance = dbm.savings_balance(conn)
    return render_template(
        "loan_form.html",
        members=eligible_members,
        months=all_months,
        max_loan=max_loan,
        balance=balance,
    )


@app.route("/loans/<int:loan_id>")
@login_required
def loan_detail(loan_id):
    conn = get_db()
    loan = conn.execute(
        "SELECT l.*, m.name as member_name FROM loans l JOIN members m ON m.id=l.member_id WHERE l.id=?",
        (loan_id,),
    ).fetchone()
    if not loan:
        flash("Loan not found.", "danger")
        return redirect(url_for("loans"))
    installments = conn.execute(
        "SELECT * FROM loan_installments WHERE loan_id=? ORDER BY installment_no", (loan_id,)
    ).fetchall()
    cumulative, run = [], 0.0
    for i in installments:
        run += i["interest_amount"]
        cumulative.append(round(run, 2))
    return render_template("loan_detail.html", loan=loan, installments=installments, cumulative=cumulative)


@app.route("/loans/<int:loan_id>/close", methods=["POST"])
@login_required
def close_loan(loan_id):
    conn = get_db()
    loan = conn.execute("SELECT * FROM loans WHERE id=?", (loan_id,)).fetchone()
    if not loan or loan["status"] == "closed":
        flash("Loan not found or already closed.", "warning")
        return redirect(url_for("loans"))
    member = conn.execute("SELECT * FROM members WHERE id=?", (loan["member_id"],)).fetchone()

    try:
        closed_month_no = int(request.form.get("closed_month_no", 1))
        interest_charged = round(float(request.form.get("interest_charged", 0)), 2)
        amount_received = round(float(request.form.get("amount_received", 0)), 2)
    except ValueError:
        flash("Enter valid numbers for interest and amount received.", "danger")
        return redirect(url_for("loan_detail", loan_id=loan_id))
    note = request.form.get("close_note", "").strip()

    principal = loan["principal"]
    total_due = round(principal + interest_charged, 2)

    if interest_charged < 0 or amount_received < 0:
        flash("Amounts cannot be negative.", "danger")
        return redirect(url_for("loan_detail", loan_id=loan_id))
    if amount_received > total_due:
        flash(f"Amount received (₹{amount_received:,.2f}) cannot exceed total due (₹{total_due:,.2f}).", "danger")
        return redirect(url_for("loan_detail", loan_id=loan_id))

    # received money is applied to principal first, then interest
    principal_received = round(min(amount_received, principal), 2)
    interest_received = round(amount_received - principal_received, 2)
    written_off = round(total_due - amount_received, 2)

    if written_off > 0 and not note:
        flash("Add a note explaining the written-off amount.", "danger")
        return redirect(url_for("loan_detail", loan_id=loan_id))

    today = date.today().isoformat()
    conn.execute(
        "UPDATE loans SET status='closed', closed_date=?, interest_charged=?, principal_received=?, "
        "interest_received=?, written_off=?, close_note=? WHERE id=?",
        (today, interest_charged, principal_received, interest_received, written_off, note, loan_id),
    )
    conn.execute(
        "UPDATE loan_installments SET paid=1, paid_date=? WHERE loan_id=? AND installment_no<=?",
        (today, loan_id, closed_month_no),
    )

    if principal_received > 0:
        conn.execute(
            "INSERT INTO savings_ledger (date, type, description, amount, related_member_id, related_loan_id) "
            "VALUES (?, 'loan_principal_repaid', ?, ?, ?, ?)",
            (today, f"Loan principal repaid by {member['name']}", principal_received, member["id"], loan_id),
        )
    if interest_received > 0:
        conn.execute(
            "INSERT INTO savings_ledger (date, type, description, amount, related_member_id, related_loan_id) "
            "VALUES (?, 'loan_interest_received', ?, ?, ?, ?)",
            (today, f"Loan interest from {member['name']} (closed in month {closed_month_no})",
             interest_received, member["id"], loan_id),
        )
    detail = (
        f"{member['name']}'s ₹{principal:,.0f} loan closed (month {closed_month_no}): "
        f"interest ₹{interest_charged:,.2f}, received ₹{amount_received:,.2f}"
    )
    if written_off > 0:
        detail += f", written off ₹{written_off:,.2f} ({note})"
    dbm.log_activity(conn, "loan_closed_with_writeoff" if written_off > 0 else "loan_closed", detail)
    conn.commit()
    flash("Loan closed." + (f" ₹{written_off:,.2f} written off." if written_off > 0 else ""), "success")
    return redirect(url_for("loan_detail", loan_id=loan_id))


# --------------------------------------------------------------- savings --

@app.route("/savings")
@login_required
def savings():
    conn = get_db()
    ledger = conn.execute("SELECT * FROM savings_ledger ORDER BY id DESC").fetchall()
    balance = dbm.savings_balance(conn)
    return render_template("savings.html", ledger=ledger, balance=balance)


# ----------------------------------------------------------------- exits --

@app.route("/exits")
@login_required
def exits():
    conn = get_db()
    all_exits = conn.execute(
        "SELECT e.*, m.name as member_name FROM exits e JOIN members m ON m.id=e.member_id "
        "ORDER BY e.exit_date DESC"
    ).fetchall()
    return render_template("exits.html", exits=all_exits)


@app.route("/exits/new", methods=["GET", "POST"])
@login_required
def new_exit():
    conn = get_db()
    if request.method == "POST":
        member_id = request.form.get("member_id")
        exit_date = request.form.get("exit_date") or date.today().isoformat()
        head_amount = float(dbm.get_setting(conn, "exit_head_amount", 1000))
        member = conn.execute("SELECT * FROM members WHERE id=?", (member_id,)).fetchone()
        conn.execute(
            "INSERT INTO exits (member_id, exit_date, head_amount_required, status) "
            "VALUES (?, ?, ?, 'pending')",
            (member_id, exit_date, head_amount),
        )
        dbm.log_activity(conn, "exit_initiated", f"{member['name']} requested to leave the chit fund")
        conn.commit()
        flash(f"Exit process started for {member['name']}.", "success")
        return redirect(url_for("exits"))
    active_members = conn.execute("SELECT * FROM members WHERE active=1 ORDER BY name").fetchall()
    return render_template("exit_form.html", members=active_members)


@app.route("/exits/<int:exit_id>/head_amount", methods=["POST"])
@login_required
def exit_head_amount(exit_id):
    conn = get_db()
    ex = conn.execute("SELECT * FROM exits WHERE id=?", (exit_id,)).fetchone()
    member = conn.execute("SELECT * FROM members WHERE id=?", (ex["member_id"],)).fetchone()
    conn.execute(
        "UPDATE exits SET head_amount_paid=1, head_amount_paid_date=? WHERE id=?",
        (date.today().isoformat(), exit_id),
    )
    conn.execute(
        "INSERT INTO savings_ledger (date, type, description, amount, related_member_id) "
        "VALUES (?, 'exit_head_amount', ?, ?, ?)",
        (date.today().isoformat(), f"Head-kuri amount from {member['name']} (leaving)", ex["head_amount_required"], member["id"]),
    )
    dbm.log_activity(conn, "exit_head_amount_received", f"₹{ex['head_amount_required']:,.0f} head amount received from {member['name']}")
    conn.commit()
    flash("Head amount recorded.", "success")
    return redirect(url_for("exits"))


@app.route("/exits/<int:exit_id>/settle", methods=["POST"])
@login_required
def settle_exit(exit_id):
    conn = get_db()
    ex = conn.execute("SELECT * FROM exits WHERE id=?", (exit_id,)).fetchone()
    member = conn.execute("SELECT * FROM members WHERE id=?", (ex["member_id"],)).fetchone()
    if not ex["head_amount_paid"]:
        flash("Head-kuri amount must be collected before final settlement.", "danger")
        return redirect(url_for("exits"))
    settlement_amount = float(request.form.get("settlement_amount", 0))
    conn.execute(
        "UPDATE exits SET settlement_amount=?, settlement_date=?, status='settled' WHERE id=?",
        (settlement_amount, date.today().isoformat(), exit_id),
    )
    conn.execute(
        "UPDATE members SET active=0, left_date=? WHERE id=?",
        (date.today().isoformat(), member["id"]),
    )
    dbm.log_activity(conn, "exit_settled", f"{member['name']} settled and left the chit fund (₹{settlement_amount:,.0f} paid out)")
    conn.commit()
    flash(f"{member['name']} has been settled and marked inactive.", "success")
    return redirect(url_for("exits"))


# --------------------------------------------------------------- activity --

@app.route("/activity")
@login_required
def activity():
    conn = get_db()
    q = request.args.get("q", "").strip()
    if q:
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE action LIKE ? OR details LIKE ? ORDER BY id DESC LIMIT 500",
            (f"%{q}%", f"%{q}%"),
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM activity_log ORDER BY id DESC LIMIT 500").fetchall()
    return render_template("activity.html", rows=rows, q=q)


# ------------------------------------------------------------------ main --

if __name__ == "__main__":
    dbm.init_db()
    port = int(os.environ.get("PORT", 5004))
    app.run(host="0.0.0.0", port=port, debug=True)
