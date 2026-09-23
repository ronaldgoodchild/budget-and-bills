"""Standard loan amortization math for the debt payoff tracker."""
import math
from datetime import datetime, timedelta


def months_to_payoff(balance, apr, payment):
    """Returns months to pay off `balance` at `payment`/month, or None if the
    payment doesn't even cover the interest (balance never shrinks)."""
    if balance <= 0:
        return 0
    monthly_rate = (apr / 100) / 12
    if monthly_rate == 0:
        if payment <= 0:
            return None
        return math.ceil(balance / payment)
    if payment <= balance * monthly_rate:
        return None
    months = -math.log(1 - (balance * monthly_rate) / payment) / math.log(1 + monthly_rate)
    return math.ceil(months)


def total_interest(balance, apr, payment, months):
    if months is None:
        return None
    return round(payment * months - balance, 2)


def payoff_summary(balance, apr, payment, extra=100):
    months = months_to_payoff(balance, apr, payment)
    interest = total_interest(balance, apr, payment, months)
    months_with_extra = months_to_payoff(balance, apr, payment + extra)
    interest_with_extra = total_interest(balance, apr, payment + extra, months_with_extra)

    payoff_date = None
    if months is not None:
        payoff_date = (datetime.now() + timedelta(days=30 * months)).strftime("%Y-%m-%d")

    interest_saved = None
    if interest is not None and interest_with_extra is not None:
        interest_saved = round(interest - interest_with_extra, 2)

    return {
        "months": months,
        "total_interest": interest,
        "payoff_date": payoff_date,
        "months_with_extra": months_with_extra,
        "interest_saved": interest_saved,
    }
