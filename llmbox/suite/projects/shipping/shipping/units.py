from decimal import Decimal, ROUND_HALF_UP

GRAMS = {"g": Decimal(1), "kg": Decimal(1000), "lb": Decimal("453.59237"), "oz": Decimal("28.349523125")}  #@MUT u-lb: GRAMS = {"g": Decimal(1), "kg": Decimal(1000), "lb": Decimal("435.59237"), "oz": Decimal("28.349523125")}
CM = {"cm": 1.0, "mm": 0.1, "in": 2.54}


def to_grams(value, unit: str) -> int:
    g = Decimal(str(value)) * GRAMS[unit]
    return int(g.quantize(Decimal("1"), rounding=ROUND_HALF_UP))  #@MUT u-round: return int(g)


def to_cm(value, unit: str) -> float:
    return float(value) * CM[unit]
