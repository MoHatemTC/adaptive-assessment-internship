#!/usr/bin/env bash
# Live interviewer helper (WebSocket rooms). Must use the repo venv so Langfuse is present.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

VENV="${STREAMLIT_VENV_DIR:-$ROOT/.venv}"
if [[ -e "$VENV" && ! -x "$VENV/bin/python" && -z "${STREAMLIT_VENV_DIR:-}" ]]; then
  VENV="$ROOT/.venv-streamlit"
fi
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "Missing usable Streamlit environment — run ./scripts/run_streamlit.sh once." >&2
  exit 1
fi
# LIVE_HOST=0.0.0.0 when exposing the helper beyond this machine (cloud / LAN).
exec "$VENV/bin/python" -m uvicorn app.main:app \
  --host "${LIVE_HOST:-127.0.0.1}" --port "${LIVE_PORT:-8765}" "$@"
