#!/usr/bin/env bash
set -euo pipefail

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8050}"
RELOAD="${RELOAD:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$(dirname "$0")"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON_BIN=".venv/bin/python"
fi

if ! "$PYTHON_BIN" -m uvicorn --version >/dev/null 2>&1; then
  echo "uvicorn is not installed. Run: $PYTHON_BIN -m pip install -r requirements.txt" >&2
  exit 1
fi

args=(main:app --host "$HOST" --port "$PORT")

if [[ "$RELOAD" == "1" ]]; then
  args+=(--reload)
fi

echo "Starting xCALLY Upload Automation at http://$HOST:$PORT"
exec "$PYTHON_BIN" -m uvicorn "${args[@]}"
