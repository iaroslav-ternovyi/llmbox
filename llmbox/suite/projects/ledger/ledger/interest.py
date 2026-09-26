import calendar
from decimal import Decimal, ROUND_HALF_UP

TIER_LIMIT = 1_000_000          # 10,000.00 in cents
RATE_LOW, RATE_HIGH = Decimal("0.01"), Decimal("0.02")


def monthly_interest(balance_cents: int, year: int, month: int) -> int:
    if balance_cents <= 0:
        return 0
    days = calendar.monthrange(year, month)[1]  #@MUT int-days: days = 30
    low = min(balance_cents, TIER_LIMIT)
    high = balance_cents - low
    yearly = Decimal(low) * RATE_LOW + Decimal(high) * RATE_HIGH  #@MUT int-tier: yearly = Decimal(balance_cents) * (RATE_HIGH if balance_cents > TIER_LIMIT else RATE_LOW)
    value = yearly * days / 365
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
