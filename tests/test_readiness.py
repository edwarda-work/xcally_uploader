import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.readiness import (ReadinessRegistry, bound_phone_headers, check_readiness, expected_profile,
                           public_summary, wait_for_phones)
from app.settings import Settings
from app.xcally import XcallyClient, XcallyError

WALLETS = ["DISBURSEMENT_WALLET", "WALLET_1", "WALLET_2"]


def csv_file(test, text):
    directory = tempfile.TemporaryDirectory()
    test.addCleanup(directory.cleanup)
    path = Path(directory.name) / "c.csv"
    path.write_text(text, encoding="utf-8")
    return path


class FakeClient:
    """Contacts keyed by main phone; phones[contact_id] = stored additional numbers."""

    def __init__(self, count, contacts, phones):
        self.count, self.contacts, self.phones = count, contacts, phones

    def contact_count(self, list_id):
        return self.count

    def contacts_by_phone(self, list_id, phone):
        return [{"id": cid, "phone": phone} for p, cid in self.contacts if p == phone]

    def contact_phones(self, contact_id):
        return [{"item": number} for number in self.phones.get(contact_id, [])]


class ProfileTests(unittest.TestCase):
    def test_counts_distinct_numbers_and_samples_last_rows(self):
        path = csv_file(self, "MOBILE_PHONE,DISBURSEMENT_WALLET,WALLET_1,WALLET_2\n"
                              "111,111,111,\n"        # one distinct number
                              "222,333,333,222\n"     # two distinct numbers
                              "444,,,\n"              # no additional phones
                              ",,,\n")                # blank row ignored
        profile = expected_profile(path, WALLETS)
        self.assertEqual(public_summary(profile), {"expected_contacts": 3, "expected_with_phones": 2})
        self.assertEqual(profile["samples"], [{"phone": "111", "expected": 1}, {"phone": "222", "expected": 2}])

    def test_shared_main_phone_is_not_used_as_sample_and_size_is_capped(self):
        rows = "".join(f"{900 + i},{800 + i},,\n" for i in range(12))
        path = csv_file(self, "MOBILE_PHONE,DISBURSEMENT_WALLET,WALLET_1,WALLET_2\n555,1,,\n555,2,,\n" + rows)
        samples = expected_profile(path, WALLETS)["samples"]
        self.assertEqual([s["phone"] for s in samples], ["907", "908", "909", "910", "911"])

    def test_only_bound_columns_count(self):
        self.assertEqual(bound_phone_headers({"phone": "MOBILE_PHONE", "Margin Number 1": "WALLET_1"},
                                             {"DISBURSEMENT_WALLET": "x", "WALLET_1": "y"}), ["WALLET_1"])
        path = csv_file(self, "MOBILE_PHONE,DISBURSEMENT_WALLET,WALLET_1\n111,222,\n")
        self.assertEqual(expected_profile(path, ["WALLET_1"])["samples"], [])


class CheckTests(unittest.TestCase):
    PROFILE = {"expected_contacts": 3, "expected_with_phones": 2,
               "samples": [{"phone": "111", "expected": 1}, {"phone": "222", "expected": 2}]}

    def test_contacts_stage(self):
        result = check_readiness(FakeClient(1, [], {}), 9, self.PROFILE)
        self.assertEqual((result["stage"], result["ready"]), ("contacts", False))
        self.assertIn("1 of 3", result["message"])

    def test_phones_stage_then_ready(self):
        client = FakeClient(3, [("111", 1), ("222", 2)], {1: ["111"], 2: ["333"]})
        result = check_readiness(client, 9, self.PROFILE)
        self.assertEqual((result["stage"], result["ready"]), ("phones", False))
        self.assertIn("1 of 2", result["message"])
        client.phones[2] = ["333", "222"]
        result = check_readiness(client, 9, self.PROFILE)
        self.assertEqual((result["stage"], result["ready"]), ("ready", True))

    def test_samples_not_found_are_not_ready(self):
        result = check_readiness(FakeClient(3, [], {}), 9, self.PROFILE)
        self.assertFalse(result["ready"])
        self.assertIn("could not be located", result["message"])

    def test_no_expected_phones_is_ready_after_contacts(self):
        profile = {"expected_contacts": 2, "expected_with_phones": 0, "samples": []}
        self.assertTrue(check_readiness(FakeClient(2, [], {}), 9, profile)["ready"])

    def test_unknown_after_restart_checks_contacts_only(self):
        self.assertEqual(check_readiness(FakeClient(1, [], {}), 9, None, expected_contacts=3)["stage"], "contacts")
        result = check_readiness(FakeClient(3, [], {}), 9, None, expected_contacts=3)
        self.assertEqual((result["stage"], result["ready"]), ("unknown", None))

    def test_wait_for_phones(self):
        client = FakeClient(3, [("111", 1), ("222", 2)], {1: ["111"], 2: ["333"]})
        sleeps = []
        def sleep(seconds):
            sleeps.append(seconds)
            client.phones[2] = ["333", "222"]
        wait_for_phones(client, 9, self.PROFILE, 30, sleep=sleep)
        self.assertEqual(len(sleeps), 1)
        with self.assertRaisesRegex(XcallyError, "not confirmed"):
            wait_for_phones(FakeClient(3, [], {}), 9, self.PROFILE, 0, sleep=lambda s: None)
        wait_for_phones(FakeClient(3, [], {}), 9, {**self.PROFILE, "samples": []}, 0)

    def test_registry_keeps_newest(self):
        registry = ReadinessRegistry(limit=2)
        for list_id in (1, 2, 3):
            registry.register(list_id, {"n": list_id})
        self.assertIsNone(registry.get(1))
        self.assertEqual(registry.get(3), {"n": 3})


class ClientMethodTests(unittest.TestCase):
    def setUp(self):
        self.client = XcallyClient(Settings('https://example.invalid', '/api', True, '', '', 1, 1, 1, 1, 1), 'u', 'p')
        self.addCleanup(self.client.close)

    def test_contact_count_reads_count_or_content_range(self):
        response = MagicMock(ok=True, headers={})
        response.json.return_value = {"count": 42, "rows": []}
        self.client.request = MagicMock(return_value=response)
        self.assertEqual(self.client.contact_count(7), 42)
        self.assertEqual(self.client.request.call_args.kwargs["params"]["ListId"], 7)
        response.json.return_value = []
        response.headers = {"Content-Range": "0-0/13"}
        self.assertEqual(self.client.contact_count(7), 13)

    def test_contacts_by_phone_is_scoped_to_list(self):
        self.client.json_request = MagicMock(return_value={"rows": [{"id": 1, "phone": "111", "ListId": 7}]})
        self.assertEqual(len(self.client.contacts_by_phone(7, "111")), 1)
        self.assertEqual(self.client.json_request.call_args.kwargs["params"]["phone"], "111")
        self.client.json_request.return_value = {"rows": [{"id": 1, "phone": "111", "ListId": 8}]}
        with self.assertRaises(XcallyError):
            self.client.contacts_by_phone(7, "111")


if __name__ == "__main__":
    unittest.main()
