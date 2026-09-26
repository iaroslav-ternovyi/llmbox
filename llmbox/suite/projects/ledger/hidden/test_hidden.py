import unittest

from ledger.accounts import Account, InsufficientFunds
from ledger.importer import parse_csv
from ledger.interest import monthly_interest
from ledger.money import format_cents, parse_amount
from ledger.report import monthly_net, top_accounts, totals_by_month


class Money(unittest.TestCase):
    def test_parens_negative(self):
        self.assertEqual(parse_amount("(3.10)"), -310)
        self.assertEqual(parse_amount("-(3.10)".replace("-", "")), -310)

    def test_round_half_up(self):
        self.assertEqual(parse_amount("0.005"), 1)
        self.assertEqual(parse_amount("12.345"), 1235)
        self.assertEqual(parse_amount("€7"), 700)

    def test_format_negative(self):
        self.assertEqual(format_cents(-123456), "-1,234.56")
        self.assertEqual(format_cents(-5), "-0.05")


class Accounts(unittest.TestCase):
    def test_overdraft_exact_limit(self):
        a = Account("c", "checking", 0)
        a.withdraw(50000)
        self.assertEqual(a.balance, -50000)
        with self.assertRaises(InsufficientFunds):
            a.withdraw(1)

    def test_savings_exact_balance(self):
        a = Account("s", "savings", 1000)
        a.withdraw(1000)
        self.assertEqual(a.balance, 0)

    def test_transfer_atomic(self):
        a, b = Account("s", "savings", 100), Account("c", "checking", 0)
        with self.assertRaises(InsufficientFunds):
            a.transfer(b, 500)
        self.assertEqual((a.balance, b.balance), (100, 0))
        a.transfer(b, 100)
        self.assertEqual((a.balance, b.balance), (0, 100))


class Interest(unittest.TestCase):
    def test_days_in_month(self):
        self.assertEqual(monthly_interest(1_000_000, 2026, 2), 767)
        self.assertEqual(monthly_interest(1_000_000, 2028, 2), 795)
        self.assertEqual(monthly_interest(1_000_000, 2026, 1), 849)

    def test_tier_boundary(self):
        self.assertEqual(monthly_interest(1_000_000, 2026, 4), 822)
        self.assertEqual(monthly_interest(1_500_000, 2026, 4), 1644)

    def test_negative(self):
        self.assertEqual(monthly_interest(-100, 2026, 4), 0)


class Importer(unittest.TestCase):
    def test_indented_comment_and_quotes(self):
        rows = parse_csv('  # date,account,amount,memo\n2026-03-01,"main",(4.50)," lunch "\n\n2026-03-02,side,"1,000.00",big\n')
        self.assertEqual(rows, [("2026-03-01", "main", -450, "lunch"), ("2026-03-02", "side", 100000, "big")])


class Report(unittest.TestCase):
    ROWS = [("2026-01-05", "a", 500, ""), ("2026-01-20", "b", -500, ""), ("2026-02-02", "a", -200, ""),
            ("2026-11-02", "c", 700, ""), ("2026-12-24", "a", 100, "")]

    def test_months(self):
        t = totals_by_month(self.ROWS)
        self.assertEqual(sorted(t), ["2026-01", "2026-02", "2026-11", "2026-12"])
        self.assertEqual(t["2026-01"], {"a": 500, "b": -500})

    def test_top_abs_and_ties(self):
        self.assertEqual(top_accounts(self.ROWS, 3), ["a", "c", "b"])
        self.assertEqual(top_accounts([("2026-01-01", "z", 5, ""), ("2026-01-01", "m", -5, "")], 2), ["m", "z"])

    def test_monthly_net(self):
        self.assertEqual(monthly_net(self.ROWS, "a"), {"2026-01": 500, "2026-02": -200, "2026-12": 100})
        self.assertEqual(monthly_net(self.ROWS, "nobody"), {})


if __name__ == "__main__":
    unittest.main()
