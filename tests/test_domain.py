import unittest

from app.domain import SHARED_HEADERS, MarketCode, get_market


class MarketConfigurationTests(unittest.TestCase):
    def test_supported_market_prefixes(self):
        self.assertEqual(get_market("gh").list_prefix, "Gh")
        self.assertEqual(get_market("UG").list_prefix, "Ug")
        self.assertEqual(get_market(" za ").list_prefix, "Za")
        self.assertEqual(get_market("zm").list_prefix, "Zm")

    def test_shared_schema_contains_previously_skipped_headers(self):
        self.assertIn("COMMENT_DATE", SHARED_HEADERS)
        self.assertIn("GENDER", SHARED_HEADERS)

    def test_unsupported_market_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "GH, UG, ZA, or ZM"):
            get_market("NG")

    def test_each_market_is_addressable(self):
        for code in MarketCode:
            self.assertEqual(get_market(code.value).code, code)

    def test_bank_account_number_is_za_specific(self):
        self.assertIn("BANK_ACCOUNT_NUMBER", get_market("ZA").allowed_headers)
        self.assertNotIn("BANK_ACCOUNT_NUMBER", get_market("GH").allowed_headers)
        self.assertNotIn("BANK_ACCOUNT_NUMBER", get_market("UG").allowed_headers)
        self.assertNotIn("BANK_ACCOUNT_NUMBER", get_market("ZM").allowed_headers)


if __name__ == "__main__":
    unittest.main()
