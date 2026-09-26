import unittest

from sheet.engine import Sheet


class Visible(unittest.TestCase):
    def test_basic(self):
        s = Sheet()
        s.set("A1", "10")
        s.set("B1", "=A1*2+1")
        self.assertEqual(s.get("B1"), 21)

    def test_sum(self):
        s = Sheet()
        for i in range(1, 4):
            s.set(f"A{i}", str(i))
        s.set("A4", "=SUM(A1:A3)")
        self.assertEqual(s.get("A4"), 6)


if __name__ == "__main__":
    unittest.main()
