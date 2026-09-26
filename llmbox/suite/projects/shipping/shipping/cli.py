import argparse
import datetime as dt
import json

from .boxes import DEFAULT_BOXES, HOLIDAYS
from .invoice import quote
from .models import Item, Order
from .units import to_cm, to_grams


def load_order(path: str) -> Order:
    d = json.load(open(path))
    items = []
    for it in d["items"]:
        l, w, h, unit = it["size"]
        items.append(Item(it["sku"], int(it["qty"]), to_grams(*it["weight"]), to_cm(l, unit), to_cm(w, unit), to_cm(h, unit)))
    return Order(d["id"], d["country"], d.get("service", "standard"), dt.datetime.fromisoformat(d["ordered_at"]),
                 d.get("promos", []), items)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("order")
    # FEATURE-BEGIN insurance
    ap.add_argument("--insure", type=int, default=0)
    # FEATURE-END
    a = ap.parse_args(argv)
    order = load_order(a.order)
    # FEATURE-BEGIN insurance
    order.insured_value_cents = a.insure
    # FEATURE-END
    print(json.dumps(quote(order, DEFAULT_BOXES, HOLIDAYS), sort_keys=True))


if __name__ == "__main__":
    main()
