import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import unittest

from shipping import calendar, packing, promos, rates
from shipping.boxes import DEFAULT_BOXES, HOLIDAYS
from shipping.invoice import quote
from shipping.models import Item, Order, Parcel
from shipping.units import to_cm, to_grams


def P(box, w):
    b = {x.name: x for x in DEFAULT_BOXES}[box]
    return Parcel(box, [], w, b.l, b.w, b.h)


class Units(unittest.TestCase):
    def test_grams(self):
        self.assertEqual(to_grams(1.5, "lb"), 680)
        self.assertEqual(to_grams(10, "oz"), 283)
        self.assertEqual(to_grams(0.0005, "kg"), 1)
        self.assertEqual(to_grams(2, "lb"), 907)

    def test_cm(self):
        self.assertAlmostEqual(to_cm(10, "in"), 25.4)


class Packing(unittest.TestCase):
    def test_order_and_first_fit(self):
        items = [Item("small", 3, 300, 10, 10, 5), Item("big", 1, 1500, 30, 20, 15)]
        ps = packing.pack(items, DEFAULT_BOXES)
        self.assertEqual([p.box for p in ps], ["M"])
        self.assertEqual(ps[0].skus, ["big", "small", "small", "small"])
        self.assertEqual(ps[0].weight_g, 1500 + 900 + 250)

    def test_weight_limit_opens_new_parcel(self):
        items = [Item("brick", 3, 1500, 8, 8, 8)]
        ps = packing.pack(items, DEFAULT_BOXES)
        self.assertEqual([p.box for p in ps], ["S", "S", "S"])
        self.assertEqual([p.weight_g for p in ps], [1600, 1600, 1600])

    def test_too_big(self):
        with self.assertRaises(ValueError):
            packing.pack([Item("x", 1, 100, 60, 60, 60)], DEFAULT_BOXES)


class Rates(unittest.TestCase):
    def test_zones(self):
        self.assertEqual([rates.zone(c) for c in ("es", "PT", "FR", "DE", "IT", "US")], [1, 2, 2, 3, 3, 4])

    def test_billable(self):
        self.assertEqual(rates.billable_g(P("S", 1200)), 1500)
        self.assertEqual(rates.billable_g(P("L", 900)), 14000)
        self.assertEqual(rates.billable_g(P("S", 1001)), 1500)
        self.assertEqual(rates.billable_g(P("S", 500)), 1000)

    def test_price(self):
        self.assertEqual(rates.parcel_price(P("S", 1200), 1, "standard"), 690)
        self.assertEqual(rates.parcel_price(P("S", 1200), 1, "express"), 1035)
        self.assertEqual(rates.parcel_price(P("M", 3000), 4, "standard"), 1500 + 300 * 7)


class Calendar(unittest.TestCase):
    def test_cutoff_and_holiday(self):
        self.assertEqual(calendar.estimate(dt.datetime(2026, 10, 9, 15, 0), 2, "standard", HOLIDAYS), dt.date(2026, 10, 15))
        self.assertEqual(calendar.estimate(dt.datetime(2026, 10, 9, 14, 0), 1, "standard", HOLIDAYS), dt.date(2026, 10, 14))
        self.assertEqual(calendar.estimate(dt.datetime(2026, 10, 7, 9, 0), 1, "standard", HOLIDAYS), dt.date(2026, 10, 8))

    def test_express_min_one(self):
        self.assertEqual(calendar.estimate(dt.datetime(2026, 10, 7, 9, 0), 1, "express", HOLIDAYS), dt.date(2026, 10, 8))
        self.assertEqual(calendar.estimate(dt.datetime(2026, 10, 7, 9, 0), 4, "express", HOLIDAYS), dt.date(2026, 10, 14))

    def test_weekend_order(self):
        self.assertEqual(calendar.estimate(dt.datetime(2026, 10, 10, 10, 0), 3, "standard", HOLIDAYS), dt.date(2026, 10, 16))


class Promos(unittest.TestCase):
    def test_freeship(self):
        self.assertEqual(promos.apply(["freeship", "TENOFF"], 1000, 2, 1), 0)
        self.assertEqual(promos.apply(["FREESHIP"], 1000, 3, 1), 1000)

    def test_stack(self):
        self.assertEqual(promos.apply(["TenOff", "BULK"], 1001, 3, 3), 856)
        self.assertEqual(promos.apply(["BULK"], 1000, 3, 2), 1000)
        self.assertEqual(promos.apply(["BULK", "nope"], 1000, 3, 3), 950)


class Invoice(unittest.TestCase):
    ORDER_ITEMS = [Item("mug", 2, 450, 12, 12, 11), Item("lamp", 1, 2600, 30, 20, 18)]

    def test_quote_es_promo(self):
        o = Order("A1", "ES", "standard", dt.datetime(2026, 10, 7, 9, 0), ["tenoff"], list(self.ORDER_ITEMS))
        q = quote(o, DEFAULT_BOXES, HOLIDAYS)
        self.assertEqual(q["parcels"], 1)
        self.assertEqual(q["shipping_cents"], 981)
        self.assertEqual(q["vat_cents"], 206)
        self.assertEqual(q["total_cents"], 1187)
        self.assertEqual(q["delivery"], "2026-10-08")

    def test_quote_us_express(self):
        o = Order("A2", "US", "express", dt.datetime(2026, 12, 24, 16, 0), [], list(self.ORDER_ITEMS))
        q = quote(o, DEFAULT_BOXES, HOLIDAYS)
        self.assertEqual((q["shipping_cents"], q["vat_cents"], q["delivery"]), (5850, 0, "2027-01-01"))


class Insurance(unittest.TestCase):
    def test_fee_min_and_vat(self):
        o = Order("A3", "DE", "standard", dt.datetime(2026, 10, 7, 9, 0), [], [Item("mug", 1, 450, 12, 12, 11)])
        o.insured_value_cents = 5000
        q = quote(o, DEFAULT_BOXES, HOLIDAYS)
        self.assertEqual(q["insurance_cents"], 150)
        self.assertEqual(q["total_cents"], q["shipping_cents"] + q["vat_cents"] + 150 + 29)
        o.insured_value_cents = 50050
        self.assertEqual(quote(o, DEFAULT_BOXES, HOLIDAYS)["insurance_cents"], 501)

    def test_no_insurance_key_without_value(self):
        o = Order("A4", "DE", "standard", dt.datetime(2026, 10, 7, 9, 0), [], [Item("mug", 1, 450, 12, 12, 11)])
        self.assertNotIn("insurance_cents", quote(o, DEFAULT_BOXES, HOLIDAYS))


class Cli(unittest.TestCase):
    def run_cli(self, order, *extra):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, dir=".") as f:
            json.dump(order, f)
        try:
            out = subprocess.run([sys.executable, "-m", "shipping.cli", f.name, *extra], capture_output=True, text=True, timeout=30)
            return json.loads(out.stdout)
        finally:
            os.unlink(f.name)

    ORDER = {"id": "C1", "country": "PT", "service": "standard", "ordered_at": "2026-10-09T15:30:00", "promos": ["BULK"],
             "items": [{"sku": "book", "qty": 4, "weight": [2.2, "lb"], "size": [9, 6, 2, "in"]}]}

    def test_cli_quote(self):
        q = self.run_cli(self.ORDER)
        self.assertEqual(q["delivery"], "2026-10-15")
        self.assertEqual(q["parcels"], 4)
        self.assertEqual(q["shipping_cents"], 4028)
        self.assertEqual(q["total_cents"], 4028 + 926)

    def test_cli_insure(self):
        q = self.run_cli(self.ORDER, "--insure", "30000")
        self.assertEqual(q["insurance_cents"], 300)
        self.assertEqual(q["total_cents"], 4028 + 926 + 300 + 69)


if __name__ == "__main__":
    unittest.main()
