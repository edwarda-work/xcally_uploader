# xCALLY Upload Automation

A market-aware automation service for validating CSV contact lists and uploading them to xCALLY. Market is local metadata only: it selects the list-name prefix and trusted custom-field schema. It is not sent to xCALLY.

## Split by agent

The **Split by agent** tab uploads one replacement list per agent to an existing
outbound campaign named `MARKET_AGENT_QUEUE_USERNAME` (for example,
`GH_AGENT_QUEUE_ZEINABE`). Load agents and campaigns, confirm the CSV names map to
real xCALLY usernames, and click **Upload & replace campaign lists**.

The workflow validates all rows and agent/campaign matches before writing to
xCALLY. Each campaign must already contain only its designated agent and no shared
teams. It uploads a fresh list and waits for its contact count to equal the CSV
segment count before changing campaign associations. Verification uses the contacts
collection filtered by `ListId`, accepting JSON counts or `Content-Range` totals.
New list names use market, date, base name, and agent, without a batch UUID; a short
numeric suffix is added only when a name already exists. An incomplete or unverifiable
import leaves the campaign untouched. The default import verification window is
60 seconds (`XCALLY_IMPORT_WAIT_SECONDS`).

Active campaigns are temporarily paused. All existing contact-list associations
are detached, then the new list is attached and verified. Detaching first avoids
having old and new contacts together during xCALLY's duplicate checks. Existing
campaign duplicate/retry rules still apply; this does not reset historical call
outcomes. A successful swap restores the prior active status. Existing agents,
blacklists, dispositions, and calling configuration are retained. No campaign is
created, and no contact list or contact is deleted from Contacts Manager.

A failed swap is not automatically retried or resumed. The result records the old
list IDs, new list ID, campaign ID, failing step, and whether the campaign may be
paused. Review associations in xCALLY before resuming. Association changes are not
transactional, and pausing does not cancel calls already in progress.

Batch status is stored in `campaign_batches.sqlite3`, without credentials or CSV
rows. The browser remembers the latest batch ID for status recovery after refresh.
Repeated submissions with the same ID do not rerun the workflow; simultaneous
batches targeting the same campaign are rejected. Use one application process;
restart recovery marks unfinished batches interrupted for operator review.
Per-agent results are also added to upload history. Batch records are retained
separately when upload history is cleared.

API routes: `POST /api/segments/preview`, `POST /api/campaigns/options`,
`POST /api/campaign-batches`, and `GET /api/campaign-batches/{id}`. Options and
submission use multipart credentials or configured server credentials. Submission
accepts `file`, `market`, `agent_column`, `assignments` (JSON mapping CSV names to
agent IDs), `batch_id` (UUID), and optional `base_name`.

## Campaign performance

The Campaign Performance tab loads only active outbound campaign names once when opened.
After an operator selects a campaign and clicks **Check campaign**, it reads
that campaign's active state, assigned users, linked contact-list count, and
current agent data. Realtime agents are matched to the selected campaign's
assigned agent IDs. Available, logged-in, talking, and ringing counts are
calculated from `/api/realtime/agents` with the voice-channel filter. The
campaign page no longer requests realtime queue data. If assigned-agent status
is incomplete, affected counts remain unavailable instead of showing zero.
**Load campaigns** refreshes the names; **Check campaign** becomes **Refresh
campaign** after the first read and updates only the selected campaign. Its backend
uses `POST /api/performance/campaigns` and
`POST /api/performance/campaigns/{id}` with the same credential handling as the
campaign options route. These calls are read only.

The selected campaign's latest ten Hopper History entries show only status,
start time, and end time. The app requests `VoiceQueueId` in the response and
rejects rows for a different campaign. Hopper counts remain unavailable until
their reporting request is verified. Agent voice states are current activity,
not a historical call rate; the tab does not treat a missing measurement as zero.

The Agent Performance tab separately reads assigned agents and their current
voice status for one active campaign. It uses
`POST /api/performance/campaigns/{id}/agents` only when an operator checks or
refreshes that campaign. The status filter works on the loaded agents without
another xCALLY request. Campaign Performance links to this tab; the campaign
snapshot does not fetch agent presence. Agent call rates and rankings are not
shown until per-agent call attribution and a time range are verified.

## Supported markets

| Code | Market | List prefix | Schema |
|---|---|---|---|
| `GH` | Ghana | `Gh_` | Shared GH/UG/ZM schema |
| `UG` | Uganda | `Ug_` | Shared GH/UG/ZM schema |
| `ZA` | South Africa | `Za_` | Shared schema plus `BANK_ACCOUNT_NUMBER` |
| `ZM` | Zambia | `Zm_` | Shared GH/UG/ZM schema |

Market definitions live in `app/domain.py`. Add ZA-only headers to the ZA configuration rather than adding market conditionals to the upload workflow.

## Local development

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
python -m uvicorn main:app --reload
```

Open <http://127.0.0.1:8000>.

Alternatively, use the startup script (port `8050` by default):

```bash
./run.sh
```

Enable development reload or override the bind address:

```bash
RELOAD=1 ./run.sh
HOST=0.0.0.0 PORT=8050 ./run.sh
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```

Tests cover market configuration, CSV validation, custom-field binding, safe unique list names, and history retention. Tests do not call the live xCALLY server.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `XCALLY_USERNAME` | Server-side xCALLY username | Empty |
| `XCALLY_PASSWORD` | Server-side xCALLY password | Empty |
| `XCALLY_BASE_URL` | xCALLY origin | Existing deployment URL |
| `XCALLY_API_PREFIX` | xCALLY API path | `/api` |
| `XCALLY_VERIFY_SSL` | Verify the xCALLY certificate | `false` for current self-signed deployment |
| `XCALLY_CA_BUNDLE` | Internal CA certificate bundle | Empty |
| `UPLOAD_MAX_WORKERS` | Concurrent uploads, capped at 10 | `5` |
| `MAX_UPLOAD_BYTES` | Maximum bytes per CSV | `62914560` |
| `HISTORY_LIMIT` | Retained history entries | `500` |

The current xCALLY installation presents a self-signed certificate, so compatibility mode disables verification by default as the legacy uploader did. Configure `XCALLY_CA_BUNDLE` with the trusted internal CA, or install a valid certificate and set `XCALLY_VERIFY_SSL=true`, before treating the connection as production-hardened.

## Architecture

```text
Browser / automation client
          ↓
       FastAPI
          ↓
Upload service → market schema → xCALLY client
          ↓
   history repository
```

- `main.py` — HTTP boundary and bounded concurrency
- `app/domain.py` — market definitions and result models
- `app/service.py` — framework-independent upload workflow
- `app/xcally.py` — CSV validation, field binding, and xCALLY transport
- `app/history.py` — locked atomic JSON history repository
- `app/settings.py` — environment configuration
- `index.html` — replaceable operator interface

## Security and production notes

- The browser sends only a market code; prefixes and mappings are selected server-side.
- Filenames are normalized into `Market_YYYYMMDD_filename` list names, and temporary uploads use the operating system temp directory.
- Duplicate named headers, non-UTF-8, non-CSV, and oversized inputs are rejected before upload.
- Unnamed columns are ignored for compatibility with CSV exports containing trailing or unused columns.
- Unknown/export-only columns are ignored and returned as `skipped_headers`; they do not block valid business fields.
- Credentials are never written to upload history or returned by the API.
- Upstream response bodies are not exposed to users.
- Cross-origin access is not enabled.
- Put the service behind organization SSO or an authenticated reverse proxy before exposing it publicly.
- JSON history is safe for one application process. Replace the repository with PostgreSQL before running multiple replicas or requiring durable audit history.

## Deployment

The included `Procfile` starts the service with Uvicorn. Configure secrets in the deployment platform rather than committing `.env`.
