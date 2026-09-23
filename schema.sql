CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    description TEXT NOT NULL,
    amount REAL NOT NULL,
    check_num TEXT,
    status TEXT,
    category TEXT DEFAULT 'Uncategorized',
    merchant_key TEXT,
    is_income INTEGER DEFAULT 0,
    hash TEXT UNIQUE NOT NULL,
    imported_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    amount REAL NOT NULL,
    category TEXT DEFAULT 'Bill',
    type TEXT DEFAULT 'expense',
    frequency TEXT DEFAULT 'monthly',
    due_day INTEGER,
    next_due_date TEXT NOT NULL,
    autopay INTEGER DEFAULT 0,
    account TEXT DEFAULT 'Checking',
    notes TEXT,
    active INTEGER DEFAULT 1,
    source TEXT DEFAULT 'manual',
    merchant_key TEXT,
    pay_url TEXT,
    payment_method TEXT,
    logo_domain TEXT
);

CREATE TABLE IF NOT EXISTS bill_payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(id),
    due_date TEXT NOT NULL,
    paid_date TEXT,
    amount REAL,
    status TEXT DEFAULT 'paid',
    matched_transaction_id INTEGER,
    prior_next_due_date TEXT,
    prior_amount REAL,
    auto_matched INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS merchant_categories (
    merchant_key TEXT PRIMARY KEY,
    category TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS dismissed_suggestions (
    merchant_key TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    monthly_limit REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS notified_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    channel TEXT NOT NULL,
    message TEXT,
    sent_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dismissed_matches (
    bill_id INTEGER NOT NULL,
    transaction_id INTEGER NOT NULL,
    PRIMARY KEY (bill_id, transaction_id)
);

CREATE TABLE IF NOT EXISTS category_types (
    category TEXT PRIMARY KEY,
    kind TEXT NOT NULL -- 'essential' or 'discretionary'
);

CREATE TABLE IF NOT EXISTS debts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    balance REAL NOT NULL,
    apr REAL NOT NULL DEFAULT 0,
    minimum_payment REAL NOT NULL,
    linked_bill_id INTEGER REFERENCES bills(id),
    notes TEXT,
    active INTEGER DEFAULT 1
);
