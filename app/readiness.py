"""Is a newly uploaded list ready to add to a campaign?

xCALLY imports in the background and in stages: contacts first, then their
Additional phones a little later. Its progress is only pushed to its own web
screen over a socket, so readiness is worked out by checking xCALLY directly:

1. Contacts: the list's contact count reaches the CSV's row count.
2. Additional phones: a few sample contacts from the end of the CSV (the last
   to be processed) have all the phones they should have.

The sample contacts' main phone numbers are kept in memory only, never on
disk, and are forgotten when the app restarts. After a restart only the
contact stage can be checked.
"""

from __future__ import annotations

import csv
import re
import threading
import time
from collections import Counter, OrderedDict
from pathlib import Path

from .xcally import XcallyError

SAMPLE_SIZE = 5
REGISTRY_LIMIT = 200


def _digits(value: str | None) -> str:
    return re.sub(r"\D", "", value or "")


def expected_profile(csv_path: Path, phone_headers: list[str]) -> dict:
    """What xCALLY should end up with for this CSV.

    phone_headers are the CSV columns bound to Additional phones. xCALLY keeps
    each number once per contact, so the expected count per contact is the
    number of distinct non-empty values across those columns.
    """
    contacts = with_phones = 0
    candidates: list[tuple[str, int]] = []
    main_counts: Counter[str] = Counter()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not any((value or "").strip() for value in row.values() if isinstance(value, str)):
                continue
            contacts += 1
            main = (row.get("MOBILE_PHONE") or "").strip()
            main_counts[main] += 1
            expected = len({_digits(row.get(h)) for h in phone_headers if _digits(row.get(h))})
            if expected:
                with_phones += 1
                if main:
                    candidates.append((main, expected))
                    del candidates[:-SAMPLE_SIZE * 4]
    # A main phone used by two contacts cannot identify one contact.
    unique = [(main, expected) for main, expected in candidates if main_counts[main] == 1]
    samples = [{"phone": main, "expected": expected} for main, expected in unique[-SAMPLE_SIZE:]]
    return {"expected_contacts": contacts, "expected_with_phones": with_phones, "samples": samples}


def public_summary(profile: dict) -> dict:
    """The part that may be stored in history: counts only, no phone numbers."""
    return {"expected_contacts": profile["expected_contacts"], "expected_with_phones": profile["expected_with_phones"]}


def bound_phone_headers(binding: dict[str, str], additional_phone_bindings: dict[str, str]) -> list[str]:
    bound = set(binding.values())
    return [header for header in additional_phone_bindings if header in bound]


class ReadinessRegistry:
    """In-memory sample contacts per list, newest uploads kept."""

    def __init__(self, limit: int = REGISTRY_LIMIT):
        self._items: OrderedDict[int, dict] = OrderedDict()
        self._lock = threading.Lock()
        self._limit = limit

    def register(self, list_id: int, profile: dict) -> None:
        with self._lock:
            self._items[list_id] = profile
            self._items.move_to_end(list_id)
            while len(self._items) > self._limit:
                self._items.popitem(last=False)

    def get(self, list_id: int) -> dict | None:
        with self._lock:
            return self._items.get(list_id)


registry = ReadinessRegistry()


def check_phones(client, list_id: int, samples: list[dict]) -> dict:
    """How many sample contacts already have all their expected Additional phones."""
    found = ready = 0
    for sample in samples:
        matches = [row for row in client.contacts_by_phone(list_id, sample["phone"])
                   if (row.get("phone") or "").strip() == sample["phone"]]
        if len(matches) != 1:
            continue
        found += 1
        stored = {_digits(item.get("item")) for item in client.contact_phones(matches[0]["id"]) if _digits(item.get("item"))}
        ready += len(stored) >= sample["expected"]
    return {"samples": len(samples), "found": found, "ready": ready,
            "complete": bool(samples) and ready == len(samples)}


def check_readiness(client, list_id: int, profile: dict | None, expected_contacts: int | None = None) -> dict:
    expected = profile["expected_contacts"] if profile else expected_contacts
    count = client.contact_count(list_id)
    result = {"list_id": list_id, "contacts": count, "expected_contacts": expected,
              "expected_with_phones": profile["expected_with_phones"] if profile else None,
              "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if expected is not None and count < expected:
        return {**result, "stage": "contacts", "ready": False,
                "message": f"Importing contacts: {count:,} of {expected:,}."}
    if profile is None:
        return {**result, "stage": "unknown", "ready": None,
                "message": f"{count:,} contacts imported. Additional phones can't be checked because the app restarted "
                           "after this upload; allow a few minutes before adding it to a campaign."}
    if not profile["samples"]:
        note = " No additional phones were expected." if not profile["expected_with_phones"] else ""
        return {**result, "stage": "ready", "ready": True, "message": f"Ready: {count:,} contacts imported.{note}"}
    phones = check_phones(client, list_id, profile["samples"])
    if phones["complete"]:
        return {**result, "stage": "ready", "ready": True, "phones": phones,
                "message": f"Ready: {count:,} contacts imported and additional phones added."}
    if not phones["found"]:
        return {**result, "stage": "phones", "ready": False, "phones": phones,
                "message": "Contacts imported. Sample contacts could not be located yet to check additional phones."}
    return {**result, "stage": "phones", "ready": False, "phones": phones,
            "message": f"Contacts imported. Adding additional phones ({phones['ready']} of {phones['samples']} sample contacts done)."}


def wait_for_phones(client, list_id: int, profile: dict, timeout_seconds: float, sleep=time.sleep) -> None:
    """Block until the sample contacts have their phones, for campaign list swaps."""
    if not profile["samples"]:
        return
    deadline = time.monotonic() + timeout_seconds
    while True:
        phones = check_phones(client, list_id, profile["samples"])
        if phones["complete"]:
            return
        if time.monotonic() >= deadline:
            raise XcallyError(
                f"Contacts were imported but additional phones were not confirmed within {int(timeout_seconds)} seconds "
                f"({phones['ready']} of {phones['samples']} sample contacts done). Existing campaign lists were not changed; "
                "check the new list in xCALLY.")
        sleep(min(3, max(0, deadline - time.monotonic())))
