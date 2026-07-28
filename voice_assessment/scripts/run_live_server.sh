#!/usr/bin/env bash
# Backward-compatible alias to the single backend entrypoint.
set -euo pipefail
exec "$(dirname "$0")/run_backend.sh"
