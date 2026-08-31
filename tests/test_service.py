import unittest
from datetime import datetime

from app.domain import get_market
from app.service import make_list_name


class ListNameTests(unittest.TestCase):
    def test_market_prefix_and_safe_file_name(self):
        name = make_list_name(get_market("ZA"), "South Africa contacts (final).csv", datetime(2026, 8, 4))
        self.assertEqual(name, "Za_20260804_South_Africa_contacts_final")
        self.assertLessEqual(len(name), 100)


if __name__ == "__main__":
    unittest.main()
