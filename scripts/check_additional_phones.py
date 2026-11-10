"""Read-only check: what did xCALLY store as Additional phones for a list's contacts?

Usage (from the project folder):
    .venv/bin/python scripts/check_additional_phones.py 10037

Prompts for xCALLY credentials. Nothing is created or changed in xCALLY.
Phone numbers are masked to their last 3 digits.
"""

import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.paths import data_directory  # noqa: E402
from app.settings import Settings  # noqa: E402
from app.xcally import XcallyClient  # noqa: E402


def mask(value):
    text = str(value or "")
    return f"…{text[-3:]}" if len(text) > 3 else text


def main() -> None:
    list_id = int(sys.argv[1]) if len(sys.argv) > 1 else int(input("List ID: "))
    username = input("xCALLY username: ").strip()
    password = getpass.getpass("xCALLY password (hidden): ")
    client = XcallyClient(Settings.from_environment(data_directory()), username, password)
    try:
        print("\n1) Contact item types (Additional phone types):")
        for row in client.json_request("GET", "/contact_item_types", params={"limit": 100}).get("rows", []):
            print(f"   id={row.get('id')}  name={row.get('name')!r}")

        page = client.json_request("GET", "/cm/contacts", params={"ListId": list_id, "limit": 3, "sort": "id"})
        contacts = page.get("rows", []) if isinstance(page, dict) else page
        print(f"\n2) First {len(contacts)} contacts in list {list_id} (total reported: {page.get('count') if isinstance(page, dict) else '?'})")
        for contact in contacts:
            contact_id = contact.get("id")
            print(f"\n   Contact {contact_id}  ListId={contact.get('ListId')}  phone={mask(contact.get('phone'))}")
            print(f"   AdditionalPhones field: {contact.get('AdditionalPhones')!r}")
            wallet_like = {k: mask(v) for k, v in contact.items() if k.startswith("cf_") and v}
            print(f"   Custom fields with values: {wallet_like or 'none'}")
            phones = client.json_request("GET", f"/cm/contacts/{contact_id}/phones")
            rows = phones if isinstance(phones, list) else phones.get("rows", [])
            print(f"   /phones returned {len(rows)} item(s):")
            for item in rows:
                print(f"     ItemTypeId={item.get('ItemTypeId')}  item={mask(item.get('item'))}  description={item.get('description')!r}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
