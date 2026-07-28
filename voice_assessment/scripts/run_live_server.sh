#!/usr/bin/env bash
# Realtime Gemini Live interviewer (browser mic duplex).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate
exec env -u LITELLM_MODEL -u LITELLM_BASE_URL -u GEMINI_LIVE_MODEL -u LITELLM_SSL_VERIFY \
  uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
