"""Merchant normalization, auto-categorization, and recurring-charge detection."""
import re
from collections import defaultdict
from datetime import datetime, timedelta

# --- Merchant name normalization -------------------------------------------------

_STRIP_PREFIXES = [
    r"^INTERNATIONAL PURCHASE TRANSACTION FEE$",
    r"^RECURRING PAYMENT\s+",
    r"^PURCHASE RETURN\s+",
    r"^PURCHASE INTL\s+",
    r"^PURCHASE\s+",
]
_AUTH_ON_RE = re.compile(r"AUTHORIZED ON\s+\d{2}/\d{2}\s*")
_CARD_RE = re.compile(r"CARD\s*\d{3,4}\s*$")
_REF_RE = re.compile(r"\b[SP]\d{12,}\b")
_STATE_CITY_RE = re.compile(r"\b[A-Z]{2}\b(?=\s|$)")
_PHONE_RE = re.compile(r"\b\d{3}-\d{3}-\d{4}\b|\b8\d{2}-\d{3}-\d{4}\b")
_MULTISPACE_RE = re.compile(r"\s{2,}")
_TRAILING_NUM_RE = re.compile(r"\s+#?\d{3,}\s*$")
# ACH-style entries (payroll, loan, insurance premium, billpay) look like:
# "NAME          TYPE       YYMMDD refnum...  Person Name" - the date code and
# reference number change every occurrence, so cut everything from that point on.
_ACH_DATE_REF_RE = re.compile(r"\s+\d{6}\s+.*$")


def normalize_merchant(description: str) -> str:
    """Collapse a raw bank description down to a stable merchant key for grouping."""
    d = description.strip()
    if d.upper() == "CHECK":
        return "CHECK"
    d = _ACH_DATE_REF_RE.sub("", d)
    for pat in _STRIP_PREFIXES:
        d = re.sub(pat, "", d, flags=re.IGNORECASE)
    d = _AUTH_ON_RE.sub("", d)
    d = _PHONE_RE.sub("", d)
    d = _REF_RE.sub("", d)
    d = _CARD_RE.sub("", d)
    d = _TRAILING_NUM_RE.sub("", d)
    d = _MULTISPACE_RE.sub(" ", d).strip()
    # Take the first meaningful chunk (merchant name), drop city/state tail noise
    # by cutting at the first run of 2+ spaces already handled above; just cap length.
    key = d.upper()
    key = re.sub(r"[^A-Z0-9 *./]", "", key)
    key = _MULTISPACE_RE.sub(" ", key).strip()
    # Cap to first ~30 chars to keep grouping stable across minor suffix variance
    return key[:30].strip()


def display_name(merchant_key: str) -> str:
    words = merchant_key.title()
    return words


_DEDUP_SUFFIX_RE = re.compile(r"#\d+$")


def base_merchant_key(merchant_key: str) -> str:
    """Strips the '#1', '#2', ... suffix used to disambiguate a merchant that
    bills multiple distinct amounts (see rules.detect_recurring). Real bank
    transactions never carry this suffix - it only ever appears in bills.
    merchant_key - so any query against the transactions table needs the
    base key, with amount-based filtering to pick out the right tier."""
    return _DEDUP_SUFFIX_RE.sub("", merchant_key)


# --- Logo domain guessing (eye candy only - purely cosmetic) ----------------------

# Ordered (regex, domain). First match on the bill's name/description wins.
# Used to auto-fill a "logo_domain" so a bill shows the company's real logo
# (via Clearbit's free logo lookup) without the user typing anything in.
DEFAULT_LOGO_DOMAINS = [
    (r"ROCKET MORTGAGE", "rocketmortgage.com"),
    (r"PROG SELECT|PROGRESSIVE", "progressive.com"),
    (r"\bGEICO\b", "geico.com"),
    (r"STATE FARM", "statefarm.com"),
    (r"ALLSTATE", "allstate.com"),
    (r"LIBERTY MUTUAL", "libertymutual.com"),
    (r"\bUSAA\b", "usaa.com"),
    (r"NATIONWIDE", "nationwide.com"),
    (r"XFINITY MOBILE|COMCAST|XFINITY", "xfinity.com"),
    (r"VERIZON", "verizon.com"),
    (r"AT&T", "att.com"),
    (r"T-MOBILE", "t-mobile.com"),
    (r"\bSPRINT\b", "sprint.com"),
    (r"\bJEA\b", "jea.com"),
    (r"DUKE ENERGY", "duke-energy.com"),
    (r"VCA CARE CLUB", "vcahospitals.com"),
    (r"VETSOURCE", "vetsource.com"),
    (r"BANFIELD", "banfield.com"),
    (r"PETSMART", "petsmart.com"),
    (r"\bPETCO\b", "petco.com"),
    (r"COOKUNITY", "cookunity.com"),
    (r"HELLOFRESH", "hellofresh.com"),
    (r"BLUE APRON", "blueapron.com"),
    (r"FACTOR75|\bFACTOR\b", "factor75.com"),
    (r"DOORDASH", "doordash.com"),
    (r"UBER EATS", "ubereats.com"),
    (r"GRUBHUB", "grubhub.com"),
    (r"AMAZON", "amazon.com"),
    (r"GOOGLE", "google.com"),
    (r"MICROSOFT", "microsoft.com"),
    (r"ANTHROPIC|CLAUDE\.AI|CLAUDE AI", "anthropic.com"),
    (r"NETFLIX", "netflix.com"),
    (r"SPOTIFY", "spotify.com"),
    (r"\bHULU\b", "hulu.com"),
    (r"DISNEY\+|DISNEY PLUS", "disneyplus.com"),
    (r"PLANET FITNESS", "planetfitness.com"),
    (r"LA FITNESS", "lafitness.com"),
    (r"CVS", "cvs.com"),
    (r"WALMART", "walmart.com"),
    (r"\bTARGET\b", "target.com"),
    (r"KROGER", "kroger.com"),
    (r"PUBLIX", "publix.com"),
    (r"WHOLE FOODS", "wholefoodsmarket.com"),
    (r"TRADER JOE", "traderjoes.com"),
    (r"SHELL\b", "shell.com"),
    (r"EXXON", "exxon.com"),
    (r"CHEVRON", "chevron.com"),
    (r"\bWAWA\b", "wawa.com"),
    (r"CIRCLE K", "circlek.com"),
    (r"BEST BUY", "bestbuy.com"),
    (r"HOME DEPOT", "homedepot.com"),
    (r"LOWE'?S", "lowes.com"),
    (r"STARBUCKS", "starbucks.com"),
    (r"SUPERCUTS", "supercuts.com"),
    (r"PYBRIDGECREST", ""),  # obscure/unclear company - no confident guess
]
_COMPILED_LOGO_DOMAINS = [(re.compile(p, re.IGNORECASE), d) for p, d in DEFAULT_LOGO_DOMAINS if d]


def guess_logo_domain(text: str) -> str:
    """Best-effort guess at a company's website domain from a bill name or
    transaction description, for showing a logo. Returns '' if unsure -
    the caller should leave logo_domain blank rather than guess wrong."""
    for regex, domain in _COMPILED_LOGO_DOMAINS:
        if regex.search(text):
            return domain
    return ""


# --- Auto categorization ----------------------------------------------------------

# Ordered (regex, category, is_income). First match on the RAW description wins.
DEFAULT_RULES = [
    (r"ADT LLC\s+PAYROLL", "Income", True),
    (r"PAYROLL", "Income", True),
    (r"ROCKET MORTGAGE", "Housing", False),
    (r"PROG SELECT INS|PROGRESSIVE", "Insurance", False),
    (r"XFINITY MOBILE", "Phone", False),
    (r"COMCAST|XFINITY", "Utilities", False),
    (r"JEA\s*/?\s*EZPAY|JEA\b", "Utilities", False),
    (r"PYBRIDGECREST", "Loan Payment", False),
    (r"VCA CARE CLUB|VETSOURCE", "Pet Care", False),
    (r"COOKUNITY|FACTOR75|\bFACTOR\b", "Meal Kit / Food Delivery", False),
    (r"DOORDASH|DD \*DOORDASH", "Dining / Delivery", False),
    (r"AMAZON PRIME", "Subscriptions", False),
    (r"AMAZON MKTPL|AMAZON\.COM|AMZN", "Shopping", False),
    (r"GOOGLE \*YOUTUBE", "Subscriptions", False),
    (r"GOOGLE \*GOOGLE ONE", "Subscriptions", False),
    (r"MICROSOFT", "Subscriptions", False),
    (r"ANTHROPIC|CLAUDE\.AI", "Subscriptions", False),
    (r"NZBFINDER|NEWSHOSTING", "Subscriptions", False),
    (r"CVS/PHARMACY|PHARMACY", "Health", False),
    (r"WAWA|CIRCLE K", "Gas / Convenience", False),
    (r"^CHECK$", "Check Payment", False),
    (r"INTERNATIONAL PURCHASE TRANSACTION FEE", "Fees", False),
    (r"APPLE CASH|MONEY TRANSFER", "Transfer", False),
    (r"Jacksonville\s+Jacksonville", "Tolls / Parking", False),
    (r"MS SOCIET", "Donations", False),
    (r"PURCHASE RETURN", "Refund", False),
    (r"VPN\*|GOTHENBURG", "Subscriptions", False),
    (r"SUPERCUTS", "Personal Care", False),
    (r"SMOOTHIE|DINER|STEAK|ALE HOUSE|GRILL|CANTEEN|STARBUCKS|COFFEE", "Restaurants", False),
    # Generic/national merchants not tied to any one person's specific bank history
    (r"MORTGAGE", "Housing", False),
    (r"\bRENT\b|APARTMENTS|PROPERTY MGMT|PROPERTY MANAGEMENT", "Housing", False),
    (r"GEICO|STATE FARM|ALLSTATE|LIBERTY MUTUAL|USAA|NATIONWIDE|\bINSURANCE\b", "Insurance", False),
    (r"AUTO FINANCE|AUTO LOAN|CAR LOAN", "Loan Payment", False),
    (r"STUDENT LOAN|SALLIE MAE|NAVIENT|NELNET", "Loan Payment", False),
    (r"DUKE ENERGY|\bELECTRIC\b|POWER (CO|COMPANY)|\bENERGY\b|WATER (CO|COMPANY|UTILITY)|GAS COMPANY", "Utilities", False),
    (r"VERIZON|AT&T|T-MOBILE|\bSPRINT\b", "Phone", False),
    (r"NETFLIX|HULU|DISNEY\+|SPOTIFY|PARAMOUNT\+|\bHBO\b|PEACOCK|APPLE TV|APPLE MUSIC", "Subscriptions", False),
    (r"HELLOFRESH|BLUE APRON|HOME CHEF|SUNBASKET", "Meal Kit / Food Delivery", False),
    (r"UBER EATS|GRUBHUB|POSTMATES|INSTACART", "Dining / Delivery", False),
    (r"PLANET FITNESS|\bLA FITNESS\b|\bGYM\b|\bFITNESS\b|YMCA", "Health", False),
    (r"PET HOSPITAL|VETERINAR|BANFIELD|PETSMART|PETCO", "Pet Care", False),
    (r"KROGER|PUBLIX|WALMART|\bTARGET\b|SAFEWAY|WHOLE FOODS|TRADER JOE|ALDI|WINN-DIXIE|GROCERY", "Groceries", False),
    (r"SHELL\b|EXXON|CHEVRON|\bBP\b|SPEEDWAY|MARATHON PETROLEUM", "Gas / Convenience", False),
    (r"TOLL AUTHORITY|E-ZPASS|SUNPASS|FASTRAK|TOLLWAY", "Tolls / Parking", False),
    (r"BEST BUY|HOME DEPOT|LOWE'S|IKEA", "Shopping", False),
]
_COMPILED_RULES = [(re.compile(p, re.IGNORECASE), cat, inc) for p, cat, inc in DEFAULT_RULES]


# Default essential/discretionary/neutral classification for the "Money Leaks"
# insights feature. User overrides are stored in the category_types table and
# take priority over this. "neutral" categories (transfers, refunds, income)
# are excluded from both totals since they aren't really spending.
DEFAULT_CATEGORY_KIND = {
    "Housing": "essential",
    "Insurance": "essential",
    "Utilities": "essential",
    "Phone": "essential",
    "Loan Payment": "essential",
    "Health": "essential",
    "Pet Care": "essential",
    "Gas / Convenience": "essential",
    "Groceries": "essential",
    "Tolls / Parking": "essential",
    "Toll Roads": "essential",
    "Fees": "essential",
    "Check Payment": "essential",
    "Income": "neutral",
    "Transfer": "neutral",
    "Refund": "neutral",
    "Dining / Delivery": "discretionary",
    "Meal Kit / Food Delivery": "discretionary",
    "Restaurants": "discretionary",
    "Shopping": "discretionary",
    "Subscriptions": "discretionary",
    "Donations": "discretionary",
    "Personal Care": "discretionary",
}


def default_category_kind(category: str) -> str:
    return DEFAULT_CATEGORY_KIND.get(category, "discretionary")


def categorize(description: str, merchant_overrides: dict) -> tuple[str, bool]:
    """Return (category, is_income). merchant_overrides: merchant_key -> category (user memory)."""
    key = normalize_merchant(description)
    if key in merchant_overrides:
        return merchant_overrides[key], merchant_overrides[key] == "Income"
    for regex, cat, is_income in _COMPILED_RULES:
        if regex.search(description):
            return cat, is_income
    return "Uncategorized", False


# --- Recurring charge detection ----------------------------------------------------

def _median(vals):
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    if n % 2 == 0:
        return (s[mid - 1] + s[mid]) / 2
    return s[mid]


def _analyze_interval(cluster, allow_one_outlier):
    """Given a list of txn dicts (>=2), decide if they look recurring.
    Returns a result dict or None. amounts consistency check optionally
    tolerates a single outlier (e.g. one-off double paycheck)."""
    cluster = sorted(cluster, key=lambda t: t["date"])
    dates = [datetime.strptime(t["date"], "%Y-%m-%d") for t in cluster]
    amounts = [t["amount"] for t in cluster]
    intervals = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
    if not intervals:
        return None
    avg_interval = sum(intervals) / len(intervals)

    if 5 <= avg_interval <= 9:
        frequency, step_days = "weekly", 7
    elif 11 <= avg_interval <= 17:
        frequency, step_days = "biweekly", 14
    elif 25 <= avg_interval <= 36:
        frequency, step_days = "monthly", 30
    elif 350 <= avg_interval <= 380:
        frequency, step_days = "annual", 365
    else:
        return None

    med = _median([abs(a) for a in amounts])
    if med == 0:
        return None
    matches = sum(1 for a in amounts if abs(abs(a) - med) / med <= 0.40)
    min_required = max(2, len(amounts) - 1) if allow_one_outlier else len(amounts)
    if matches < min_required:
        return None

    is_income = amounts[-1] > 0
    last_date = dates[-1]
    if frequency in ("monthly",):
        next_due = last_date + timedelta(days=30)
    elif frequency == "annual":
        next_due = last_date + timedelta(days=365)
    else:
        next_due = last_date + timedelta(days=step_days)

    return {
        "occurrences": len(cluster),
        "avg_amount": round(med, 2),
        "is_income": is_income,
        "frequency": frequency,
        "last_date": last_date.strftime("%Y-%m-%d"),
        "suggested_next_due": next_due.strftime("%Y-%m-%d"),
        "suggested_due_day": last_date.day if frequency in ("monthly", "annual") else None,
    }


def detect_recurring(transactions, existing_bill_keys, dismissed_keys):
    """
    transactions: list of dicts with date (ISO str), description, amount, merchant_key
    Returns candidate recurring charges grouped by merchant_key, excluding merchants
    already tracked as bills or explicitly dismissed by the user.
    """
    groups = defaultdict(list)
    for t in transactions:
        key = t["merchant_key"]
        if not key or key in ("CHECK",):
            continue
        groups[key].append(t)

    candidates = []
    for key, txns in groups.items():
        if len(txns) < 2:
            continue

        # A single merchant can bill multiple genuinely distinct amounts for
        # different products (e.g. two separate pet-care plans). Only split
        # into per-amount candidates when doing so cleanly accounts for every
        # transaction and each sub-amount independently looks recurring -
        # otherwise (e.g. a utility bill whose amount just varies month to
        # month) fall back to treating the merchant as one flexible-amount bill.
        clusters = _cluster_by_amount(txns)
        per_cluster_results = []
        covered = 0
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            result = _analyze_interval(cluster, allow_one_outlier=False)
            if result:
                per_cluster_results.append((cluster, result))
                covered += len(cluster)

        entries = []  # list of (composite_key, result)
        if len(per_cluster_results) >= 2 and covered == len(txns):
            for idx, (cluster, result) in enumerate(per_cluster_results):
                composite_key = key if idx == 0 else f"{key}#{idx}"
                entries.append((composite_key, result))
        else:
            whole = _analyze_interval(txns, allow_one_outlier=True)
            if whole:
                entries.append((key, whole))

        for composite_key, result in entries:
            if composite_key in existing_bill_keys or composite_key in dismissed_keys:
                continue
            candidates.append({
                "merchant_key": composite_key,
                "display_name": display_name(key),
                **result,
            })

    candidates.sort(key=lambda c: c["last_date"], reverse=True)
    return candidates


def _cluster_by_amount(txns):
    """Split a merchant's transactions into clusters of similar amounts."""
    if not txns:
        return []
    ordered = sorted(txns, key=lambda t: abs(t["amount"]))
    clusters = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        smaller = min(abs(prev["amount"]), abs(cur["amount"])) or 1
        if abs(abs(cur["amount"]) - abs(prev["amount"])) / smaller <= 0.40:
            clusters[-1].append(cur)
        else:
            clusters.append([cur])
    return clusters
