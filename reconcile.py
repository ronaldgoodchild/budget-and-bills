"""Match imported transactions to tracked bills and auto mark-paid.

A bill created from a detected recurring charge carries a merchant_key. When
new (or previously-pending-now-posted) transactions come in, we look for a
posted transaction from that same merchant, with the right sign, an amount
within tolerance of the bill's amount, dated near the bill's next_due_date.

Two tiers:
  - reconcile_bills(): exact merchant_key match -> auto-applied (safe, since
    the merchant string matched exactly).
  - suggest_reconciliations(): real bank statement descriptors sometimes drift
    month to month for the same merchant (e.g. a subscription's processor
    changes the descriptor, or a loan gets paid via a phone/IVR channel one
    month instead of the usual card charge). These are looser first-token
    matches that are NOT auto-applied - they're surfaced for one-click human
    confirmation via apply_match(), since blindly fuzzy-matching risks
    cross-matching similarly-priced bills from different merchants.
"""
from datetime import timedelta

import rules
import scheduling

AMOUNT_TOLERANCE = 0.40  # matches the tolerance used for recurring detection
DATE_WINDOW_DAYS = 6
SUGGESTION_DATE_WINDOW_DAYS = 10  # looser since a human confirms these
AMOUNT_CHANGE_ALERT_THRESHOLD = 0.10
MIN_TOKEN_LEN = 5  # avoid overly generic fallback prefixes


def _already_matched_ids(db):
    return set(
        r["matched_transaction_id"] for r in db.execute(
            "SELECT matched_transaction_id FROM bill_payments WHERE matched_transaction_id IS NOT NULL"
        ).fetchall()
    )


def _within_tolerance(bill_amount, txn_amount):
    if bill_amount <= 0:
        return False
    return abs(abs(txn_amount) - bill_amount) / bill_amount <= AMOUNT_TOLERANCE


def apply_match(db, bill, txn):
    """Marks `bill` paid using `txn` as the matching transaction. Returns the result dict."""
    prior_next_due = bill["next_due_date"]
    prior_amount = bill["amount"]
    new_amount = round(abs(txn["amount"]), 2)

    cur = db.execute(
        "INSERT INTO bill_payments (bill_id, due_date, paid_date, amount, status, "
        "matched_transaction_id, prior_next_due_date, prior_amount, auto_matched) "
        "VALUES (?, ?, ?, ?, 'paid', ?, ?, ?, 1)",
        (bill["id"], prior_next_due, txn["date"], new_amount, txn["id"], prior_next_due, prior_amount),
    )
    payment_id = cur.lastrowid

    nxt = scheduling.advance(scheduling.parse(prior_next_due), bill["frequency"], bill["due_day"])
    if nxt is None:
        db.execute("UPDATE bills SET active = 0, amount = ? WHERE id = ?", (new_amount, bill["id"]))
    else:
        db.execute(
            "UPDATE bills SET next_due_date = ?, amount = ? WHERE id = ?",
            (scheduling.fmt(nxt), new_amount, bill["id"]),
        )

    amount_changed = bool(prior_amount) and abs(new_amount - prior_amount) / prior_amount > AMOUNT_CHANGE_ALERT_THRESHOLD

    return {
        "payment_id": payment_id,
        "bill_id": bill["id"],
        "bill_name": bill["name"],
        "matched_date": txn["date"],
        "matched_description": txn["description"],
        "old_amount": prior_amount,
        "new_amount": new_amount,
        "amount_changed": amount_changed,
    }


def _candidates_for_bill(db, bill, already_matched, merchant_clause_sql, merchant_param, window_days=DATE_WINDOW_DAYS):
    due = scheduling.parse(bill["next_due_date"])
    window_start = scheduling.fmt(due - timedelta(days=window_days))
    window_end = scheduling.fmt(due + timedelta(days=window_days))
    sign_clause = "amount < 0" if bill["type"] == "expense" else "amount > 0"

    rows = db.execute(
        f"SELECT * FROM transactions WHERE {merchant_clause_sql} AND status = 'Posted' "
        f"AND date >= ? AND date <= ? AND {sign_clause} ORDER BY date",
        (merchant_param, window_start, window_end),
    ).fetchall()

    good = []
    for t in rows:
        if t["id"] in already_matched:
            continue
        if not _within_tolerance(bill["amount"], t["amount"]):
            continue
        good.append(t)
    if not good:
        return None

    def score(t):
        day_gap = abs((scheduling.parse(t["date"]) - due).days)
        amount_gap = abs(abs(t["amount"]) - bill["amount"])
        return (day_gap, amount_gap)

    return min(good, key=score)


def reconcile_bills(db):
    """Tier 1: exact merchant_key match, auto-applied."""
    results = []
    bills = db.execute(
        "SELECT * FROM bills WHERE active = 1 AND merchant_key IS NOT NULL AND merchant_key != ''"
    ).fetchall()
    already_matched = _already_matched_ids(db)

    for row in bills:
        bill = dict(row)
        # Bills whose merchant shares a name with another amount tier (e.g. two
        # pet-care plans from the same provider) carry a "#1"-style suffix that
        # only ever exists in bills.merchant_key - real transactions never have
        # it, so we must strip it before querying the transactions table. The
        # amount-tolerance check in _candidates_for_bill then picks out the
        # right tier.
        base_key = rules.base_merchant_key(bill["merchant_key"])
        best = _candidates_for_bill(db, bill, already_matched, "merchant_key = ?", base_key)
        if best is None:
            continue
        results.append(apply_match(db, bill, best))
        already_matched.add(best["id"])

    return results


def suggest_reconciliations(db):
    """Tier 2: loose first-token match for bills that found no exact match.
    Not applied automatically - returned for the user to confirm or dismiss."""
    suggestions = []
    bills = db.execute(
        "SELECT * FROM bills WHERE active = 1 AND merchant_key IS NOT NULL AND merchant_key != ''"
    ).fetchall()
    already_matched = _already_matched_ids(db)
    dismissed = set(
        (r["bill_id"], r["transaction_id"]) for r in db.execute("SELECT bill_id, transaction_id FROM dismissed_matches").fetchall()
    )

    # First: which bills already have an exact-match candidate right now? Skip those
    # (they'll be caught by reconcile_bills - no need to also suggest for them).
    exactly_matchable_bill_ids = set()
    for row in bills:
        bill = dict(row)
        base_key = rules.base_merchant_key(bill["merchant_key"])
        if _candidates_for_bill(db, bill, already_matched, "merchant_key = ?", base_key):
            exactly_matchable_bill_ids.add(bill["id"])

    for row in bills:
        bill = dict(row)
        base_key = rules.base_merchant_key(bill["merchant_key"])
        if bill["id"] in exactly_matchable_bill_ids:
            continue
        token = base_key.split(" ")[0]
        if len(token) < MIN_TOKEN_LEN:
            continue
        best = _candidates_for_bill(
            db, bill, already_matched, "merchant_key LIKE ?", token + "%",
            window_days=SUGGESTION_DATE_WINDOW_DAYS,
        )
        if best is None or best["merchant_key"] == base_key:
            continue
        if (bill["id"], best["id"]) in dismissed:
            continue
        suggestions.append({
            "bill_id": bill["id"],
            "bill_name": bill["name"],
            "bill_amount": bill["amount"],
            "bill_due_date": bill["next_due_date"],
            "transaction_id": best["id"],
            "transaction_date": best["date"],
            "transaction_description": best["description"],
            "transaction_amount": best["amount"],
        })
    return suggestions
