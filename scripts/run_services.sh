#!/usr/bin/env bash
# Run the five services locally, without Docker.
#
# `deploy/docker-compose.yml` is the real deployment and the thing to reach for when you
# want to know how this is configured. This script exists for the loop where you are
# editing one service and want the other four up in a second, and it deliberately mirrors
# the compose file's wiring rather than inventing its own.
#
#   ./scripts/run_services.sh            # all five
#   ./scripts/run_services.sh bank-registry grader
#
# Stop with Ctrl-C; every child is killed with the script.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="${SERVICES_VENV_DIR:-$ROOT/.venv}"
PY="$VENV/bin/python"
if [[ ! -x "$PY" ]]; then
  cat >&2 <<'EOF'
No usable virtual environment.

    python3 -m venv .venv
    .venv/bin/pip install -e ./backend -e ./services/contracts \
                             -e ./services/common -e ./services/clients
    .venv/bin/pip install -r backend/requirements.txt

Set SERVICES_VENV_DIR to use a different one.
EOF
  exit 1
fi

# Same wiring as the compose file. Kept in step by hand, which is why the compose file is
# the one to trust if they ever disagree.
export BANK_REGISTRY_URL="${BANK_REGISTRY_URL:-http://127.0.0.1:8081}"
export GRADER_URL="${GRADER_URL:-http://127.0.0.1:8082}"
export COMPETENCY_GRAPH_URL="${COMPETENCY_GRAPH_URL:-http://127.0.0.1:8083}"

declare -A PORTS=(
  [assessment-orchestrator]=8080
  [bank-registry]=8081
  [grader]=8082
  [competency-graph]=8083
  [live-voice]=8765
)

WANTED=("$@")
if [[ ${#WANTED[@]} -eq 0 ]]; then
  WANTED=(bank-registry grader competency-graph assessment-orchestrator live-voice)
fi

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

for name in "${WANTED[@]}"; do
  port="${PORTS[$name]:-}"
  if [[ -z "$port" ]]; then
    echo "unknown service: $name (expected one of: ${!PORTS[*]})" >&2
    exit 2
  fi
  echo "starting $name on :$port"
  (cd "$ROOT/services/$name" && exec "$PY" -m uvicorn service.main:app \
     --host "${SERVICES_HOST:-127.0.0.1}" --port "$port") &
  pids+=($!)
done

wait
