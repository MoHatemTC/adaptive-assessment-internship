#!/usr/bin/env bash
# Single backend entrypoint for this branch (CAT + live routes).
set -euo pipefail
cd "$(dirname "$0")/.."
# shellcheck disable=SC1091
source .venv/bin/activate
exec uvicorn app.main:app --host 127.0.0.1 --port 8765 --reload
