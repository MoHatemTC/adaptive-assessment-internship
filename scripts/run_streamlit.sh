#!/usr/bin/env bash
# Full adaptive engine (MCQ + code + Live voice) — same role as cat-engine-combined-streamlit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="${STREAMLIT_VENV_DIR:-$ROOT/.venv}"
# A copied Windows venv can leave executable-looking console scripts backed by a broken
# reparse-point Python. Preserve it and use a fresh local env instead of failing at exec.
if [[ -e "$VENV" && ! -x "$VENV/bin/python" && -z "${STREAMLIT_VENV_DIR:-}" ]]; then
  VENV="$ROOT/.venv-streamlit"
fi
if [[ ! -x "$VENV/bin/python" ]] || ! "$VENV/bin/python" -c \
  'import numpy, openai, pandas, pydantic, streamlit' >/dev/null 2>&1; then
  python3 -m venv "$VENV"
  "$VENV/bin/python" -m pip install -U pip
  "$VENV/bin/python" -m pip install -r streamlit/requirements.txt
fi
exec "$VENV/bin/python" -m streamlit run streamlit/main.py \
  --server.port "${STREAMLIT_PORT:-8501}" "$@"
