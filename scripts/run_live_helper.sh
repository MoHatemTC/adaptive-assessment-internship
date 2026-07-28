#!/usr/bin/env bash
# Live interviewer helper (WebSocket rooms). Must use the repo venv so Langfuse is present.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
if [[ ! -x ../.venv/bin/python ]]; then
  echo "Missing .venv — run ./scripts/run_streamlit.sh once to create it." >&2
  exit 1
fi
exec ../.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port "${LIVE_PORT:-8765}" "$@"
