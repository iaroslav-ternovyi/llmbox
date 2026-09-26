import math
from decimal import Decimal, ROUND_HALF_UP

ZONES = {"ES": 1, "PT": 2, "FR": 2, "DE": 3, "IT": 3}  #@MUT r-zone: ZONES = {"ES": 1, "PT": 3, "FR": 2, "DE": 3, "IT": 3}
BASE = {1: 450, 2: 700, 3: 900, 4: 1500}
PER_500G = {1: 80, 2: 120, 3: 150, 4: 300}


def zone(country: str) -> int:
    return ZONES.get(country.upper(), 4)


def billable_g(parcel) -> int:
    dim = parcel.l * parcel.w * parcel.h / 5000 * 1000  #@MUT r-dim: dim = parcel.l * parcel.w * parcel.h / 6000 * 1000
    g = max(parcel.weight_g, dim)
    return int(math.ceil(g / 500) * 500)  #@MUT r-step: return int(round(g / 500) * 500)


def parcel_price(parcel, z: int, service: str) -> int:
    price = BASE[z] + PER_500G[z] * (billable_g(parcel) // 500)
    if service == "express":
        price = int((Decimal(price) * Decimal("1.5")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return price
