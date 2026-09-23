"""Generates fictional sample data so this demo copy shows off every feature
without containing anyone's real financial information.

Run this any time to regenerate fresh sample data (dates are always relative
to today, so it never looks stale):

    python seed_demo_data.py

This OVERWRITES data/budget.db. If you want to start completely empty
instead of with sample data, use Settings > Clear All Data in the app after
running this once, or just delete data/budget.db and let the app recreate an
empty one on next start.
"""
import csv
import io
import os
import random
import shutil
import sqlite3
from datetime import datetime, timedelta

import app as A
import reconcile
import rules

random.seed(42)  # stable/reproducible demo data across regenerations

TODAY = datetime.now()
START = TODAY - timedelta(days=90)


def daterange_days():
    d = START
    while d <= TODAY:
        yield d
        d += timedelta(days=1)


# --- Recurring bills: tracked (get a Bills row) -----------------------------------
# (description, amount, category, type, frequency, freq_days, variance_pct)
TRACKED_RECURRING = [
    ("ACME CORP PAYROLL", 2850.00, "Income", "income", "biweekly", 14, 0.01),
    ("SUNSHINE MORTGAGE CO LOAN PAYMT", -1650.00, "Housing", "expense", "monthly", 30, 0.0),
    ("SUNCOAST AUTO FINANCE PAYMT", -385.00, "Loan Payment", "expense", "monthly", 30, 0.0),
    ("GEICO INSURANCE PREM", -145.00, "Insurance", "expense", "monthly", 30, 0.03),
    ("DUKE ENERGY", -135.00, "Utilities", "expense", "monthly", 30, 0.35),
    ("COMCAST / XFINITY", -95.00, "Utilities", "expense", "monthly", 30, 0.0),
    ("VERIZON WIRELESS", -85.00, "Phone", "expense", "monthly", 30, 0.02),
    ("AMAZON PRIME", -14.99, "Subscriptions", "expense", "monthly", 30, 0.0),
    ("GOOGLE *GOOGLE ONE", -2.99, "Subscriptions", "expense", "monthly", 30, 0.0),
    ("PLANET FITNESS", -24.99, "Health", "expense", "monthly", 30, 0.0),
    ("BANFIELD PET HOSPITAL", -45.00, "Pet Care", "expense", "monthly", 30, 0.0),
]

# --- Recurring but deliberately NOT added as a bill yet ----------------------------
# so the demo shows "Detected Recurring Charges" with one-click Add as Bill.
DETECTED_ONLY_RECURRING = [
    ("NETFLIX.COM", -15.49, "monthly", 30, 0.0),
    ("SPOTIFY USA", -11.99, "monthly", 30, 0.0),
    ("HELLOFRESH", -68.00, "weekly", 7, 0.10),
]

# --- Irregular spending (varies week to week, not a "bill") ------------------------
IRREGULAR = [
    ("DD *DOORDASH {n}", (-45, -15), 2.0),
    ("KROGER #{n}", (-110, -45), 1.4),
    ("SHELL OIL {n}", (-55, -32), 1.0),
    ("STARBUCKS STORE {n}", (-8, -4), 1.6),
    ("AMAZON MKTPL*{code}", (-95, -14), 1.1),
    ("SUNPASS TOLL AUTHORITY", (-2.50, -2.50), 3.0),
]

ONE_OFFS = [
    ("BEST BUY 00123", -249.99, 62, ""),
    ("CHECK", -100.00, 21, "101"),
    ("CHECK", -60.00, 5, "102"),
    ("PURCHASE RETURN AMAZON.COM", 22.50, 40, ""),
]


def gen_recurring_rows(desc, amount, freq_days, variance):
    rows = []
    d = START
    while d <= TODAY:
        amt = amount
        if variance:
            amt = round(amount * (1 + random.uniform(-variance, variance)), 2)
        rows.append({"date": d, "description": desc, "amount": amt, "check_num": "", "status": "Posted"})
        d += timedelta(days=freq_days + random.choice([-1, 0, 0, 1]))
    return rows


def gen_irregular_rows(pattern, amount_range, per_week):
    rows = []
    d = START
    while d <= TODAY:
        if random.random() < per_week / 7.0:
            amt = round(random.uniform(*amount_range), 2)
            desc = pattern.format(n=random.randint(100, 999), code=f"{random.randint(10,99)}{chr(random.randint(65,90))}{random.randint(100,999)}")
            rows.append({"date": d, "description": desc, "amount": amt, "check_num": "", "status": "Posted"})
        d += timedelta(days=1)
    return rows


def build_transactions():
    rows = []
    for desc, amount, _cat, _type, _freq, freq_days, variance in TRACKED_RECURRING:
        rows.extend(gen_recurring_rows(desc, amount, freq_days, variance))
    for desc, amount, _freq, freq_days, variance in DETECTED_ONLY_RECURRING:
        rows.extend(gen_recurring_rows(desc, amount, freq_days, variance))
    for pattern, amount_range, per_week in IRREGULAR:
        rows.extend(gen_irregular_rows(pattern, amount_range, per_week))
    for desc, amount, days_ago, check_num in ONE_OFFS:
        rows.append({
            "date": TODAY - timedelta(days=days_ago), "description": desc,
            "amount": amount, "check_num": check_num, "status": "Posted",
        })
    # Last couple of days show as Pending, like a real bank feed
    for r in rows:
        if (TODAY - r["date"]).days <= 1:
            r["status"] = "Pending"
    rows.sort(key=lambda r: r["date"], reverse=True)
    return rows


def write_sample_csv(rows, path):
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_ALL)
    writer.writerow(["DATE", "DESCRIPTION", "AMOUNT", "CHECK #", "STATUS"])
    for r in rows:
        writer.writerow([r["date"].strftime("%m/%d/%Y"), r["description"], f"{r['amount']:.2f}", r["check_num"], r["status"]])
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())


def import_transactions(conn, rows):
    overrides = {}
    seq_counts = {}
    now = datetime.now().isoformat()
    added = 0
    for r in rows:
        date_iso = r["date"].strftime("%Y-%m-%d")
        base_key = (date_iso, r["description"].strip(), r["amount"], r["check_num"])
        seq = seq_counts.get(base_key, 0)
        seq_counts[base_key] = seq + 1
        h = A.make_hash({"date": date_iso, "description": r["description"], "amount": r["amount"], "check_num": r["check_num"]}, seq)
        merchant_key = rules.normalize_merchant(r["description"])
        category, is_income = rules.categorize(r["description"], overrides)
        conn.execute(
            "INSERT OR IGNORE INTO transactions (date, description, amount, check_num, status, "
            "category, merchant_key, is_income, hash, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (date_iso, r["description"], r["amount"], r["check_num"], r["status"],
             category, merchant_key, int(is_income), h, now),
        )
        added += 1
    conn.commit()
    return added


def next_due_after(merchant_key, freq_days, conn):
    """Next occurrence date, computed from this merchant's own actual last
    transaction date + its frequency - so bills land on staggered, believable
    dates instead of all bunching on the same day."""
    row = conn.execute(
        "SELECT MAX(date) as last_date FROM transactions WHERE merchant_key = ?",
        (merchant_key,),
    ).fetchone()
    last_date = datetime.strptime(row["last_date"], "%Y-%m-%d") if row and row["last_date"] else TODAY
    due = last_date + timedelta(days=freq_days)
    while due <= TODAY:
        due += timedelta(days=freq_days)
    return due


def insert_bills(conn):
    for desc, amount, category, type_, frequency, freq_days, _variance in TRACKED_RECURRING:
        merchant_key = rules.normalize_merchant(desc)
        due = next_due_after(merchant_key, freq_days, conn)
        due_day = due.day if frequency in ("monthly", "annual") else None
        conn.execute(
            "INSERT INTO bills (name, amount, category, type, frequency, due_day, "
            "next_due_date, autopay, account, notes, active, source, merchant_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 'Checking', '', 1, 'detected', ?)",
            (rules.display_name(merchant_key), abs(amount), category, type_, frequency,
             due_day, due.strftime("%Y-%m-%d"), merchant_key),
        )
    conn.commit()


def insert_budgets(conn):
    for category, limit in [("Groceries", 450.0), ("Dining / Delivery", 150.0), ("Shopping", 200.0)]:
        conn.execute(
            "INSERT INTO budgets (category, monthly_limit) VALUES (?, ?) "
            "ON CONFLICT(category) DO UPDATE SET monthly_limit = excluded.monthly_limit",
            (category, limit),
        )
    conn.commit()


def insert_debts(conn):
    conn.execute(
        "INSERT INTO debts (name, balance, apr, minimum_payment, notes, active) VALUES (?, ?, ?, ?, ?, 1)",
        ("Sunshine Mortgage", 310000.0, 6.25, 1650.0, "Sample 30-year mortgage"),
    )
    conn.execute(
        "INSERT INTO debts (name, balance, apr, minimum_payment, notes, active) VALUES (?, ?, ?, ?, ?, 1)",
        ("Suncoast Auto Finance", 9500.0, 7.9, 385.0, "Sample auto loan"),
    )
    conn.commit()


def insert_settings(conn):
    settings = {
        "current_balance": "1850.00",
        "current_balance_date": TODAY.strftime("%Y-%m-%d"),
        "notify_days_ahead": "3",
        "notify_low_balance_threshold": "200",
        "notify_time": "08:00",
    }
    for k, v in settings.items():
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (k, v),
        )
    conn.commit()


def main():
    if os.path.exists(A.DB_PATH):
        os.remove(A.DB_PATH)
    A.init_db()

    conn = sqlite3.connect(A.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    rows = build_transactions()
    csv_path = os.path.join(A.BASE_DIR, "sample_checking.csv")
    write_sample_csv(rows, csv_path)
    added = import_transactions(conn, rows)

    insert_bills(conn)
    A._backfill_logo_domains(conn)  # insert_bills does raw SQL, so run the guesser explicitly
    reconciled = reconcile.reconcile_bills(conn)
    conn.commit()

    insert_budgets(conn)
    insert_debts(conn)
    insert_settings(conn)
    conn.close()

    # Keep the bundled seed copy (used by the .exe build and first-run
    # auto-seed) in sync with the freshly generated sample data.
    seed_path = os.path.join(A.BASE_DIR, "seed_budget.db")
    shutil.copy(A.DB_PATH, seed_path)

    print(f"Generated {added} sample transactions -> {csv_path}")
    print(f"Tracked bills: {len(TRACKED_RECURRING)}")
    print(f"Auto-reconciled {len(reconciled)} bill occurrences against sample history")
    print(f"Detected-only recurring (for the 'Add as Bill' demo): {len(DETECTED_ONLY_RECURRING)}")
    print(f"Updated bundled seed copy -> {seed_path}")
    print("Done. Sample data is in data/budget.db")


if __name__ == "__main__":
    main()
