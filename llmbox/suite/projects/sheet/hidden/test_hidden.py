import unittest

from sheet.engine import Sheet


def sheet(**cells):
    s = Sheet()
    for k, v in cells.items():
        s.set(k, v)
    return s


class L3_Basics(unittest.TestCase):
    def test_arith_precedence(self):
        s = sheet(A1="=2+3*4", A2="=(2+3)*4", A3="=10/4", A4="=7-2-1")
        self.assertEqual([s.get(c) for c in ("A1", "A2", "A3", "A4")], [14, 20, 2.5, 4])

    def test_refs_and_update(self):
        s = sheet(A1="5", B1="=A1*A1", C1="=B1+A1")
        self.assertEqual(s.get("C1"), 30)
        s.set("A1", "2")
        self.assertEqual((s.get("B1"), s.get("C1")), (4, 6))

    def test_empty_is_zero_and_none(self):
        s = sheet(A1="=B1+1")
        self.assertEqual(s.get("A1"), 1)
        self.assertIsNone(s.get("B1"))

    def test_numbers_and_text(self):
        s = sheet(A1="-3.5", A2="1e3", A3="hello", A4="12")
        self.assertEqual((s.get("A1"), s.get("A2"), s.get("A3"), s.get("A4")), (-3.5, 1000, "hello", 12))

    def test_int_normalization(self):
        s = sheet(A1="=1.5*2", A2="=7/7")
        self.assertIsInstance(s.get("A1"), int)
        self.assertEqual(s.get("A2"), 1)

    def test_clear(self):
        s = sheet(A1="4", B1="=A1+1")
        s.set("A1", "")
        self.assertEqual(s.get("B1"), 1)


class L3_Ranges(unittest.TestCase):
    def test_sum_min_max(self):
        s = sheet(A1="4", A2="-2", B1="10", B2="x")
        self.assertEqual(s.get("C1") if False else None, None)
        s.set("C1", "=SUM(A1:B2)"); s.set("C2", "=MIN(A1:B2)"); s.set("C3", "=MAX(B2:A1)")
        self.assertEqual((s.get("C1"), s.get("C2"), s.get("C3")), (12, -2, 10))

    def test_sum_mixed_args(self):
        s = sheet(A1="1", A2="2", A3="=SUM(A1:A2, 10, A1*3)")
        self.assertEqual(s.get("A3"), 16)

    def test_min_empty(self):
        s = sheet(A1="=MIN(B1:B5)", A2="=MAX(B1:B5)")
        self.assertEqual((s.get("A1"), s.get("A2")), (0, 0))

    def test_case_insensitive_fn(self):
        s = sheet(A1="2", A2="3", A3="=sum(a1:a2)")
        self.assertEqual(s.get("A3"), 5)


class L3_Errors(unittest.TestCase):
    def test_div0(self):
        s = sheet(A1="=1/0", A2="=A1+1", A3="=5/B9")
        self.assertEqual((s.get("A1"), s.get("A2"), s.get("A3")), ("#DIV/0!",) * 3)

    def test_ref(self):
        s = sheet(A1="=AA1+1", A2="=A0", A3="=A100*2")
        self.assertEqual((s.get("A1"), s.get("A2"), s.get("A3")), ("#REF!",) * 3)

    def test_malformed(self):
        s = sheet(A1="=1+", A2="=(2", A3="=2**3")
        self.assertEqual((s.get("A1"), s.get("A2"), s.get("A3")), ("#VALUE!",) * 3)


class L4_Text(unittest.TestCase):
    def test_concat(self):
        s = sheet(A1="2", A2="=A1&\"x\"", A3="=1+2&\"-\"&3*2", A4="=\"a\"&B9&\"b\"")
        self.assertEqual((s.get("A2"), s.get("A3"), s.get("A4")), ("2x", "3-6", "ab"))

    def test_text_in_arith(self):
        s = sheet(A1="hello", A2="=A1+1", A3="=\"3\"*2")
        self.assertEqual((s.get("A2"), s.get("A3")), ("#VALUE!", "#VALUE!"))

    def test_len_concat(self):
        s = sheet(A1="ab", A2="3", B1="c", A3="=LEN(A1&A2)", A4="=CONCAT(A1:B2, \"!\")")
        self.assertEqual((s.get("A3"), s.get("A4")), (3, "abc3!"))

    def test_float_text_form(self):
        s = sheet(A1="=10/4&\"\"", A2="=4/2&\"\"")
        self.assertEqual((s.get("A1"), s.get("A2")), ("2.5", "2"))


class L4_Compare(unittest.TestCase):
    def test_numbers(self):
        s = sheet(A1="3", A2="=A1>2", A3="=A1<>3", A4="=A1<=3", A5="=B9=0")
        self.assertEqual((s.get("A2"), s.get("A3"), s.get("A4"), s.get("A5")), (True, False, True, True))

    def test_strings(self):
        s = sheet(A1="=\"abc\"=\"abc\"", A2="=\"a\"<\"b\"", A3="=\"a\"=1")
        self.assertEqual((s.get("A1"), s.get("A2"), s.get("A3")), (True, True, "#VALUE!"))

    def test_bool_arith(self):
        s = sheet(A1="=(2>1)+(3>4)+TRUE")
        self.assertEqual(s.get("A1"), 2)


class L4_Functions(unittest.TestCase):
    def test_if_lazy(self):
        s = sheet(A1="0", A2="=IF(A1=0, 1, 1/A1)", A3="=IF(A1, 1/A1, \"zero\")")
        self.assertEqual((s.get("A2"), s.get("A3")), (1, "zero"))

    def test_average_count(self):
        s = sheet(A1="2", A2="x", A3="4", A4="=AVERAGE(A1:A3)", A5="=COUNT(A1:A3, B1:B2)", A6="=AVERAGE(B1:B3)")
        self.assertEqual((s.get("A4"), s.get("A5"), s.get("A6")), (3, 2, "#DIV/0!"))

    def test_round(self):
        s = sheet(A1="=ROUND(2.5, 0)", A2="=ROUND(-2.5, 0)", A3="=ROUND(1234.5678, 2)", A4="=ROUND(1250, -2)", A5="=ROUND(0.125, 2)")
        self.assertEqual((s.get("A1"), s.get("A2"), s.get("A3"), s.get("A4"), s.get("A5")), (3, -3, 1234.57, 1300, 0.13))

    def test_unknown_fn(self):
        s = sheet(A1="=FOO(1)", A2="=A1+1")
        self.assertEqual((s.get("A1"), s.get("A2")), ("#NAME?", "#NAME?"))


class L5_Precedence(unittest.TestCase):
    def test_power(self):
        s = sheet(A1="=-2^2", A2="=2^3^2", A3="=(-2)^2", A4="=2^-1", A5="=-(2)^3")
        self.assertEqual((s.get("A1"), s.get("A2"), s.get("A3"), s.get("A4"), s.get("A5")), (-4, 512, 4, 0.5, -8))

    def test_concat_vs_compare(self):
        s = sheet(A1="=1&2=\"12\"", A2="=1+1&1+1")
        self.assertEqual((s.get("A1"), s.get("A2")), (True, "22"))


class L5_Cycles(unittest.TestCase):
    def test_two_cycle(self):
        s = sheet(A1="=B1+1", B1="=A1+1", C1="=A1*2", D1="5")
        self.assertEqual((s.get("A1"), s.get("B1"), s.get("C1"), s.get("D1")), ("#CYCLE!", "#CYCLE!", "#CYCLE!", 5))

    def test_self_and_range_cycle(self):
        s = sheet(A1="=A1", B1="1", B2="=SUM(B1:B3)")
        self.assertEqual((s.get("A1"), s.get("B2")), ("#CYCLE!", "#CYCLE!"))

    def test_cycle_breaks_after_edit(self):
        s = sheet(A1="=B1+1", B1="=A1+1")
        s.set("B1", "10")
        self.assertEqual((s.get("A1"), s.get("B1")), (11, 10))

    def test_long_chain_no_cycle(self):
        s = Sheet()
        s.set("A1", "1")
        for i in range(2, 60):
            s.set(f"A{i}", f"=A{i-1}+1")
        self.assertEqual(s.get("A59"), 59)


class L5_ErrorOrder(unittest.TestCase):
    def test_first_error_wins(self):
        s = sheet(A1="=1/0", A2="=AA1", A3="=A1+A2", A4="=A2+A1", A5="=SUM(A2, A1)")
        self.assertEqual((s.get("A3"), s.get("A4"), s.get("A5")), ("#DIV/0!", "#REF!", "#REF!"))

    def test_range_error_row_major(self):
        s = sheet(A1="1", B1="=1/0", A2="=Q0", B2="2", C1="=SUM(A1:B2)")
        self.assertEqual(s.get("C1"), "#DIV/0!")

    def test_error_in_concat(self):
        s = sheet(A1="=1/0", A2="=\"x\"&A1")
        self.assertEqual(s.get("A2"), "#DIV/0!")


if __name__ == "__main__":
    unittest.main()
