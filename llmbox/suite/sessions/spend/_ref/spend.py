import csv, datetime as dt, json, os
from decimal import Decimal, ROUND_HALF_EVEN

CATS = [(("mercadona", "lidl", "carrefour"), "еда"), (("uber", "cabify", "renfe"), "транспорт"),
        (("netflix", "spotify"), "подписки"), (("amazon",), "покупки")]


def _cat(desc):
    d = desc.lower()
    for keys, c in CATS:
        if any(k in d for k in keys):
            return c
    return "другое"


def _rows(path):
    rates_path = os.path.join(os.path.dirname(os.path.abspath(path)), "rates.json")
    rates = json.load(open(rates_path), parse_float=Decimal) if os.path.exists(rates_path) else {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter=";"):
            day = dt.datetime.strptime(row["fecha"], "%d/%m/%Y").date()
            amt = Decimal(row["importe"].replace(".", "").replace(",", "."))
            cur = (row.get("divisa") or "EUR").strip().upper()
            if cur != "EUR":
                table = rates[cur]
                k = max(d for d in table if d <= day.isoformat())
                amt = (amt * Decimal(str(table[k]))).quantize(Decimal("0.01"), ROUND_HALF_EVEN)
            desc = row["concepto"]
            if amt > 0:
                if "devolucion" in desc.lower() or "refund" in desc.lower():
                    yield day, _cat(desc), -amt
                continue
            yield day, _cat(desc), -amt


def summarize(path):
    out = {}
    for _d, c, a in _rows(path):
        out[c] = out.get(c, Decimal(0)) + a
    return {c: float(v) for c, v in out.items()}


def summarize_by_month(path):
    out = {}
    for d, c, a in _rows(path):
        m = out.setdefault(f"{d:%Y-%m}", {})
        m[c] = m.get(c, Decimal(0)) + a
    return {k: {c: float(v) for c, v in m.items()} for k, m in out.items()}
