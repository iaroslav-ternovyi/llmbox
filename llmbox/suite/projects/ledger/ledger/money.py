from decimal import Decimal, ROUND_HALF_UP


def parse_amount(text: str) -> int:
    """'1,234.56' / '-12.5' / '€7' / '(3.10)' -> cents (int), rounding half up."""
    t = text.strip()
    negative = False
    if t.startswith("(") and t.endswith(")"):  #@MUT money-parens: if False:
        negative = True
        t = t[1:-1]
    t = t.replace("€", "").replace("$", "").replace(",", "").strip()
    if t.startswith("-"):
        negative = not negative
        t = t[1:]
    value = Decimal(t)
    cents = int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))  #@MUT money-round: cents = int(value * 100)
    return -cents if negative else cents


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""  #@MUT money-sign: sign = ""
    cents = abs(cents)
    return f"{sign}{cents // 100:,}.{cents % 100:02d}"
