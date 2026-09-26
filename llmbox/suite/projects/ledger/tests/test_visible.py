import unittest

from ledger.accounts import Account, InsufficientFunds
from ledger.importer import parse_csv
from ledger.interest import monthly_interest
from ledger.money import format_cents, parse_amount
from ledger.report import top_accounts, totals_by_month


class Visible(unittest.TestCase):
    def test_parse_basic(self):
        self.assertEqual(parse_amount("1,234.56"), 123456)
        self.assertEqual(parse_amount("-12.5"), -1250)

    def test_format(self):
        self.assertEqual(format_cents(123456), "1,234.56")

    def test_withdraw_savings(self):
        a = Account("s", "savings", 1000)
        with self.assertRaises(InsufficientFunds):
            a.withdraw(1001)

    def test_interest_low_tier(self):
        self.assertEqual(monthly_interest(500_000, 2026, 4), 411)

    def test_import(self):
        rows = parse_csv("2026-01-05,main,12.00,coffee\n# note\n\n2026-01-06,main,-2.00,refund\n")
        self.assertEqual(len(rows), 2)

    def test_totals(self):
        rows = [("2026-01-05", "main", 1200, ""), ("2026-02-01", "main", -200, "")]
        self.assertEqual(totals_by_month(rows), {"2026-01": {"main": 1200}, "2026-02": {"main": -200}})

    def test_top(self):
        rows = [("2026-01-05", "a", 500, ""), ("2026-01-05", "b", 900, "")]
        self.assertEqual(top_accounts(rows, 1), ["b"])


if __name__ == "__main__":
    unittest.main()
