import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.domain import MARKETS, get_market
from app.xcally import build_binding, check_column_targets, phone_value_error, validate_phone_columns

# Contact item types: the "Additional phones" rows in the xCALLY import screen.
ITEM_TYPES = [
    {"id": 1, "name": "Disbursement Wallet"},
    {"id": 2, "name": "Margin Number 1"},
    {"id": 3, "name": "Margin Number 2"},
    {"id": 4, "name": "Reference Number 1"},
]
# A list custom field with the same name must NOT receive the wallet number.
FIELDS = [{"id": 71, "alias": "Margin Number 1"}]


class WalletMappingTests(unittest.TestCase):
    def test_wallet_columns_map_to_additional_phones(self):
        binding, skipped = build_binding(
            ["MOBILE_PHONE", "DISBURSEMENT_WALLET", "WALLET_1", "WALLET_2"], FIELDS, get_market("GH"), ITEM_TYPES)
        # Same shape the xCALLY import screen sends: item type name -> CSV column.
        self.assertEqual(binding, {"phone": "MOBILE_PHONE", "Disbursement Wallet": "DISBURSEMENT_WALLET",
                                   "Margin Number 1": "WALLET_1", "Margin Number 2": "WALLET_2"})
        self.assertEqual(skipped, [])

    def test_item_type_name_is_matched_loosely_but_sent_exactly(self):
        binding, _ = build_binding(["WALLET_1"], [], get_market("GH"), [{"id": 2, "name": "margin number  1"}])
        self.assertEqual(binding, {"margin number  1": "WALLET_1"})

    def test_missing_item_type_is_reported_as_skipped(self):
        binding, skipped = build_binding(["MOBILE_PHONE", "WALLET_2"], FIELDS, get_market("GH"), ITEM_TYPES[:2])
        self.assertEqual(binding, {"phone": "MOBILE_PHONE"})
        self.assertEqual(skipped, ["WALLET_2"])

    def test_every_market_accepts_wallet_columns(self):
        for market in MARKETS.values():
            self.assertTrue({"DISBURSEMENT_WALLET", "WALLET_1", "WALLET_2"} <= market.allowed_headers, market.code)

    def test_margin_columns_are_skipped_not_uploaded(self):
        headers = ["MOBILE_PHONE", "MARGIN_PHONENUMBER1", "MARGIN_PHONENUMBER2",
                   "DISBURSEMENT_WALLET", "WALLET_1", "WALLET_2"]
        market = get_market("GH")
        check_column_targets(headers, market)
        binding, skipped = build_binding(headers, FIELDS, market, ITEM_TYPES)
        self.assertEqual(binding["Margin Number 1"], "WALLET_1")
        self.assertEqual(binding["Margin Number 2"], "WALLET_2")
        self.assertEqual(skipped, ["MARGIN_PHONENUMBER1", "MARGIN_PHONENUMBER2"])

    def test_two_columns_for_one_field_are_rejected_before_upload(self):
        market = replace(get_market("GH"), additional_phone_bindings={
            **get_market("GH").additional_phone_bindings, "REF_PHONE_1": "Margin Number 1"})
        with self.assertRaisesRegex(ValueError, "WALLET_1 and REF_PHONE_1 both map"):
            check_column_targets(["WALLET_1", "REF_PHONE_1"], market)
        with self.assertRaises(ValueError):
            build_binding(["WALLET_1", "REF_PHONE_1"], FIELDS, market, ITEM_TYPES)


class ScientificNotationTests(unittest.TestCase):
    def test_phone_value_error(self):
        self.assertIn("scientific notation", phone_value_error("WALLET_1", " 2.33553E+11 ", 4))
        self.assertIn("line 4", phone_value_error("DISBURSEMENT_WALLET", "2.3354e11", 4))
        self.assertIsNone(phone_value_error("WALLET_1", "233553123456", 4))
        self.assertIsNone(phone_value_error("WALLET_1", "", 4))
        self.assertIsNone(phone_value_error("LOAN_AMOUNT", "2.5E+3", 4))

    def write(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "contacts.csv"
        path.write_text(text, encoding="utf-8")
        return path

    def test_file_with_shortened_wallet_is_rejected(self):
        path = self.write("MOBILE_PHONE,WALLET_1\n233553123456,233553123456\n233243000000,2.33243E+11\n")
        with self.assertRaisesRegex(ValueError, "WALLET_1 at CSV line 3"):
            validate_phone_columns(path)

    def test_clean_file_passes(self):
        validate_phone_columns(self.write("MOBILE_PHONE,WALLET_1,WALLET_2\n233553123456,233553123456,\n"))


if __name__ == "__main__":
    unittest.main()


class ItemTypeFetchTests(unittest.TestCase):
    def test_client_reads_contact_item_types(self):
        from unittest.mock import MagicMock
        from app.settings import Settings
        from app.xcally import XcallyClient
        client = XcallyClient(Settings('https://example.invalid', '/api', True, '', '', 1, 1, 1, 1, 1), 'user', 'pass')
        self.addCleanup(client.close)
        client.collection = MagicMock(return_value=[{"id": 2, "name": "Margin Number 1", "OrderBy": 1}])
        self.assertEqual(client.contact_item_types(), [{"id": 2, "name": "Margin Number 1"}])
        client.collection.assert_called_once_with("/contact_item_types", fields="id,name")

    def test_upload_reads_item_types_before_creating_list_and_sends_names(self):
        from unittest.mock import MagicMock, patch
        from app.service import process_file
        from app.settings import Settings
        path = ScientificNotationTests.write(self, "FIRSTNAME,MOBILE_PHONE,WALLET_1\nAma,233553123456,233243123456\n")
        client = MagicMock()
        client.contact_item_types.return_value = ITEM_TYPES
        client.create_list.return_value = {"id": 9}
        client.fetch_list_fields.return_value = FIELDS
        client.stage_upload.return_value = {"file": {"name": "staged.csv"}}
        client.finalize_upload.return_value = {"pid": 1}
        settings = Settings('https://example.invalid', '/api', True, '', '', 1, 1, 60000000, 500)
        with patch("app.service.XcallyClient", return_value=client):
            result = process_file(path, "c.csv", get_market("GH"), "u", "p", settings)
        self.assertEqual(result.status, "success", result.message)
        calls = [c[0] for c in client.method_calls]
        self.assertLess(calls.index("contact_item_types"), calls.index("create_list"))
        self.assertEqual(client.finalize_upload.call_args.args[2]["Margin Number 1"], "WALLET_1")
        self.assertNotIn("cf_71", client.finalize_upload.call_args.args[2])
