"""Bill occurrence generation and balance projection."""
import calendar
from datetime import datetime, timedelta

ISO = "%Y-%m-%d"


def parse(d):
    return datetime.strptime(d, ISO)


def fmt(d):
    return d.strftime(ISO)


def clamp_day(year, month, day):
    last = calendar.monthrange(year, month)[1]
    return min(day, last)


def advance(date, frequency, due_day):
    if frequency == "weekly":
        return date + timedelta(days=7)
    if frequency == "biweekly":
        return date + timedelta(days=14)
    if frequency == "monthly":
        y, m = date.year, date.month
        m += 1
        if m > 12:
            m = 1
            y += 1
        day = due_day or date.day
        return datetime(y, m, clamp_day(y, m, day))
    if frequency == "annual":
        y = date.year + 1
        day = due_day or date.day
        month = date.month
        try:
            return datetime(y, month, day)
        except ValueError:
            return datetime(y, month, clamp_day(y, month, day))
    return None  # 'once' has no further occurrences


def occurrences_between(bill, start, end, max_iter=500):
    """bill: dict with next_due_date, frequency, due_day. Returns list of datetime."""
    results = []
    if not bill.get("active", 1):
        return results
    d = parse(bill["next_due_date"])
    frequency = bill["frequency"]
    due_day = bill.get("due_day")

    if frequency == "once":
        if start <= d <= end:
            results.append(d)
        return results

    i = 0
    while d <= end and i < max_iter:
        if d >= start:
            results.append(d)
        nxt = advance(d, frequency, due_day)
        if nxt is None or nxt <= d:
            break
        d = nxt
        i += 1
    return results


def project_balances(bills, start_date, end_date, starting_balance, as_of_date):
    """
    Returns dict of ISO date -> {"balance": float, "events": [{"name","amount","type"}]}
    for every day from start_date to end_date. Only occurrences on/after as_of_date affect
    the running balance; days before as_of_date just show scheduled events (no balance).
    """
    day_events = {}
    d = start_date
    while d <= end_date:
        day_events[fmt(d)] = []
        d += timedelta(days=1)

    for bill in bills:
        occs = occurrences_between(bill, start_date, end_date)
        for occ in occs:
            key = fmt(occ)
            if key in day_events:
                day_events[key].append({
                    "bill_id": bill["id"],
                    "name": bill["name"],
                    "amount": bill["amount"],
                    "type": bill["type"],
                    "category": bill.get("category"),
                    "autopay": bool(bill.get("autopay")),
                    "pay_url": bill.get("pay_url") or "",
                    "payment_method": bill.get("payment_method") or "",
                    "logo_domain": bill.get("logo_domain") or "",
                })

    result = {}
    running = starting_balance
    d = start_date
    while d <= end_date:
        key = fmt(d)
        events = day_events[key]
        if d >= as_of_date:
            for e in events:
                running += e["amount"] if e["type"] == "income" else -abs(e["amount"])
            balance = running
        else:
            balance = None
        result[key] = {"balance": balance, "events": events}
        d += timedelta(days=1)
    return result
