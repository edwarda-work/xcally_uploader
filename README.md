# xCALLY Upload Automation

A market-aware automation service for validating CSV contact lists and uploading them to xCALLY. Market is local metadata only: it selects the list-name prefix and trusted custom-field schema. It is not sent to xCALLY.

## Supported markets

| Code | Market | List prefix | Schema |
|---|---|---|---|
| `GH` | Ghana | `Gh_` | Shared GH/UG schema |
| `UG` | Uganda | `Ug_` | Shared GH/UG schema |
| `ZA` | South Africa | `Za_` | Shared schema plus `BANK_ACCOUNT_NUMBER` |

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
