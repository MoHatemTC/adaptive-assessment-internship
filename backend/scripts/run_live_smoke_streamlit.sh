#!/usr/bin/env bash
# Standalone Gemini Live smoke Streamlit (no CAT assessment).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate
exec env -u LITELLM_MODEL -u LITELLM_BASE_URL -u GEMINI_LIVE_MODEL -u LITELLM_SSL_VERIFY \
  streamlit run streamlit/live_smoke.py --server.headless true --server.port 8503
