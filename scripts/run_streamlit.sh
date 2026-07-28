#!/usr/bin/env bash
# Full adaptive engine (MCQ + code + Live voice) — same role as cat-engine-combined-streamlit.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
if [[ ! -x .venv/bin/streamlit ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -U pip
  .venv/bin/pip install -r streamlit/requirements.txt
fi
exec .venv/bin/streamlit run streamlit/main.py --server.port "${STREAMLIT_PORT:-8501}" "$@"
