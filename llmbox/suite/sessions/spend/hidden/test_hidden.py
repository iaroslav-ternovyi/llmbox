import json, os, sys, tempfile, unittest

TURNS = int(os.environ.get("LLMBOX_TURNS", "5"))
sys.path.insert(0, ".")


def need(n):
    return unittest.skipIf(TURNS < n, f"turn {n} not given")


def bank(rows, rates=None):
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "bank.csv"), "w", encoding="utf-8") as f:
        f.write("fecha;concepto;importe;divisa\n" + "\n".join(";".join(r) for r in rows) + "\n")
    if rates is None and TURNS >= 3:   # from turn 3 on the user said rates.json always lies next to the statement
        rates = {"USD": {"2026-01-01": 0.9}}
    if rates is not None:
        json.dump(rates, open(os.path.join(d, "rates.json"), "w"))
    return os.path.join(d, "bank.csv")


def nz(d):
    return {k: round(float(v), 2) for k, v in d.items() if abs(float(v)) > 0.001}


EUR = [("02/07/2026", "MERCADONA VALENCIA", "-54,20", "EUR"), ("03/07/2026", "UBER *TRIP", "-12,35", "EUR"),
       ("05/07/2026", "NETFLIX.COM", "-12,99", "EUR"), ("06/07/2026", "Lidl 0421", "-31,07", "EUR"),
       ("12/07/2026", "EL CORTE INGLES", "-1.234,50", "EUR"), ("01/07/2026", "NOMINA JULIO ACME SL", "2.450,00", "EUR"),
       ("14/08/2026", "BIZUM DE MARTA", "30,00", "EUR"), ("03/08/2026", "CABIFY", "-9,80", "EUR"),
       ("21/08/2026", "carrefour express", "-18,45", "EUR"), ("18/08/2026", "SPOTIFY P1234", "-10,99", "EUR")]


class T1_Categories(unittest.TestCase):
    def test_totals(self):
        import spend
        self.assertEqual(nz(spend.summarize(bank(EUR))),
                         {"еда": 103.72, "транспорт": 22.15, "подписки": 23.98, "другое": 1234.5})

    def test_income_ignored_and_case(self):
        import spend
        self.assertEqual(nz(spend.summarize(bank([("01/09/2026", "NOMINA", "3.000,00", "EUR"), ("02/09/2026", "RENFE VIAJEROS", "-45,60", "EUR")]))),
                         {"транспорт": 45.6})


@need(2)
class T2_ByMonth(unittest.TestCase):
    def test_months(self):
        import spend
        got = {m: nz(v) for m, v in spend.summarize_by_month(bank(EUR)).items()}
        got = {m: v for m, v in got.items() if v}
        self.assertEqual(got, {"2026-07": {"еда": 85.27, "транспорт": 12.35, "подписки": 12.99, "другое": 1234.5},
                               "2026-08": {"еда": 18.45, "транспорт": 9.8, "подписки": 10.99}})


RATES = {"USD": {"2026-07-01": 0.92, "2026-07-15": 0.9, "2026-08-01": 0.95}}


@need(3)
class T3_Currency(unittest.TestCase):
    def test_usd_exact_date_and_previous(self):
        import spend
        rows = [("01/07/2026", "GITHUB INC", "-10,00", "USD"), ("20/07/2026", "GITHUB INC", "-10,00", "USD"),
                ("03/08/2026", "OPENAI *CHATGPT", "-20,00", "USD"), ("04/08/2026", "UBER *TRIP", "-10,00", "EUR")]
        self.assertEqual(nz(spend.summarize(bank(rows, RATES))), {"другое": 37.2, "транспорт": 10.0})

    def test_usd_by_month(self):
        import spend
        rows = [("16/07/2026", "NETFLIX.COM", "-20,00", "USD"), ("02/08/2026", "NETFLIX.COM", "-20,00", "USD")]
        got = {m: nz(v) for m, v in spend.summarize_by_month(bank(rows, RATES)).items()}
        self.assertEqual(got, {"2026-07": {"подписки": 18.0}, "2026-08": {"подписки": 19.0}})


@need(4)
class T4_Rounding(unittest.TestCase):
    def test_half_even_per_operation(self):
        import spend
        rows = [("02/07/2026", "GITHUB INC", "-20,01", "USD"), ("03/07/2026", "GITHUB INC", "-20,01", "USD"),
                ("04/07/2026", "GITHUB INC", "-20,03", "USD")]
        # 10.005 -> 10.00, 10.005 -> 10.00, 10.015 -> 10.02 (half-even per operation, exact decimal arithmetic)
        self.assertEqual(nz(spend.summarize(bank(rows, {"USD": {"2026-07-01": 0.5}}))), {"другое": 30.02})


@need(5)
class T5_ShoppingAndRefunds(unittest.TestCase):
    def test_amazon_and_refund(self):
        import spend
        rows = [("07/07/2026", "AMAZON EU SARL", "-89,90", "EUR"), ("15/07/2026", "DEVOLUCION AMAZON EU SARL", "25,00", "EUR"),
                ("16/07/2026", "Refund Uber", "5,00", "EUR"), ("17/07/2026", "UBER *TRIP", "-12,00", "EUR"),
                ("18/07/2026", "BIZUM DE MARTA", "30,00", "EUR")]
        self.assertEqual(nz(spend.summarize(bank(rows))), {"покупки": 64.9, "транспорт": 7.0})

    def test_refund_by_month(self):
        import spend
        rows = [("30/07/2026", "AMAZON EU SARL", "-40,00", "EUR"), ("02/08/2026", "AMAZON REFUND", "15,00", "EUR"),
                ("03/08/2026", "AMAZON EU SARL", "-20,00", "EUR")]
        got = {m: nz(v) for m, v in spend.summarize_by_month(bank(rows)).items()}
        self.assertEqual(got, {"2026-07": {"покупки": 40.0}, "2026-08": {"покупки": 5.0}})


if __name__ == "__main__":
    unittest.main()
