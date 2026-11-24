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
username: admin
password: admin123
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

## Closing a debit (Full Paid) and write-offs

**Full Paid** opens a close page where an admin can:

1. Edit the **interest to collect** (it starts at the automatic figure).
2. Enter the **amount actually received**.
3. Anything left over is a **write-off** — a note is required, the debit is marked
   *Written off*, and the amount is recorded in the activity log.

Only the amount received is added to the fund balance. Written-off money is never added.

## Installing on a phone (PWA)

The site is an installable web app. On Android, open it in Chrome and tap **Install app**
(a button in the sidebar and on the login page, or Chrome menu → *Install app*). On iPhone,
open it in Safari → Share → *Add to Home Screen*. It then opens full-screen with its own icon.
Only static files are cached; fund data is always loaded live, and an offline screen shows
when there is no connection.

## Running locally

```bash
python -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000 — by default, a JSON data file (`data/money_mafia.json`) is
created automatically on first run, seeded with 14 placeholder members you can rename
from the Members tab.

## Storage: local JSON file vs. MongoDB

The app supports two interchangeable storage backends — same code, same data shape,
switched automatically by an environment variable:

- **Local JSON file** (default) — used whenever `MONGODB_URI` is not set. Everything
  lives in one plain-text file on disk. Simplest option, ideal for your own VPS where
  the disk persists between restarts.
- **MongoDB** — used automatically when you set `MONGODB_URI`. The same data is stored
  as a single document in a MongoDB database instead of a local file. Use this when
  deploying somewhere with an *ephemeral* filesystem (like Render's free tier), where a
  local file would get wiped on every redeploy, restart, or idle spin-down.

You don't need to touch any code to switch — just set (or unset) the environment
variable.

## Deploying to Render — free, with MongoDB Atlas

This is the free-forever way to host this app on Render without a paid disk:

1. **Create a free MongoDB Atlas cluster** — sign up at
   [mongodb.com/cloud/atlas](https://www.mongodb.com/cloud/atlas), create a new project,
   and deploy an **M0 (Free) cluster**. It never expires and includes 512MB of storage —
   far more than this app needs.
2. **Create a database user** — in Atlas, under *Database Access*, add a user with a
   username and password (not your Atlas login — a separate DB credential).
3. **Allow network access** — under *Network Access*, add `0.0.0.0/0` (allow from
   anywhere) so Render can reach it. Atlas free clusters don't support fixed IP
   allowlisting from Render easily, so this is the practical option for a small app.
4. **Get your connection string** — click *Connect* → *Drivers* → copy the
   `mongodb+srv://...` URI. Replace `<password>` in it with your database user's
   password.
5. **On Render**, create the Web Service on the **Free** instance type:
   - Build Command: `pip install -r requirements.txt`
   - Start Command: `gunicorn app:app`
   - Environment variables: `SECRET_KEY` (a random string) and `MONGODB_URI` (the
     connection string from step 4).
   - No disk needed — skip that step entirely.
6. Deploy. Your data now lives in MongoDB Atlas, not on Render's disk, so it survives
   every redeploy, restart, and 15-minute idle spin-down.

The trade-off versus a paid Render disk: the free web service still sleeps after 15
minutes of inactivity and takes ~30–60 seconds to wake on the next visit — that part
is unavoidable on Render's free tier. But your actual fund data (members, debits,
contributions) is safe either way, since it's no longer stored on Render at all.

## Alternative: your own VPS

Since the JSON file just needs a disk that persists, running this on your own server
(systemd + Waitress/gunicorn, reverse-proxied through nginx/Apache) works with zero
extra setup and no external database — just don't set `MONGODB_URI`, and the file lives
wherever you put the project folder.

### Concurrent writes

Whichever backend you use, writes are guarded by an in-process lock, which is fine for
a small group (14 members) making occasional edits. It is not built for many people
hammering the same server with simultaneous writes — for that scale, a proper
multi-writer database would be a better fit. For this use case, it's not a concern.

### Backing up the data

**Local file backend:** copy `data/money_mafia.json` anywhere (Dropbox, email, another
server). It's excluded from git by default (see `.gitignore`) since it changes
constantly.

**MongoDB backend:** use `mongodump`/`mongoexport`, or just export the single document
from the Atlas web UI's Data Explorer.
