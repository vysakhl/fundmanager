# Money Mafia — Kuri Ledger (Python / Flask)

A fund management system for a group savings scheme: monthly contributions,
a monthly lottery, and member debits with automatically doubling monthly interest.

## Features

- **Dashboard** — fund balance, total contributions, outstanding debits, members
  overdue by more than 2 months.
- **Members** — add / rename / remove members (side panel, seeded with 14 placeholder members).
- **Contributions** — mark each member paid/unpaid per month; fund balance updates automatically.
- **Debits** — issue debits up to a configurable per-member limit; interest doubles every
  month the debit stays unpaid (month 1 = base rate, month 2 = 2x, month 3 = 4x, ...).
- **Lottery / Winners** — record each month's winner and prize, with an option to
  settle the winner's outstanding debits from the prize before cash is handed over.
- **Settings** — contribution amount, lottery amount, lottery day, debit limit,
  interest rate, and a manual fund balance override.
- **Rules** — an editable rules page shown to the group.

## Logging in

The app now requires login. On first run it creates one default admin account:

```
```

**Change this password immediately** after first login — go to *My Account* in the
sidebar. From the *Logins* page (admin-only), you can create additional accounts:

- **Admin** — full control, same as the original account.
- **Viewer** — can see every page (dashboard, members, debits, contributions, winners,
  activity log, rules) but cannot add debits, mark contributions paid, record winners,
  edit settings, or make any other change. Good for giving group members visibility
  without letting them touch the ledger. You can optionally link a viewer login to a
  specific member record for reference.

There's no "forgot password" flow — if a viewer forgets their password, an admin resets
it from the Logins page.

## Per-member debit limits

Settings still has a group-wide default debit limit (₹7,500 out of the box). From the
Members page, an admin can now set a **custom limit for any individual member** —
click *Set Limit* and enter an amount, or leave it blank to fall back to the group
default. The Debits page always shows and enforces whichever limit currently applies
to each member.

## Correcting a debit

If a debit was entered with the wrong amount or date, open its **Edit** button on the
Debits page. Changing the amount automatically adjusts the fund balance by the
difference (since issuing a debit originally reduced the fund balance by the old
amount). This does not touch any payments already recorded against the debit — if it
already has partial payments, double check they still make sense after the edit.

## Running locally

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5005 — a JSON data file (`data/money_mafia.json`) is created
automatically on first run, seeded with 14 placeholder members you can rename from
the Members tab. There's no database server or driver involved — all data (members,
contributions, debits, winners, activity log) lives in that one file.

## Deploying to the cloud

This is a plain Flask app with no database dependency, so it runs on any Python host:

**Render.com (free tier, easiest)**
1. Push this folder to a GitHub repo.
2. On Render, "New Web Service" → connect the repo.
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn app:app` (add `gunicorn` to requirements.txt for production)
5. Add an environment variable `SECRET_KEY` with a random value.
6. Render's free tier has an *ephemeral* filesystem — `data/money_mafia.json` will be
   wiped on every redeploy/restart unless you attach a persistent disk (Render offers
   this on paid plans) or set `DATA_FILE` to point at a mounted volume.

**Railway.app** — similar flow: connect the repo, it auto-detects Flask, add `SECRET_KEY`.
Attach a volume and set `DATA_FILE=/data/money_mafia.json` for data to survive restarts.

**Your own VPS (recommended for this use case)** — since the JSON file needs to persist
on disk between deploys, running this on your own server (systemd + Waitress/gunicorn,
reverse-proxied through nginx/Apache) is the simplest option — the file just lives
wherever you put the project folder and survives restarts by default.

### Concurrent writes

The JSON file is rewritten in full on every change and guarded by an in-process lock,
which is fine for a small group (14 members) making occasional edits. It is not built
for many people hammering the same server with simultaneous writes — for that scale,
a real database would be a better fit. For this use case, it's not a concern.

### Backing up / versioning the data

Because it's one plain JSON file, you can back it up by simply copying
`data/money_mafia.json` anywhere (Dropbox, email, another server). It's excluded from
git by default (see `.gitignore`) since it changes constantly — but if you'd like a
running history of every change via git commits instead of (or alongside) the activity
log, you can remove it from `.gitignore` and commit it periodically.

## No built-in login

Right now anyone with the URL can add debits, record winners, or edit settings —
there's no authentication. That's fine for a small trusted group, but if you want an
admin-only login (with members only able to view), that's a reasonable next addition —
just ask.
