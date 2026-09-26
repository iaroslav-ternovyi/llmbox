from decimal import Decimal, ROUND_HALF_UP

from . import calendar, packing, promos, rates

VAT = {"ES": 21, "PT": 23, "FR": 20, "DE": 19, "IT": 22}


def _pct(cents: int, pct) -> int:
    return int((Decimal(cents) * Decimal(pct) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def quote(order, boxes, holidays) -> dict:
    parcels = packing.pack(order.items, boxes)
    z = rates.zone(order.country)
    gross = sum(rates.parcel_price(p, z, order.service) for p in parcels)
    shipping = promos.apply(order.promos, gross, z, len(parcels))
    vat_rate = VAT.get(order.country.upper(), 0)
    vat = _pct(shipping, vat_rate)  #@MUT i-vat: vat = _pct(gross, vat_rate)
    out = {"parcels": len(parcels), "shipping_cents": shipping, "vat_cents": vat, "total_cents": shipping + vat,
           "delivery": calendar.estimate(order.ordered_at, z, order.service, holidays).isoformat()}
    # FEATURE-BEGIN insurance
    if order.insured_value_cents and order.insured_value_cents > 0:
        fee = max(150, _pct(order.insured_value_cents, 1))
        out["insurance_cents"] = fee
        out["total_cents"] += fee + _pct(fee, vat_rate)
    # FEATURE-END
    return out
