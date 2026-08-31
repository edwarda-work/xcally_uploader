import tempfile
import unittest
from pathlib import Path

from app.domain import get_market
from app.xcally import build_binding, normalize_alias, read_headers


class CsvValidationTests(unittest.TestCase):
    def test_reads_utf8_bom_and_trims_headers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.csv"
            path.write_text("\ufeffCLIENT_ID, FIRSTNAME\n1,Ama\n", encoding="utf-8")
            self.assertEqual(read_headers(path), ["CLIENT_ID", "FIRSTNAME"])

    def test_duplicate_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.csv"
            path.write_text("CLIENT_ID,CLIENT_ID\n1,2\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                read_headers(path)

    def test_empty_headers_are_allowed_for_legacy_compatibility(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contacts.csv"
            path.write_text("CLIENT_ID,,FIRSTNAME,\n1,unused,Ama,\n")
            self.assertEqual(read_headers(path), ["CLIENT_ID", "", "FIRSTNAME", ""])


class BindingTests(unittest.TestCase):
    def test_direct_custom_and_unknown_headers(self):
        fields = [{"id": 41, "alias": "Client Id"}, {"id": 42, "alias": "Gender"}]
        binding, skipped = build_binding(
            ["FIRSTNAME", "CLIENT_ID", "GENDER", "UNKNOWN"], fields, get_market("GH")
        )
        self.assertEqual(binding, {"firstName": "FIRSTNAME", "cf_41": "CLIENT_ID", "cf_42": "GENDER"})
        self.assertEqual(skipped, ["UNKNOWN"])

    def test_alias_normalization(self):
        self.assertEqual(normalize_alias("  Campaign Name  "), "campaign_name")

    def test_unknown_headers_are_reported_as_skipped(self):
        binding, skipped = build_binding(
            ["FIRSTNAME", "IS_UNREACHABLE"], [], get_market("GH")
        )
        self.assertEqual(binding, {"firstName": "FIRSTNAME"})
        self.assertEqual(skipped, ["IS_UNREACHABLE"])

    def test_empty_headers_are_ignored(self):
        binding, skipped = build_binding(["FIRSTNAME", ""], [], get_market("ZA"))
        self.assertEqual(binding, {"firstName": "FIRSTNAME"})
        self.assertEqual(skipped, [])


if __name__ == "__main__":
    unittest.main()
