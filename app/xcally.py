from __future__ import annotations

import csv
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

import requests
from requests.auth import HTTPBasicAuth
from urllib3.exceptions import InsecureRequestWarning

from .domain import MarketConfig
from .settings import Settings


FLOW_CHUNK_SIZE = 62_914_560


class XcallyError(RuntimeError):
    pass


def normalize_alias(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    text = text.strip().lower()
    chars: list[str] = []
    last_was_separator = False
    for char in text:
        if char.isalnum():
            chars.append(char)
            last_was_separator = False
        elif not last_was_separator:
            chars.append("_")
            last_was_separator = True
    return "".join(chars).strip("_")


def read_headers(csv_path: Path) -> list[str]:
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            headers = next(csv.reader(handle), None)
    except UnicodeDecodeError as exc:
        raise ValueError("CSV must use UTF-8 encoding.") from exc
    if not headers:
        raise ValueError("CSV file is empty.")
    cleaned = [header.strip() for header in headers]
    named_headers = [header for header in cleaned if header]
    if len(named_headers) != len(set(named_headers)):
        raise ValueError("CSV contains duplicate headers.")
    return cleaned


def build_binding(
    headers: Iterable[str], field_rows: Iterable[dict[str, Any]], market: MarketConfig
) -> tuple[dict[str, str], list[str]]:
    alias_to_field_id: dict[str, int] = {}
    for row in field_rows:
        alias = row.get("alias") or row.get("name")
        field_id = row.get("id")
        if alias and isinstance(field_id, int):
            alias_to_field_id[normalize_alias(alias)] = field_id

    binding: dict[str, str] = {}
    skipped: list[str] = []
    for header in headers:
        if not header:
            continue
        if header not in market.allowed_headers:
            skipped.append(header)
            continue
        if header in market.direct_bindings:
            binding[market.direct_bindings[header]] = header
            continue
        candidates = []
        if header in market.alias_overrides:
            candidates.append(normalize_alias(market.alias_overrides[header]))
        candidates.append(normalize_alias(header))
        field_id = next((alias_to_field_id[c] for c in candidates if c in alias_to_field_id), None)
        if field_id is None:
            skipped.append(header)
        else:
            binding[f"cf_{field_id}"] = header

    for header, aliases in market.duplicate_alias_bindings.items():
        if header not in headers:
            continue
        for alias in aliases:
            field_id = alias_to_field_id.get(normalize_alias(alias))
            if field_id:
                binding[f"cf_{field_id}"] = header
    return binding, skipped


class XcallyClient:
    def __init__(self, settings: Settings, username: str, password: str):
        self.settings = settings
        if settings.verify_ssl is False:
            requests.packages.urllib3.disable_warnings(category=InsecureRequestWarning)
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.session.headers.update({"Accept": "application/json"})

    def request(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        try:
            return self.session.request(
                method=method.upper(),
                url=f"{self.settings.base_url}{self.settings.api_prefix}{endpoint}",
                timeout=self.settings.request_timeout,
                verify=self.settings.verify_ssl,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise XcallyError("Unable to connect to xCALLY.") from exc

    def create_list(self, list_name: str) -> dict[str, Any]:
        response = self.request("POST", "/cm/lists", json={"name": list_name})
        if response.status_code != 201:
            raise XcallyError(f"xCALLY list creation failed (HTTP {response.status_code}).")
        return response.json()

    def fetch_list_fields(self, list_id: int) -> list[dict[str, Any]]:
        response = self.request("GET", f"/cm/lists/{list_id}/fields", params={"includeGlobals": "true"})
        if not response.ok:
            raise XcallyError(f"Unable to retrieve xCALLY fields (HTTP {response.status_code}).")
        return response.json().get("rows", [])

    def stage_upload(self, csv_path: Path, original_name: str) -> dict[str, Any]:
        file_size = csv_path.stat().st_size
        safe_identifier = "".join(c for c in original_name.lower() if c.isalnum())
        form_data = {
            "flowChunkNumber": "1", "flowChunkSize": str(FLOW_CHUNK_SIZE),
            "flowCurrentChunkSize": str(file_size), "flowTotalSize": str(file_size),
            "flowIdentifier": f"{file_size}-{safe_identifier}", "flowFilename": original_name,
            "flowRelativePath": original_name, "flowTotalChunks": "1",
        }
        with csv_path.open("rb") as handle:
            response = self.request(
                "POST", "/cm/contacts/upload", data=form_data,
                files={"file": (original_name, handle, "text/csv")}, headers={"Accept": "*/*"},
            )
        if not response.ok:
            raise XcallyError(f"xCALLY staging failed (HTTP {response.status_code}).")
        return response.json()

    def finalize_upload(self, list_id: int, staged_id: str, binding: dict[str, str]) -> dict[str, Any]:
        payload = {
            "id": staged_id, "ListId": list_id, "binding": binding,
            "socket_timestamp": int(time.time()),
        }
        response = self.request("POST", f"/cm/contacts/upload/{staged_id}", json=payload)
        if not response.ok:
            raise XcallyError(f"xCALLY finalization failed (HTTP {response.status_code}).")
        return response.json()
