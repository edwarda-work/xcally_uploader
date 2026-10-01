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

    def close(self) -> None:
        self.session.close()

    def json_request(self, method: str, endpoint: str, **kwargs: Any) -> Any:
        response = self.request(method, endpoint, **kwargs)
        if not response.ok:
            if response.status_code in (401, 403):
                raise XcallyError(f"xCALLY rejected access (HTTP {response.status_code}). Check credentials and campaign permissions.")
            raise XcallyError(f"xCALLY request failed (HTTP {response.status_code}) for {endpoint}.")
        try:
            return response.json()
        except ValueError as exc:
            raise XcallyError("xCALLY returned an invalid JSON response.") from exc

    @staticmethod
    def rows(data: Any) -> list[dict]:
        rows = data if isinstance(data, list) else data.get("rows") if isinstance(data, dict) else None
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise XcallyError("xCALLY returned an unexpected collection format.")
        return rows

    def collection(self, endpoint: str, **params: Any) -> list[dict]:
        result: list[dict] = []
        seen: set[int] = set()
        offset = 0
        for _ in range(1000):
            response = self.request("GET", endpoint, params={**params, "limit": 100, "offset": offset, "sort": "id"})
            if not response.ok:
                raise XcallyError(f"Unable to read {endpoint} (HTTP {response.status_code}). Check xCALLY permissions.")
            try:
                data = response.json()
            except ValueError as exc:
                raise XcallyError("xCALLY returned invalid JSON.") from exc
            rows = self.rows(data)
            if not rows:
                return result
            for row in rows:
                if type(row.get("id")) is not int or row["id"] in seen:
                    raise XcallyError("xCALLY pagination returned invalid or repeated IDs.")
                seen.add(row["id"])
            result.extend(rows)
            offset += len(rows)
            count = data.get("count") if isinstance(data, dict) else None
            if count is None:
                count = response.headers.get("Content-Range", "").rsplit("/", 1)[-1]
            if str(count).isdigit() and offset >= int(count):
                return result
            if count in (None, "") and len(rows) < 100:
                return result
        raise XcallyError("xCALLY returned too many pages.")

    def agents(self) -> list[dict]:
        return [{key: row.get(key) for key in ('id', 'name', 'fullname', 'role')}
                for row in self.collection("/users", fields="id,name,fullname,role", role="agent") if row.get("role") == "agent"]

    def campaigns(self, active_only: bool = False) -> list[dict]:
        params = {'fields': 'id,name,type,dialMethod,TrunkId,dialTimezone,dialActive', 'type': 'outbound'}
        if active_only:
            params['dialActive'] = True
        rows = self.collection('/voice/queues', **params)
        return [{key: row.get(key) for key in ('id', 'name', 'type', 'dialMethod', 'TrunkId', 'dialTimezone', 'dialActive')}
                for row in rows if row.get('type') == 'outbound' and
                (not active_only or row.get('dialActive') in (True, 1))]

    def queue(self, queue_id: int) -> dict:
        data = self.json_request("GET", f"/voice/queues/{queue_id}")
        if not isinstance(data, dict) or data.get("id") != queue_id:
            raise XcallyError("xCALLY did not return the requested campaign.")
        return data

    def assign_list(self, queue_id: int, list_id: int) -> None:
        self.json_request("POST", f"/voice/queues/{queue_id}/lists", json={"id": queue_id, "ids": [list_id]})

    def detach_lists(self, queue_id: int, list_ids: list[int]) -> None:
        # Angular $resource DELETE uses query parameters, as in the installed UI.
        if list_ids:
            response = self.request("DELETE", f"/voice/queues/{queue_id}/lists", params={"ids": list_ids})
            if not response.ok:
                raise XcallyError(f"Unable to detach campaign lists (HTTP {response.status_code}).")

    def set_campaign_active(self, queue_id: int, active: bool) -> None:
        self.json_request("PUT", f"/voice/queues/{queue_id}", json={"dialActive": active})
        if self.queue(queue_id).get('dialActive') != active:
            raise XcallyError('Campaign active status could not be verified.')

    def verify_agent(self, queue_id: int, agent_id: int) -> None:
        members = self.collection(f"/voice/queues/{queue_id}/users")
        teams = self.collection(f"/voice/queues/{queue_id}/teams")
        if {row['id'] for row in members} != {agent_id} or teams:
            raise XcallyError('The campaign must already contain only its designated agent, with no shared teams. Check its assignments in xCALLY.')

    def list_ids(self, queue_id: int) -> set[int]:
        return {row['id'] for row in self.collection(f"/voice/queues/{queue_id}/lists")}

    def campaign_monitor_snapshot(self, queue_id: int) -> dict:
        """Read only campaign configuration available through verified API routes."""
        queue = self.queue(queue_id)
        if queue.get('type') != 'outbound':
            raise XcallyError('The selected campaign is not outbound.')
        users = self.collection(f'/voice/queues/{queue_id}/users')
        lists = self.collection(f'/voice/queues/{queue_id}/lists')
        snapshot = {
            'id': queue_id,
            'name': queue.get('name'),
            'active': bool(queue['dialActive']) if queue.get('dialActive') in (True, False, 0, 1) else None,
            'agents': [{'id': row['id'], 'name': row.get('fullname') or row.get('name') or str(row['id'])} for row in users],
            'list_count': len(lists),
        }
        snapshot['agents'], error = self.assigned_agent_presence(users)
        if error:
            snapshot['agents_error'] = error
        known_online = all(agent['online'] is not None for agent in snapshot['agents'])
        known_voice = known_online and all(agent['online'] is False or
                                            agent['voice_status'] in {'idle', 'talking', 'ringing', 'pause', 'unavailable'}
                                            for agent in snapshot['agents'])
        snapshot['realtime'] = {
            'logged_in': sum(agent['online'] is True for agent in snapshot['agents']) if known_online else None,
            'available': sum(agent['online'] is True and agent['voice_status'] == 'idle' for agent in snapshot['agents']) if known_voice else None,
            'talking': sum(agent['online'] is True and agent['voice_status'] == 'talking' for agent in snapshot['agents']) if known_voice else None,
            'ringing': sum(agent['online'] is True and agent['voice_status'] == 'ringing' for agent in snapshot['agents']) if known_voice else None,
        }
        try:
            snapshot['recent_calls'] = self.campaign_recent_calls(queue_id)
        except XcallyError:
            snapshot['calls_error'] = 'xCALLY call history is unavailable.'
        return snapshot

    def campaign_agent_status(self, queue_id: int) -> dict:
        queue = self.queue(queue_id)
        if queue.get('type') != 'outbound':
            raise XcallyError('The selected campaign is not outbound.')
        assigned = self.collection(f'/voice/queues/{queue_id}/users')
        agents, error = self.assigned_agent_presence(assigned)
        result = {'id': queue_id, 'name': queue.get('name'), 'agents': agents}
        if error:
            result['error'] = error
        return result

    def assigned_agent_presence(self, assigned: list[dict]) -> tuple[list[dict], str | None]:
        agents = [{'id': row['id'], 'name': row.get('fullname') or row.get('name') or str(row['id']),
                   'online': None, 'voice_status': None} for row in assigned]
        if not agents:
            return agents, None
        error = None
        try:
            realtime = {row['id']: row for row in self.realtime_agents()}
            matched = 0
            for agent in agents:
                row = realtime.get(agent['id'])
                if row is not None:
                    matched += 1
                    agent['online'] = row.get('online') if type(row.get('online')) is bool else None
                    agent['voice_status'] = row.get('voiceStatus')
            if agents and matched == 0:
                error = 'None of this campaign’s assigned agents appeared in xCALLY realtime agents.'
            elif matched < len(agents):
                error = f'Only {matched} of {len(agents)} assigned agents appeared in xCALLY realtime agents.'
        except XcallyError as exc:
            error = str(exc)
        return agents, error

    def realtime_agents(self) -> list[dict]:
        response = self.request('GET', '/realtime/agents', params={
            'channel': 'voice', 'globalStatusFilter': 'null', 'nolimit': 'true',
            'pauseTypeFilter': 'null', 'sort': 'fullname',
        })
        if not response.ok:
            raise XcallyError(f'Unable to read /realtime/agents (HTTP {response.status_code}).')
        try:
            data = response.json()
        except ValueError as exc:
            raise XcallyError('xCALLY returned invalid realtime agent data.') from exc
        rows = self.rows(data)
        count = data.get('count') if isinstance(data, dict) else None
        if type(count) is int and len(rows) < count:
            raise XcallyError(f'xCALLY returned only {len(rows)} of {count} realtime agents.')
        return rows

    def campaign_recent_calls(self, queue_id: int) -> list[dict]:
        """Read a bounded, newest-first page of Hopper History for one queue."""
        response = self.request('GET', f'/voice/queues/{queue_id}/hopper_histories', params={
            'VoiceQueueId': queue_id,
            'fields': 'VoiceQueueId,statedesc,starttime,endtime',
            'limit': 10, 'offset': 0, 'page': 1, 'sort': '-id',
        })
        if not response.ok:
            raise XcallyError(f'Unable to read campaign call history (HTTP {response.status_code}).')
        try:
            data = response.json()
        except ValueError as exc:
            raise XcallyError('xCALLY returned invalid campaign call history.') from exc
        rows = self.rows(data)
        if len(rows) > 10:
            raise XcallyError('xCALLY returned too many call history rows.')
        result = []
        for row in rows:
            if row.get('VoiceQueueId') != queue_id:
                raise XcallyError('xCALLY returned call history for a different campaign.')
            result.append({
                'status': row.get('statedesc'),
                'started_at': row.get('starttime'),
                'ended_at': row.get('endtime'),
            })
        return result

    def wait_for_import(self, list_id: int, expected_count: int) -> None:
        deadline = time.monotonic() + self.settings.import_wait_seconds
        while True:
            remaining = max(1, deadline - time.monotonic())
            # The list association endpoint returns a bare array. The contacts
            # collection is the paginated/countable endpoint used by Motion UI.
            response = self.request('GET', '/cm/contacts',
                                    params={'ListId': list_id, 'limit': 1, 'fields': 'id'},
                                    timeout=min(self.settings.request_timeout, remaining))
            if not response.ok:
                raise XcallyError(f'Unable to verify imported contacts (HTTP {response.status_code}). Existing campaign lists were not changed.')
            try:
                data = response.json()
            except ValueError as exc:
                raise XcallyError('xCALLY returned invalid contact data. Existing campaign lists were not changed.') from exc
            count = data.get('count') if isinstance(data, dict) else None
            if count is None:
                count = response.headers.get('Content-Range', '').rsplit('/', 1)[-1]
            if isinstance(count, str) and count.isdigit():
                count = int(count)
            if type(count) is not int or count < 0:
                raise XcallyError('Contact count could not be verified from xCALLY pagination metadata. Existing campaign lists were not changed.')
            if count == expected_count:
                return
            if count > expected_count or time.monotonic() >= deadline:
                raise XcallyError(f'Import verification found {count} of {expected_count} contacts. Existing campaign lists were not changed; inspect the new list in xCALLY.')
            time.sleep(min(2, max(0, deadline - time.monotonic())))

    def request(self, method: str, endpoint: str, **kwargs: Any) -> requests.Response:
        endpoint = endpoint if endpoint.startswith("/") else f"/{endpoint}"
        try:
            return self.session.request(
                method=method.upper(),
                url=f"{self.settings.base_url}{self.settings.api_prefix}{endpoint}",
                timeout=kwargs.pop("timeout", self.settings.request_timeout),
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
