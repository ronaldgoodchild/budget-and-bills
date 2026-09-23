"""Local budgeting app: checking-account CSV import + bill calendar.

Run with:  python app.py
Then open: http://127.0.0.1:5000
"""
import csv
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta

from flask import Flask, g, jsonify, request, render_template, Response

import debts as debts_module
import notifications
import reconcile
import rules
import scheduling

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# A PyInstaller onefile .exe extracts itself (this script, templates,
# static, schema.sql, seed_budget.db) into a NEW temp folder every single
# launch and wipes it on exit - BASE_DIR points there, which is fine for
# read-only bundled resources but would silently lose the database between
# runs. sys.executable's own folder is the one stable, writable location an
# exe recipient actually controls, so that's where the real data/budget.db
# has to live when frozen.
DATA_DIR = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else BASE_DIR
DB_PATH = os.path.join(DATA_DIR, "data", "budget.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")
SEED_DB_PATH = os.path.join(BASE_DIR, "seed_budget.db")

app = Flask(__name__)
# Auto-reload templates on edit even with debug off (debug's interactive
# code-execution console is what we're avoiding, not this).
app.config["TEMPLATES_AUTO_RELOAD"] = True


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def _add_column_if_missing(conn, table, column, coltype):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    if not os.path.exists(DB_PATH) and os.path.exists(SEED_DB_PATH):
        # First run: start from the bundled sample data instead of an empty
        # database (demo copy only - the real copy has no seed_budget.db).
        shutil.copy(SEED_DB_PATH, DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    # Migrations for columns added after initial release - CREATE TABLE IF NOT
    # EXISTS above won't add columns to a table that already exists.
    _add_column_if_missing(conn, "bill_payments", "matched_transaction_id", "INTEGER")
    _add_column_if_missing(conn, "bill_payments", "prior_next_due_date", "TEXT")
    _add_column_if_missing(conn, "bill_payments", "prior_amount", "REAL")
    _add_column_if_missing(conn, "bill_payments", "auto_matched", "INTEGER DEFAULT 0")
    _add_column_if_missing(conn, "bills", "pay_url", "TEXT")
    _add_column_if_missing(conn, "bills", "payment_method", "TEXT")
    _add_column_if_missing(conn, "bills", "logo_domain", "TEXT")
    _backfill_logo_domains(conn)
    conn.commit()
    conn.close()


def _backfill_logo_domains(conn):
    """One-time (per bill) best-effort logo guess for bills that don't have
    one yet - covers bills that existed before this feature, so they get a
    logo without the user having to edit each one. Only touches NULL (never
    considered) - an empty string means the user deliberately cleared it, and
    that choice is never overwritten."""
    rows = conn.execute(
        "SELECT id, name, merchant_key FROM bills WHERE logo_domain IS NULL"
    ).fetchall()
    for row in rows:
        guess = rules.guess_logo_domain(row[1]) or (row[2] and rules.guess_logo_domain(row[2])) or ""
        if guess:
            conn.execute("UPDATE bills SET logo_domain = ? WHERE id = ?", (guess, row[0]))


def get_setting(db, key, default=None):
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(db, key, value):
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )


def get_merchant_overrides(db):
    rows = db.execute("SELECT merchant_key, category FROM merchant_categories").fetchall()
    return {r["merchant_key"]: r["category"] for r in rows}


def today_iso():
    return datetime.now().strftime("%Y-%m-%d")


def project_with_bills(bills, balance, as_of_date, horizon_days):
    """Lower-level projection helper that takes an explicit bills list, so
    callers (like the What-If simulator) can pass a modified copy without
    touching the database. Returns (days_dict, safe_to_spend, next_income_date)."""
    end = as_of_date + timedelta(days=horizon_days)
    days = scheduling.project_balances(bills, as_of_date, end, balance, as_of_date)

    next_income_date = None
    for date_str in sorted(days.keys()):
        d = scheduling.parse(date_str)
        if d <= as_of_date:
            continue
        if any(e["type"] == "income" for e in days[date_str]["events"]):
            next_income_date = date_str
            break

    if next_income_date:
        window = {k: v for k, v in days.items() if as_of_date <= scheduling.parse(k) < scheduling.parse(next_income_date)}
    else:
        window = days
    floor_vals = [v["balance"] for v in window.values() if v["balance"] is not None]
    safe_to_spend = round(min(floor_vals), 2) if floor_vals else None

    return days, safe_to_spend, next_income_date


def compute_forecast(db, horizon_days):
    """Shared projection helper used by /api/dashboard and /api/forecast."""
    bills = [dict(r) for r in db.execute("SELECT * FROM bills WHERE active = 1").fetchall()]
    balance = float(get_setting(db, "current_balance", "0") or 0)
    as_of = get_setting(db, "current_balance_date", today_iso())
    as_of_date = scheduling.parse(as_of)
    return project_with_bills(bills, balance, as_of_date, horizon_days)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


# ---------------------------------------------------------------------------
# CSV Import
# ---------------------------------------------------------------------------

def parse_checking_csv(file_bytes):
    text = file_bytes.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for row in reader:
        # Normalize header keys (strip whitespace, handle "CHECK #")
        norm = {k.strip().upper(): (v.strip() if v else "") for k, v in row.items() if k}
        date_raw = norm.get("DATE", "")
        desc = norm.get("DESCRIPTION", "")
        amount_raw = norm.get("AMOUNT", "")
        check_num = norm.get("CHECK #", "") or norm.get("CHECK", "")
        status = norm.get("STATUS", "")
        if not date_raw or not desc:
            continue
        try:
            date_iso = datetime.strptime(date_raw, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        try:
            amount = float(amount_raw.replace(",", ""))
        except ValueError:
            continue
        rows.append({
            "date": date_iso,
            "description": desc,
            "amount": amount,
            "check_num": check_num,
            "status": status,
        })
    return rows


def make_hash(row, seq=0):
    # seq disambiguates genuinely repeated same-day/same-amount/same-description
    # transactions (e.g. two identical toll charges) so they aren't collapsed
    # into one by the dedupe logic. Rows are hashed in stable file order, so
    # re-importing the same overlapping history yields the same seq per row.
    key = f"{row['date']}|{row['description'].strip()}|{row['amount']}|{row['check_num']}|{seq}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@app.route("/api/import", methods=["POST"])
def api_import():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    data = f.read()
    try:
        rows = parse_checking_csv(data)
    except Exception as e:
        return jsonify({"error": f"Could not parse CSV: {e}"}), 400

    if not rows:
        return jsonify({"error": "No valid rows found in CSV"}), 400

    db = get_db()
    overrides = get_merchant_overrides(db)
    added, updated, skipped = 0, 0, 0
    now = datetime.now().isoformat()
    seq_counts = {}

    for row in rows:
        base_key = (row["date"], row["description"].strip(), row["amount"], row["check_num"])
        seq = seq_counts.get(base_key, 0)
        seq_counts[base_key] = seq + 1
        h = make_hash(row, seq)
        merchant_key = rules.normalize_merchant(row["description"])
        category, is_income = rules.categorize(row["description"], overrides)
        existing = db.execute("SELECT id, status FROM transactions WHERE hash = ?", (h,)).fetchone()
        if existing:
            if existing["status"] != row["status"]:
                db.execute(
                    "UPDATE transactions SET status = ? WHERE id = ?",
                    (row["status"], existing["id"]),
                )
                updated += 1
            else:
                skipped += 1
            continue
        db.execute(
            "INSERT INTO transactions (date, description, amount, check_num, status, "
            "category, merchant_key, is_income, hash, imported_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["date"], row["description"], row["amount"], row["check_num"], row["status"],
             category, merchant_key, int(is_income), h, now),
        )
        added += 1
    db.commit()

    reconciled = reconcile.reconcile_bills(db)
    db.commit()
    suggestions = reconcile.suggest_reconciliations(db)

    return jsonify({
        "added": added,
        "updated": updated,
        "skipped": skipped,
        "total_rows": len(rows),
        "reconciled": reconciled,
        "suggestions": suggestions,
    })


@app.route("/api/reconcile", methods=["POST"])
def api_reconcile():
    db = get_db()
    reconciled = reconcile.reconcile_bills(db)
    db.commit()
    suggestions = reconcile.suggest_reconciliations(db)
    return jsonify({"reconciled": reconciled, "suggestions": suggestions})


@app.route("/api/reconcile/confirm", methods=["POST"])
def api_reconcile_confirm():
    db = get_db()
    body = request.get_json(force=True)
    bill_id = body.get("bill_id")
    transaction_id = body.get("transaction_id")
    bill = db.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    txn = db.execute("SELECT * FROM transactions WHERE id = ?", (transaction_id,)).fetchone()
    if not bill or not txn:
        return jsonify({"error": "bill or transaction not found"}), 404
    result = reconcile.apply_match(db, dict(bill), dict(txn))
    db.commit()
    return jsonify(result)


@app.route("/api/reconcile/dismiss", methods=["POST"])
def api_reconcile_dismiss():
    db = get_db()
    body = request.get_json(force=True)
    bill_id = body.get("bill_id")
    transaction_id = body.get("transaction_id")
    db.execute(
        "INSERT OR IGNORE INTO dismissed_matches (bill_id, transaction_id) VALUES (?, ?)",
        (bill_id, transaction_id),
    )
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Transactions
# ---------------------------------------------------------------------------

@app.route("/api/transactions")
def api_transactions():
    db = get_db()
    limit = request.args.get("limit", 200, type=int)
    category = request.args.get("category")
    search = request.args.get("q", "").strip()
    clauses = []
    params = []
    if category:
        clauses.append("category = ?")
        params.append(category)
    if search:
        clauses.append("description LIKE ?")
        params.append(f"%{search}%")
    q = "SELECT * FROM transactions"
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY date DESC, id DESC LIMIT ?"
    params.append(limit)
    rows = db.execute(q, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/transactions/<int:txn_id>/category", methods=["PUT"])
def api_update_category(txn_id):
    db = get_db()
    body = request.get_json(force=True)
    category = body.get("category", "").strip()
    apply_to_all = body.get("apply_to_all", False)
    if not category:
        return jsonify({"error": "category required"}), 400

    txn = db.execute("SELECT * FROM transactions WHERE id = ?", (txn_id,)).fetchone()
    if not txn:
        return jsonify({"error": "not found"}), 404

    db.execute("UPDATE transactions SET category = ? WHERE id = ?", (category, txn_id))
    if apply_to_all and txn["merchant_key"]:
        db.execute(
            "INSERT INTO merchant_categories (merchant_key, category) VALUES (?, ?) "
            "ON CONFLICT(merchant_key) DO UPDATE SET category = excluded.category",
            (txn["merchant_key"], category),
        )
        db.execute(
            "UPDATE transactions SET category = ? WHERE merchant_key = ?",
            (category, txn["merchant_key"]),
        )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/categories")
def api_categories():
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT category FROM transactions ORDER BY category"
    ).fetchall()
    cats = sorted(set([r["category"] for r in rows] + [c for _, c, _ in rules.DEFAULT_RULES]))
    return jsonify(cats)


# ---------------------------------------------------------------------------
# Bills
# ---------------------------------------------------------------------------

def bill_to_dict(row):
    return dict(row)


@app.route("/api/bills")
def api_bills():
    db = get_db()
    show_inactive = request.args.get("all", "false") == "true"
    q = "SELECT * FROM bills"
    if not show_inactive:
        q += " WHERE active = 1"
    q += " ORDER BY next_due_date ASC"
    rows = db.execute(q).fetchall()
    return jsonify([bill_to_dict(r) for r in rows])


def _clean_pay_url(url):
    """Only allow http(s) links - never store a javascript:/data: URI that
    could execute when someone clicks the "Pay Online" link later."""
    url = (url or "").strip()
    if not url:
        return ""
    if not re.match(r"^https?://", url, re.IGNORECASE):
        raise ValueError("Pay URL must start with http:// or https://")
    return url


@app.route("/api/bills", methods=["POST"])
def api_create_bill():
    db = get_db()
    b = request.get_json(force=True)
    required = ["name", "amount", "frequency", "next_due_date"]
    for field in required:
        if field not in b or b[field] in (None, ""):
            return jsonify({"error": f"Missing field: {field}"}), 400
    try:
        pay_url = _clean_pay_url(b.get("pay_url"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    logo_domain = (b.get("logo_domain") or "").strip()
    if not logo_domain:
        # Auto-guess a logo for eye candy - purely cosmetic, never blocks saving.
        logo_domain = rules.guess_logo_domain(b["name"]) or (
            b.get("merchant_key") and rules.guess_logo_domain(b["merchant_key"])
        ) or None
    db.execute(
        "INSERT INTO bills (name, amount, category, type, frequency, due_day, "
        "next_due_date, autopay, account, notes, active, source, merchant_key, "
        "pay_url, payment_method, logo_domain) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            b["name"], float(b["amount"]), b.get("category", "Bill"),
            b.get("type", "expense"), b["frequency"], b.get("due_day"),
            b["next_due_date"], int(bool(b.get("autopay"))), b.get("account", "Checking"),
            b.get("notes", ""), 1, b.get("source", "manual"), b.get("merchant_key"),
            pay_url, b.get("payment_method", ""), logo_domain,
        ),
    )
    db.commit()
    return jsonify({"ok": True, "id": db.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]})


@app.route("/api/bills/<int:bill_id>", methods=["PUT"])
def api_update_bill(bill_id):
    db = get_db()
    b = request.get_json(force=True)
    existing = db.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if not existing:
        return jsonify({"error": "not found"}), 404
    fields = ["name", "amount", "category", "type", "frequency", "due_day",
              "next_due_date", "autopay", "account", "notes", "active",
              "pay_url", "payment_method", "logo_domain"]
    updates = {f: b[f] for f in fields if f in b}
    if "pay_url" in updates:
        try:
            updates["pay_url"] = _clean_pay_url(updates["pay_url"])
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
    if not updates:
        return jsonify({"error": "no fields to update"}), 400
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [bill_id]
    db.execute(f"UPDATE bills SET {set_clause} WHERE id = ?", values)
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/bills/<int:bill_id>", methods=["DELETE"])
def api_delete_bill(bill_id):
    db = get_db()
    db.execute("UPDATE bills SET active = 0 WHERE id = ?", (bill_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/bills/<int:bill_id>/pay", methods=["POST"])
def api_pay_bill(bill_id):
    db = get_db()
    bill = db.execute("SELECT * FROM bills WHERE id = ?", (bill_id,)).fetchone()
    if not bill:
        return jsonify({"error": "not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    paid_date = body.get("paid_date", today_iso())
    due_date = bill["next_due_date"]

    cur = db.execute(
        "INSERT INTO bill_payments (bill_id, due_date, paid_date, amount, status, "
        "prior_next_due_date, prior_amount) VALUES (?, ?, ?, ?, 'paid', ?, ?)",
        (bill_id, due_date, paid_date, bill["amount"], due_date, bill["amount"]),
    )
    payment_id = cur.lastrowid

    nxt = scheduling.advance(scheduling.parse(due_date), bill["frequency"], bill["due_day"])
    if nxt is None:
        db.execute("UPDATE bills SET active = 0 WHERE id = ?", (bill_id,))
    else:
        db.execute(
            "UPDATE bills SET next_due_date = ? WHERE id = ?",
            (scheduling.fmt(nxt), bill_id),
        )
    db.commit()
    return jsonify({"ok": True, "next_due_date": scheduling.fmt(nxt) if nxt else None, "payment_id": payment_id})


@app.route("/api/bill-payments/<int:payment_id>", methods=["DELETE"])
def api_undo_payment(payment_id):
    db = get_db()
    payment = db.execute("SELECT * FROM bill_payments WHERE id = ?", (payment_id,)).fetchone()
    if not payment:
        return jsonify({"error": "not found"}), 404
    updates = {"active": 1}
    if payment["prior_next_due_date"]:
        updates["next_due_date"] = payment["prior_next_due_date"]
    if payment["prior_amount"] is not None:
        updates["amount"] = payment["prior_amount"]
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.execute(f"UPDATE bills SET {set_clause} WHERE id = ?", list(updates.values()) + [payment["bill_id"]])
    db.execute("DELETE FROM bill_payments WHERE id = ?", (payment_id,))
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/bills/<int:bill_id>/history")
def api_bill_history(bill_id):
    db = get_db()
    rows = db.execute(
        "SELECT * FROM bill_payments WHERE bill_id = ? ORDER BY due_date DESC", (bill_id,)
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Recurring charge detection
# ---------------------------------------------------------------------------

@app.route("/api/detect-recurring")
def api_detect_recurring():
    db = get_db()
    txns = db.execute(
        "SELECT date, description, amount, merchant_key FROM transactions "
        "WHERE merchant_key IS NOT NULL ORDER BY date ASC"
    ).fetchall()
    existing_bill_keys = set(
        r["merchant_key"] for r in db.execute(
            "SELECT DISTINCT merchant_key FROM bills WHERE active = 1 AND merchant_key IS NOT NULL"
        ).fetchall()
    )
    dismissed = set(
        r["merchant_key"] for r in db.execute("SELECT merchant_key FROM dismissed_suggestions").fetchall()
    )
    candidates = rules.detect_recurring([dict(t) for t in txns], existing_bill_keys, dismissed)
    return jsonify(candidates)


@app.route("/api/detect-recurring/dismiss", methods=["POST"])
def api_dismiss_recurring():
    db = get_db()
    body = request.get_json(force=True)
    key = body.get("merchant_key")
    if not key:
        return jsonify({"error": "merchant_key required"}), 400
    db.execute(
        "INSERT OR IGNORE INTO dismissed_suggestions (merchant_key) VALUES (?)", (key,)
    )
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Calendar / projection
# ---------------------------------------------------------------------------

@app.route("/api/calendar")
def api_calendar():
    db = get_db()
    month_str = request.args.get("month")  # YYYY-MM
    if month_str:
        year, month = map(int, month_str.split("-"))
    else:
        now = datetime.now()
        year, month = now.year, now.month

    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year, 12, 31)
    else:
        end = datetime(year, month + 1, 1) - timedelta(days=1)
    # Pad a bit so projection continuity across month view is sane
    proj_start = start - timedelta(days=31)

    bills = [dict(r) for r in db.execute("SELECT * FROM bills WHERE active = 1").fetchall()]
    balance = float(get_setting(db, "current_balance", "0") or 0)
    as_of = get_setting(db, "current_balance_date", today_iso())
    as_of_date = scheduling.parse(as_of)

    days = scheduling.project_balances(bills, proj_start, end, balance, as_of_date)
    visible_days = {k: v for k, v in days.items() if start <= scheduling.parse(k) <= end}

    # Bills whose next occurrence has already passed and haven't been marked
    # paid - these sit on a day that may be scrolled past/out of view, so
    # surface them separately no matter which month is being viewed.
    overdue = []
    for b in bills:
        due = scheduling.parse(b["next_due_date"])
        if due < as_of_date:
            overdue.append({
                "bill_id": b["id"], "name": b["name"], "amount": b["amount"],
                "type": b["type"], "category": b.get("category"),
                "due_date": b["next_due_date"],
            })
    overdue.sort(key=lambda e: e["due_date"])

    return jsonify({
        "year": year, "month": month,
        "start": scheduling.fmt(start), "end": scheduling.fmt(end),
        "days": visible_days,
        "overdue": overdue,
    })


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.route("/api/dashboard")
def api_dashboard():
    db = get_db()
    balance = float(get_setting(db, "current_balance", "0") or 0)
    as_of = get_setting(db, "current_balance_date", today_iso())
    as_of_date = scheduling.parse(as_of)

    bills = [dict(r) for r in db.execute("SELECT * FROM bills WHERE active = 1").fetchall()]

    horizon_end = as_of_date + timedelta(days=30)
    days = scheduling.project_balances(bills, as_of_date, horizon_end, balance, as_of_date)

    upcoming = []
    for date_str in sorted(days.keys()):
        for e in days[date_str]["events"]:
            upcoming.append({**e, "date": date_str})
    upcoming_7 = [u for u in upcoming if u["date"] <= scheduling.fmt(as_of_date + timedelta(days=7))]

    lowest_balance = None
    lowest_date = None
    for date_str in sorted(days.keys()):
        bal = days[date_str]["balance"]
        if bal is not None and (lowest_balance is None or bal < lowest_balance):
            lowest_balance = bal
            lowest_date = date_str

    monthly_expense_total = sum(
        b["amount"] * (1 if b["frequency"] != "weekly" else 4.33) *
        (1 if b["frequency"] not in ("biweekly",) else 2.17)
        for b in bills if b["type"] == "expense"
    )
    monthly_income_total = sum(
        b["amount"] * (1 if b["frequency"] != "weekly" else 4.33) *
        (1 if b["frequency"] not in ("biweekly",) else 2.17)
        for b in bills if b["type"] == "income"
    )

    # 90-day spending by category (from actual transactions, expenses only)
    cat_rows = db.execute(
        "SELECT category, SUM(-amount) as total, COUNT(*) as cnt FROM transactions "
        "WHERE amount < 0 AND status = 'Posted' GROUP BY category ORDER BY total DESC"
    ).fetchall()

    last_import = db.execute(
        "SELECT MAX(imported_at) as t FROM transactions"
    ).fetchone()["t"]

    _, safe_to_spend, next_income_date = compute_forecast(db, 60)

    return jsonify({
        "current_balance": balance,
        "as_of_date": as_of,
        "lowest_projected_balance": lowest_balance,
        "lowest_projected_date": lowest_date,
        "safe_to_spend": safe_to_spend,
        "next_income_date": next_income_date,
        "upcoming_7_days": upcoming_7,
        "upcoming_30_days": upcoming,
        "monthly_expense_estimate": round(monthly_expense_total, 2),
        "monthly_income_estimate": round(monthly_income_total, 2),
        "spending_by_category_90d": [dict(r) for r in cat_rows],
        "last_import": last_import,
        "bill_count": len(bills),
    })


# ---------------------------------------------------------------------------
# Forecast (for the balance chart)
# ---------------------------------------------------------------------------

@app.route("/api/forecast")
def api_forecast():
    db = get_db()
    horizon_days = request.args.get("days", 60, type=int)
    days, safe_to_spend, next_income_date = compute_forecast(db, horizon_days)
    series = [{"date": d, "balance": days[d]["balance"]} for d in sorted(days.keys())]
    return jsonify({
        "series": series,
        "safe_to_spend": safe_to_spend,
        "next_income_date": next_income_date,
    })


@app.route("/api/whatif", methods=["POST"])
def api_whatif():
    """Preview moving one or more bills' due dates without saving anything,
    so you can see the effect on your cash-flow cushion before actually
    calling the company to request the change."""
    db = get_db()
    body = request.get_json(force=True)
    changes = body.get("changes", [])
    horizon_days = body.get("days", 60)

    balance = float(get_setting(db, "current_balance", "0") or 0)
    as_of = get_setting(db, "current_balance_date", today_iso())
    as_of_date = scheduling.parse(as_of)

    baseline_bills = [dict(r) for r in db.execute("SELECT * FROM bills WHERE active = 1").fetchall()]
    modified_bills = [dict(b) for b in baseline_bills]

    applied = []
    for change in changes:
        bill_id = change.get("bill_id")
        new_date_str = change.get("next_due_date")
        if not bill_id or not new_date_str:
            continue
        try:
            new_date = scheduling.parse(new_date_str)
        except ValueError:
            continue
        for b in modified_bills:
            if b["id"] == bill_id:
                b["next_due_date"] = scheduling.fmt(new_date)
                if b["frequency"] in ("monthly", "annual"):
                    b["due_day"] = new_date.day
                applied.append({"bill_id": bill_id, "bill_name": b["name"], "new_due_date": b["next_due_date"]})

    baseline_days, baseline_safe, baseline_income_date = project_with_bills(baseline_bills, balance, as_of_date, horizon_days)
    modified_days, modified_safe, modified_income_date = project_with_bills(modified_bills, balance, as_of_date, horizon_days)

    def summarize(days):
        series = [{"date": d, "balance": days[d]["balance"]} for d in sorted(days.keys())]
        lowest = min((v["balance"] for v in days.values() if v["balance"] is not None), default=None)
        lowest_date = None
        for d in sorted(days.keys()):
            if days[d]["balance"] == lowest:
                lowest_date = d
                break
        return series, lowest, lowest_date

    baseline_series, baseline_lowest, baseline_lowest_date = summarize(baseline_days)
    modified_series, modified_lowest, modified_lowest_date = summarize(modified_days)

    return jsonify({
        "applied_changes": applied,
        "baseline": {
            "series": baseline_series, "safe_to_spend": baseline_safe,
            "lowest_balance": baseline_lowest, "lowest_date": baseline_lowest_date,
        },
        "modified": {
            "series": modified_series, "safe_to_spend": modified_safe,
            "lowest_balance": modified_lowest, "lowest_date": modified_lowest_date,
        },
    })


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------

@app.route("/api/budgets")
def api_budgets():
    db = get_db()
    limits = {r["category"]: r["monthly_limit"] for r in db.execute("SELECT * FROM budgets").fetchall()}
    spent_rows = db.execute(
        "SELECT category, SUM(-amount) as total FROM transactions "
        "WHERE amount < 0 AND status = 'Posted' AND date >= date('now', '-30 days') "
        "GROUP BY category"
    ).fetchall()
    spent = {r["category"]: r["total"] for r in spent_rows}

    categories = sorted(set(limits.keys()) | set(spent.keys()))
    result = []
    for cat in categories:
        result.append({
            "category": cat,
            "monthly_limit": limits.get(cat),
            "spent_last_30d": round(spent.get(cat, 0.0), 2),
        })
    return jsonify(result)


@app.route("/api/budgets", methods=["POST"])
def api_set_budget():
    db = get_db()
    body = request.get_json(force=True)
    category = body.get("category", "").strip()
    limit = body.get("monthly_limit")
    if not category or limit in (None, ""):
        return jsonify({"error": "category and monthly_limit required"}), 400
    db.execute(
        "INSERT INTO budgets (category, monthly_limit) VALUES (?, ?) "
        "ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
        (category, float(limit)),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/budgets/<path:category>", methods=["DELETE"])
def api_delete_budget(category):
    db = get_db()
    db.execute("DELETE FROM budgets WHERE category = ?", (category,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Debts (payoff tracker)
# ---------------------------------------------------------------------------

def debt_with_payoff(row):
    d = dict(row)
    d["payoff"] = debts_module.payoff_summary(d["balance"], d["apr"], d["minimum_payment"])
    return d


@app.route("/api/debts")
def api_debts():
    db = get_db()
    rows = db.execute("SELECT * FROM debts WHERE active = 1 ORDER BY id").fetchall()
    return jsonify([debt_with_payoff(r) for r in rows])


@app.route("/api/debts", methods=["POST"])
def api_create_debt():
    db = get_db()
    b = request.get_json(force=True)
    required = ["name", "balance", "minimum_payment"]
    for field in required:
        if field not in b or b[field] in (None, ""):
            return jsonify({"error": f"Missing field: {field}"}), 400
    cur = db.execute(
        "INSERT INTO debts (name, balance, apr, minimum_payment, notes, active) "
        "VALUES (?, ?, ?, ?, ?, 1)",
        (b["name"], float(b["balance"]), float(b.get("apr", 0)), float(b["minimum_payment"]), b.get("notes", "")),
    )
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid})


@app.route("/api/debts/<int:debt_id>", methods=["PUT"])
def api_update_debt(debt_id):
    db = get_db()
    b = request.get_json(force=True)
    existing = db.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    if not existing:
        return jsonify({"error": "not found"}), 404
    fields = ["name", "balance", "apr", "minimum_payment", "notes", "active", "linked_bill_id"]
    updates = {f: b[f] for f in fields if f in b}
    if not updates:
        return jsonify({"error": "no fields to update"}), 400
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    db.execute(f"UPDATE debts SET {set_clause} WHERE id = ?", list(updates.values()) + [debt_id])
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/debts/<int:debt_id>", methods=["DELETE"])
def api_delete_debt(debt_id):
    db = get_db()
    db.execute("UPDATE debts SET active = 0 WHERE id = ?", (debt_id,))
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Category types (essential / discretionary) + Money Leaks insights
# ---------------------------------------------------------------------------

def get_category_kind(db, category, overrides=None):
    if overrides is None:
        overrides = {r["category"]: r["kind"] for r in db.execute("SELECT * FROM category_types").fetchall()}
    return overrides.get(category, rules.default_category_kind(category))


@app.route("/api/category-types", methods=["GET"])
def api_get_category_types():
    db = get_db()
    cats = [r["category"] for r in db.execute(
        "SELECT DISTINCT category FROM transactions WHERE amount < 0"
    ).fetchall()]
    overrides = {r["category"]: r["kind"] for r in db.execute("SELECT * FROM category_types").fetchall()}
    return jsonify([{"category": c, "kind": get_category_kind(db, c, overrides)} for c in sorted(cats)])


@app.route("/api/category-types", methods=["POST"])
def api_set_category_type():
    db = get_db()
    body = request.get_json(force=True)
    category = body.get("category", "").strip()
    kind = body.get("kind", "").strip()
    if not category or kind not in ("essential", "discretionary", "neutral"):
        return jsonify({"error": "category and a valid kind are required"}), 400
    db.execute(
        "INSERT INTO category_types (category, kind) VALUES (?, ?) "
        "ON CONFLICT(category) DO UPDATE SET kind = excluded.kind",
        (category, kind),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/insights")
def api_insights():
    db = get_db()
    days = request.args.get("days", 90, type=int)
    overrides = {r["category"]: r["kind"] for r in db.execute("SELECT * FROM category_types").fetchall()}

    rows = db.execute(
        "SELECT category, SUM(-amount) as total, COUNT(*) as cnt FROM transactions "
        "WHERE amount < 0 AND status = 'Posted' AND date >= date('now', ?) "
        "GROUP BY category ORDER BY total DESC",
        (f"-{days} days",),
    ).fetchall()

    income_rows = db.execute(
        "SELECT SUM(amount) as total FROM transactions "
        "WHERE amount > 0 AND status = 'Posted' AND is_income = 1 AND date >= date('now', ?)",
        (f"-{days} days",),
    ).fetchone()
    income_total = income_rows["total"] or 0
    income_monthly_avg = round(income_total / days * 30, 2) if days else 0

    months = days / 30.0
    categories = []
    essential_total = discretionary_total = neutral_total = 0.0

    for r in rows:
        kind = get_category_kind(db, r["category"], overrides)
        monthly_avg = round(r["total"] / months, 2)
        entry = {
            "category": r["category"],
            "kind": kind,
            "total": round(r["total"], 2),
            "monthly_avg": monthly_avg,
            "cnt": r["cnt"],
            "pct_of_income": round((monthly_avg / income_monthly_avg) * 100, 1) if income_monthly_avg else None,
            "half_savings_monthly": round(monthly_avg / 2, 2),
        }
        categories.append(entry)
        if kind == "essential":
            essential_total += r["total"]
        elif kind == "discretionary":
            discretionary_total += r["total"]
        else:
            neutral_total += r["total"]

    discretionary_categories = sorted(
        [c for c in categories if c["kind"] == "discretionary"],
        key=lambda c: c["total"], reverse=True,
    )

    return jsonify({
        "days": days,
        "income_monthly_avg": income_monthly_avg,
        "essential_total": round(essential_total, 2),
        "essential_monthly_avg": round(essential_total / months, 2),
        "discretionary_total": round(discretionary_total, 2),
        "discretionary_monthly_avg": round(discretionary_total / months, 2),
        "discretionary_pct_of_income": round((discretionary_total / months) / income_monthly_avg * 100, 1) if income_monthly_avg else None,
        "neutral_total": round(neutral_total, 2),
        "categories": categories,
        "top_discretionary": discretionary_categories[:6],
    })


@app.route("/api/reports/spending")
def api_reports_spending():
    db = get_db()
    days = request.args.get("days", 90, type=int)

    cat_rows = db.execute(
        "SELECT category, SUM(-amount) as total, COUNT(*) as cnt FROM transactions "
        "WHERE amount < 0 AND status = 'Posted' AND date >= date('now', ?) "
        "GROUP BY category ORDER BY total DESC",
        (f"-{days} days",),
    ).fetchall()
    total_expense = sum(r["total"] for r in cat_rows)
    categories = [
        {
            "category": r["category"],
            "total": round(r["total"], 2),
            "cnt": r["cnt"],
            "pct": round(r["total"] / total_expense * 100, 1) if total_expense else 0,
        }
        for r in cat_rows
    ]

    income_row = db.execute(
        "SELECT SUM(amount) as total FROM transactions "
        "WHERE amount > 0 AND status = 'Posted' AND is_income = 1 AND date >= date('now', ?)",
        (f"-{days} days",),
    ).fetchone()
    total_income = round(income_row["total"] or 0, 2)

    merchant_rows = db.execute(
        "SELECT merchant_key, SUM(-amount) as total, COUNT(*) as cnt FROM transactions "
        "WHERE amount < 0 AND status = 'Posted' AND merchant_key IS NOT NULL "
        "AND merchant_key != '' AND date >= date('now', ?) "
        "GROUP BY merchant_key ORDER BY total DESC LIMIT 10",
        (f"-{days} days",),
    ).fetchall()
    top_merchants = [
        {"name": rules.display_name(r["merchant_key"]), "total": round(r["total"], 2), "cnt": r["cnt"]}
        for r in merchant_rows
    ]

    month_rows = db.execute(
        "SELECT strftime('%Y-%m', date) as month, SUM(-amount) as total FROM transactions "
        "WHERE amount < 0 AND status = 'Posted' AND date >= date('now', '-180 days') "
        "GROUP BY month ORDER BY month",
    ).fetchall()
    monthly_trend = [{"month": r["month"], "total": round(r["total"], 2)} for r in month_rows]

    return jsonify({
        "days": days,
        "total_income": total_income,
        "total_expense": round(total_expense, 2),
        "net": round(total_income - total_expense, 2),
        "categories": categories,
        "top_merchants": top_merchants,
        "monthly_trend": monthly_trend,
    })


# ---------------------------------------------------------------------------
# Bill Trends (is each recurring bill creeping up month to month?)
# ---------------------------------------------------------------------------

BILL_TREND_ALERT_PCT = 10       # latest month this much above your recent baseline -> flag it
BILL_TREND_VARIABILITY_PCT = 8  # bills that barely move get grouped as "steady" instead of charted


@app.route("/api/bill-trends")
def api_bill_trends():
    db = get_db()
    months_n = request.args.get("months", 6, type=int)

    bills = db.execute(
        "SELECT * FROM bills WHERE active = 1 AND type = 'expense' "
        "AND merchant_key IS NOT NULL AND merchant_key != ''"
    ).fetchall()

    results = []
    for b in bills:
        # Strip any "#1"-style tier suffix (only ever exists in bills.
        # merchant_key, never on a real transaction) and filter to amounts
        # near this bill's own - needed when one merchant bills multiple
        # distinct amounts (e.g. two pet-care plans), so one bill's trend
        # doesn't get blended with the other's.
        base_key = rules.base_merchant_key(b["merchant_key"])
        tolerance = b["amount"] * reconcile.AMOUNT_TOLERANCE
        rows = db.execute(
            # AVG, not SUM: a weekly/biweekly bill has a different number of
            # billing cycles in different calendar months (4 vs 5 Fridays),
            # which would otherwise look like a price change when it isn't.
            "SELECT strftime('%Y-%m', date) as month, AVG(-amount) as total FROM transactions "
            "WHERE merchant_key = ? AND status = 'Posted' AND amount < 0 "
            "AND ABS(-amount - ?) <= ? AND date >= date('now', ?) GROUP BY month ORDER BY month",
            (base_key, b["amount"], tolerance, f"-{months_n} months"),
        ).fetchall()
        if len(rows) < 2:
            continue  # not enough history yet to call it a trend

        months = [{"month": r["month"], "total": round(r["total"], 2)} for r in rows]
        amounts = [r["total"] for r in rows]
        latest = amounts[-1]
        baseline = sum(amounts[:-1]) / len(amounts[:-1])  # average of everything before the latest month
        mean_all = sum(amounts) / len(amounts)

        pct_change = round((latest - baseline) / baseline * 100, 1) if baseline else 0
        variability = round((max(amounts) - min(amounts)) / mean_all * 100, 1) if mean_all else 0

        if pct_change > BILL_TREND_ALERT_PCT:
            direction = "up"
        elif pct_change < -BILL_TREND_ALERT_PCT:
            direction = "down"
        else:
            direction = "flat"

        results.append({
            "bill_id": b["id"],
            "name": b["name"],
            "category": b["category"],
            "months": months,
            "latest_amount": round(latest, 2),
            "baseline_amount": round(baseline, 2),
            "pct_change": pct_change,
            "variability": variability,
            "direction": direction,
            "is_variable": variability >= BILL_TREND_VARIABILITY_PCT,
            "is_alert": pct_change > BILL_TREND_ALERT_PCT,
        })

    results.sort(key=lambda r: (-r["is_alert"], -r["pct_change"]))
    rising = [r for r in results if r["is_alert"]]

    return jsonify({
        "months_requested": months_n,
        "bills": results,
        "rising_count": len(rising),
        "rising_bills": rising,
    })


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

NOTIFICATION_SETTING_KEYS = [
    "notify_days_ahead", "notify_low_balance_threshold", "notify_time",
    "ntfy_enabled", "ntfy_server", "ntfy_topic", "ntfy_priority",
    "sms_gateway_enabled", "sms_gateway_phone", "sms_gateway_carrier",
    "twilio_enabled", "twilio_account_sid", "twilio_auth_token",
    "twilio_from_number", "twilio_to_number",
    "email_enabled", "email_to",
    "smtp_host", "smtp_port", "smtp_user", "smtp_pass",
]


@app.route("/api/notifications/settings", methods=["GET"])
def api_get_notification_settings():
    db = get_db()
    rows = db.execute(
        "SELECT key, value FROM settings WHERE key IN ({})".format(
            ",".join("?" for _ in NOTIFICATION_SETTING_KEYS)
        ),
        NOTIFICATION_SETTING_KEYS,
    ).fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/notifications/settings", methods=["POST"])
def api_set_notification_settings():
    db = get_db()
    body = request.get_json(force=True)
    for k, v in body.items():
        if k in NOTIFICATION_SETTING_KEYS:
            set_setting(db, k, v)
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/notifications/test", methods=["POST"])
def api_test_notification():
    db = get_db()
    body = request.get_json(force=True)
    channel = body.get("channel")
    if channel not in ("ntfy", "sms_gateway", "twilio", "email"):
        return jsonify({"error": "invalid channel"}), 400
    settings = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings").fetchall()}
    try:
        notifications.send_test(channel, settings)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/notifications/run-check", methods=["POST"])
def api_run_notification_check():
    body = request.get_json(force=True, silent=True) or {}
    force = bool(body.get("force"))
    result = notifications.run_daily_check(DB_PATH, force=force)
    return jsonify(result)


@app.route("/api/notifications/history")
def api_notification_history():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM notified_log ORDER BY sent_at DESC LIMIT 20"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Backup / export
# ---------------------------------------------------------------------------

# Order matters for /api/reset - child tables (foreign keys) must be deleted
# before the parent tables they reference (bill_payments/debts -> bills).
ALL_DATA_TABLES = [
    "bill_payments", "debts", "bills", "transactions", "merchant_categories",
    "budgets", "dismissed_suggestions", "dismissed_matches", "category_types",
    "notified_log", "settings",
]


def _ics_escape(text):
    text = str(text)
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def _ics_fold(line):
    """RFC5545 lines longer than 75 octets must be folded onto continuation
    lines starting with a space."""
    if len(line) <= 75:
        return line
    parts = [line[:75]]
    rest = line[75:]
    while rest:
        parts.append(" " + rest[:74])
        rest = rest[74:]
    return "\r\n".join(parts)


@app.route("/api/calendar.ics")
def api_calendar_ics():
    """A standard .ics feed of every active bill's due date, recurring
    forever using the same frequency/due-day logic as the in-app calendar -
    either download it once into any calendar app, or paste this URL in as a
    subscription so it keeps showing future occurrences automatically."""
    db = get_db()
    bills = db.execute("SELECT * FROM bills WHERE active = 1").fetchall()
    now_stamp = datetime.now().strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Budget & Bills//REGTeches//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Bills - Budget & Bills",
        "X-PUBLISHED-TTL:PT12H",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
    ]
    for b in bills:
        due_iso = b["next_due_date"]
        due_compact = due_iso.replace("-", "")
        is_income = b["type"] == "income"
        label = f"{'Income' if is_income else 'Bill'}: {b['name']} (${b['amount']:.2f})"
        lines.append("BEGIN:VEVENT")
        lines.append(f"UID:bill-{b['id']}@budgetbills.local")
        lines.append(f"DTSTAMP:{now_stamp}")
        lines.append(f"DTSTART;VALUE=DATE:{due_compact}")
        lines.append(_ics_fold(f"SUMMARY:{_ics_escape(label)}"))
        desc_parts = []
        if b["payment_method"]:
            desc_parts.append(f"Payment method: {b['payment_method']}")
        if b["pay_url"]:
            desc_parts.append(f"Pay online: {b['pay_url']}")
        if b["notes"]:
            desc_parts.append(b["notes"])
        if desc_parts:
            lines.append(_ics_fold(f"DESCRIPTION:{_ics_escape(chr(10).join(desc_parts))}"))
        freq = b["frequency"]
        due_day = b["due_day"]
        rrule = None
        if freq == "weekly":
            rrule = "FREQ=WEEKLY"
        elif freq == "biweekly":
            rrule = "FREQ=WEEKLY;INTERVAL=2"
        elif freq == "monthly":
            day = due_day or int(due_iso.split("-")[2])
            rrule = f"FREQ=MONTHLY;BYMONTHDAY={day}"
        elif freq == "annual":
            month = int(due_iso.split("-")[1])
            day = due_day or int(due_iso.split("-")[2])
            rrule = f"FREQ=YEARLY;BYMONTH={month};BYMONTHDAY={day}"
        if rrule:
            lines.append(f"RRULE:{rrule}")
        lines.append("BEGIN:VALARM")
        lines.append("ACTION:DISPLAY")
        lines.append("DESCRIPTION:Reminder")
        lines.append("TRIGGER:-P1D")
        lines.append("END:VALARM")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines) + "\r\n"
    return Response(
        body, mimetype="text/calendar",
        headers={"Content-Disposition": "attachment; filename=bills.ics"},
    )


@app.route("/api/export")
def api_export():
    db = get_db()
    dump = {}
    for t in ALL_DATA_TABLES:
        dump[t] = [dict(r) for r in db.execute(f"SELECT * FROM {t}").fetchall()]
    dump["exported_at"] = datetime.now().isoformat()
    body = json.dumps(dump, indent=2)
    filename = f"budget_backup_{today_iso()}.json"
    return Response(
        body, mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/api/reset", methods=["POST"])
def api_reset_data():
    """Wipes every table - transactions, bills, budgets, debts, settings,
    everything. Irreversible (aside from restoring a Download Backup file by
    hand). Requires an explicit confirmation string so it can't be triggered
    by an accidental or stray request."""
    body = request.get_json(force=True, silent=True) or {}
    if body.get("confirm") != "RESET":
        return jsonify({"error": "confirmation required"}), 400
    db = get_db()
    for t in ALL_DATA_TABLES:
        db.execute(f"DELETE FROM {t}")
    db.commit()
    return jsonify({"ok": True})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.route("/api/settings", methods=["GET"])
def api_get_settings():
    db = get_db()
    rows = db.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/settings", methods=["POST"])
def api_set_settings():
    db = get_db()
    body = request.get_json(force=True)
    for k, v in body.items():
        set_setting(db, k, v)
    db.commit()
    return jsonify({"ok": True})


def scheduler_loop():
    """Runs the daily notification check while this process is alive, at the
    time configured in Settings > Notifications (default 08:00). This only
    fires while the app is running - for reminders that work even when the
    app/browser is closed, use schedule_reminders.bat (Windows Task Scheduler)."""
    last_run_date = None
    while True:
        try:
            now = datetime.now()
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT value FROM settings WHERE key = 'notify_time'").fetchone()
            conn.close()
            target = row["value"] if row and row["value"] else "08:00"
            hh, mm = (int(x) for x in target.split(":"))
            if now.hour == hh and now.minute == mm and last_run_date != now.date():
                notifications.run_daily_check(DB_PATH)
                last_run_date = now.date()
        except Exception:
            pass
        time.sleep(30)


def _find_open_port(host, preferred, tries=20):
    """Returns preferred if free, otherwise the next free port after it -
    so a second copy of this app (or anything else already using the
    port) doesn't stop this one from starting.

    Checks by attempting to connect rather than bind: Windows lets a second
    process bind the same port Werkzeug is already listening on (it sets
    SO_REUSEADDR), so a bind-based check would wrongly report a busy port
    as free and both copies would silently share one port."""
    check_host = "127.0.0.1" if host == "0.0.0.0" else host
    for port in range(preferred, preferred + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.25)
            if s.connect_ex((check_host, port)) != 0:
                return port  # nothing answered - free
    return preferred  # give up trying to dodge it; let Flask raise the real error


if __name__ == "__main__":
    init_db()
    threading.Thread(target=scheduler_loop, daemon=True).start()
    # Defaults to localhost-only. Set BUDGET_APP_HOST=0.0.0.0 (see
    # start_budget_lan.bat) to make this reachable from your phone on the
    # same WiFi network - only do that on a network you trust, since it has
    # no login and anyone on that network could open your financial data.
    host = os.environ.get("BUDGET_APP_HOST", "127.0.0.1")
    preferred_port = int(os.environ.get("BUDGET_APP_PORT", "5000"))
    port = _find_open_port(host, preferred_port)
    if port != preferred_port:
        print(f"Port {preferred_port} is already in use on this PC - starting on {port} instead.")
    print(f"Budget & Bills: http://127.0.0.1:{port}/")
    if not os.path.exists("/.dockerenv"):
        # Skip trying to launch a local browser when running headless in a
        # container/server - there's nothing to open and no display for it.
        threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
    app.run(host=host, debug=False, port=port)
