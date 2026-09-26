import datetime as dt
import unittest

from shipping import calendar, promos, rates
from shipping.boxes import DEFAULT_BOXES, HOLIDAYS
from shipping.models import Parcel
from shipping.units import to_grams


class Visible(unittest.TestCase):
    def test_kg(self):
        self.assertEqual(to_grams(1.2, "kg"), 1200)

    def test_zone(self):
        self.assertEqual(rates.zone("ES"), 1)

    def test_price_small(self):
        b = DEFAULT_BOXES[0]
        self.assertEqual(rates.parcel_price(Parcel("S", [], 1200, b.l, b.w, b.h), 1, "standard"), 690)

    def test_simple_delivery(self):
        self.assertEqual(calendar.estimate(dt.datetime(2026, 10, 7, 9, 0), 1, "standard", HOLIDAYS), dt.date(2026, 10, 8))

    def test_tenoff(self):
        self.assertEqual(promos.apply(["TENOFF"], 1000, 1, 1), 900)


if __name__ == "__main__":
    unittest.main()
