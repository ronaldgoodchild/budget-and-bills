"""Notification senders (ntfy, email-to-SMS carrier gateway, Twilio SMS, email)
and the daily "what's coming up" digest. Stdlib only - no extra dependencies.

All credentials live in the local settings table (plaintext, local SQLite file)
and are never sent anywhere except directly to the service you configured.
"""
import base64
import smtplib
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from email.mime.text import MIMEText

import scheduling

CARRIER_GATEWAYS = {
    "att": "txt.att.net",
    "verizon": "vtext.com",
    "tmobile": "tmomail.net",
    "sprint": "messaging.sprintpcs.com",
    "boost": "sms.myboostmobile.com",
    "cricket": "sms.cricketwireless.net",
    "metropcs": "mymetropcs.com",
    "uscellular": "email.uscc.net",
    "googlefi": "msg.fi.google.com",
    "mint": "mailmymobile.net",
    "visible": "vtext.com",
    "straighttalk": "vtext.com",
    "republicwireless": "text.republicwireless.com",
    "ting": "message.ting.com",
    "xfinitymobile": "vtext.com",
}


def today_iso():
    return datetime.now().strftime("%Y-%m-%d")


# --- Senders -----------------------------------------------------------------

def send_ntfy(server, topic, title, message, priority="default"):
    if not server or not topic:
        raise ValueError("ntfy server/topic not configured")
    url = f"{server.rstrip('/')}/{urllib.parse.quote(topic)}"
    req = urllib.request.Request(url, data=message.encode("utf-8"), method="POST")
    req.add_header("Title", title.encode("ascii", "replace").decode("ascii"))
    req.add_header("Priority", priority)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status


def send_email(smtp_host, smtp_port, smtp_user, smtp_pass, to_addr, subject, body):
    if not all([smtp_host, smtp_port, smtp_user, smtp_pass, to_addr]):
        raise ValueError("SMTP settings incomplete")
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_addr
    with smtplib.SMTP(smtp_host, int(smtp_port), timeout=15) as server:
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_user, [to_addr], msg.as_string())


def send_sms_gateway(smtp_host, smtp_port, smtp_user, smtp_pass, phone, carrier, body):
    domain = CARRIER_GATEWAYS.get(carrier)
    if not domain:
        raise ValueError(f"Unknown carrier: {carrier}")
    digits = "".join(ch for ch in phone if ch.isdigit())
    if not digits:
        raise ValueError("Phone number not configured")
    to_addr = f"{digits}@{domain}"
    send_email(smtp_host, smtp_port, smtp_user, smtp_pass, to_addr, "", body)


def send_twilio_sms(account_sid, auth_token, from_number, to_number, body):
    if not all([account_sid, auth_token, from_number, to_number]):
        raise ValueError("Twilio settings incomplete")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = urllib.parse.urlencode({"From": from_number, "To": to_number, "Body": body}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    creds = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    req.add_header("Authorization", f"Basic {creds}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Twilio error {e.code}: {detail}") from e


def _truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def dispatch(channel, settings, title, message):
    if channel == "ntfy":
        send_ntfy(
            settings.get("ntfy_server", "https://ntfy.sh"),
            settings.get("ntfy_topic", ""),
            title, message,
            priority=settings.get("ntfy_priority", "default"),
        )
    elif channel == "sms_gateway":
        send_sms_gateway(
            settings.get("smtp_host"), settings.get("smtp_port"),
            settings.get("smtp_user"), settings.get("smtp_pass"),
            settings.get("sms_gateway_phone"), settings.get("sms_gateway_carrier"),
            f"{title}\n{message}",
        )
    elif channel == "twilio":
        send_twilio_sms(
            settings.get("twilio_account_sid"), settings.get("twilio_auth_token"),
            settings.get("twilio_from_number"), settings.get("twilio_to_number"),
            f"{title}\n{message}",
        )
    elif channel == "email":
        send_email(
            settings.get("smtp_host"), settings.get("smtp_port"),
            settings.get("smtp_user"), settings.get("smtp_pass"),
            settings.get("email_to"), title, message,
        )
    else:
        raise ValueError(f"Unknown channel: {channel}")


def enabled_channels(settings):
    channels = []
    if _truthy(settings.get("ntfy_enabled")):
        channels.append("ntfy")
    if _truthy(settings.get("sms_gateway_enabled")):
        channels.append("sms_gateway")
    if _truthy(settings.get("twilio_enabled")):
        channels.append("twilio")
    if _truthy(settings.get("email_enabled")):
        channels.append("email")
    return channels


# --- Digest builder ------------------------------------------------------------

def build_digest(db, days_ahead, low_balance_threshold):
    """Returns (title, message) or None if there's nothing worth reporting."""
    bills = [dict(r) for r in db.execute("SELECT * FROM bills WHERE active = 1").fetchall()]
    row = db.execute("SELECT value FROM settings WHERE key = 'current_balance'").fetchone()
    balance = float(row["value"]) if row and row["value"] else 0.0
    row = db.execute("SELECT value FROM settings WHERE key = 'current_balance_date'").fetchone()
    as_of = row["value"] if row and row["value"] else today_iso()
    as_of_date = scheduling.parse(as_of)

    horizon_end = as_of_date + timedelta(days=max(days_ahead, 14))
    days = scheduling.project_balances(bills, as_of_date, horizon_end, balance, as_of_date)

    overdue, due_today, due_soon = [], [], []
    for date_str in sorted(days.keys()):
        d = scheduling.parse(date_str)
        delta = (d - as_of_date).days
        for e in days[date_str]["events"]:
            if e["type"] != "expense":
                continue
            if delta < 0:
                overdue.append((date_str, e))
            elif delta == 0:
                due_today.append((date_str, e))
            elif delta <= days_ahead:
                due_soon.append((date_str, e))

    lowest_balance, lowest_date = None, None
    for date_str in sorted(days.keys()):
        bal = days[date_str]["balance"]
        if bal is not None and (lowest_balance is None or bal < lowest_balance):
            lowest_balance, lowest_date = bal, date_str
    low_warning = lowest_balance is not None and lowest_balance < low_balance_threshold

    if not (overdue or due_today or due_soon or low_warning):
        return None

    lines = []
    if overdue:
        lines.append("OVERDUE (past due date, not marked paid):")
        for date_str, e in overdue:
            lines.append(f"  - {date_str}: {e['name']} ${abs(e['amount']):.2f}")
    if due_today:
        lines.append("Due TODAY:")
        for date_str, e in due_today:
            lines.append(f"  - {e['name']} ${abs(e['amount']):.2f}")
    if due_soon:
        lines.append(f"Due in the next {days_ahead} days:")
        for date_str, e in due_soon:
            lines.append(f"  - {date_str}: {e['name']} ${abs(e['amount']):.2f}")
    if low_warning:
        lines.append(f"Heads up: projected balance dips to ${lowest_balance:.2f} around {lowest_date}.")

    title = "Budget: bills coming up"
    if overdue:
        title = "Budget: overdue bill!"
    elif low_warning:
        title = "Budget: balance running low"
    message = "\n".join(lines)
    return title, message


def send_test(channel, settings):
    title = "Budget & Bills - test"
    message = "This is a test notification from your local Budget & Bills app. If you got this, it's working!"
    dispatch(channel, settings, title, message)


def run_daily_check(db_path, force=False):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    settings = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM settings")}
    days_ahead = int(settings.get("notify_days_ahead", 3) or 3)
    low_threshold = float(settings.get("notify_low_balance_threshold", 200) or 200)

    result = build_digest(conn, days_ahead, low_threshold)
    if result is None:
        conn.close()
        return {"sent": False, "reason": "nothing due or low right now"}

    title, message = result
    today = today_iso()
    channels = enabled_channels(settings)
    summary = []
    for channel in channels:
        if not force:
            already = conn.execute(
                "SELECT 1 FROM notified_log WHERE run_date = ? AND channel = ?", (today, channel)
            ).fetchone()
            if already:
                summary.append({"channel": channel, "status": "skipped (already sent today)"})
                continue
        try:
            dispatch(channel, settings, title, message)
            conn.execute(
                "INSERT INTO notified_log (run_date, channel, message, sent_at) VALUES (?, ?, ?, ?)",
                (today, channel, message, datetime.now().isoformat()),
            )
            conn.commit()
            summary.append({"channel": channel, "status": "sent"})
        except Exception as e:
            summary.append({"channel": channel, "status": f"error: {e}"})
    conn.close()
    return {"sent": True, "title": title, "message": message, "channels": summary}
