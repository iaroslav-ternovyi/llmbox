from decimal import Decimal, ROUND_HALF_UP


def apply(codes, shipping_cents: int, zone: int, n_parcels: int) -> int:
    codes = {c.upper() for c in codes}  #@MUT pr-case: codes = set(codes)
    if "FREESHIP" in codes and zone <= 2:  #@MUT pr-free: if "FREESHIP" in codes:
        return 0
    factor = Decimal(1)
    if "TENOFF" in codes:
        factor *= Decimal("0.90")
    if "BULK" in codes and n_parcels >= 3:  #@MUT pr-bulk: if "BULK" in codes and n_parcels > 3:
        factor *= Decimal("0.95")
    return int((Decimal(shipping_cents) * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
