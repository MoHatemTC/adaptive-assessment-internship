#!/usr/bin/env bash
# Launch all three CAT approaches side by side for a human test run.
#
#   approach 1  code math, LLM picks      http://localhost:8501
#   approach 2  LLM math, code picks      http://localhost:8502
#   approach 3  full LLM controller       http://localhost:8503
#
# The approaches live on three git branches. This resolves each one to a checkout via
# `git worktree list` rather than hardcoding paths, so it keeps working when the
# worktrees move — they have already moved once, when the drive they were created on
# stopped being mounted.
#
# Usage:
#   ./run_human_test.sh                 # model from each worktree's .env
#   ./run_human_test.sh kimi-k2.5       # override the model for all three
#   ./run_human_test.sh --stop
#
# Logs: /tmp/cat-human-test/<approach>.log, plus each worktree's own assessment.log.

set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR=/tmp/cat-human-test

BRANCHES=(
  "approach-1-code-math-llm-pick:8501"
  "approach-2-llm-math-code-pick:8502"
  "approach-3-llm-full-cat:8503"
)

if [[ "${1:-}" == "--stop" ]]; then
  pkill -f "streamlit run streamlit_app.py" && echo "stopped" || echo "nothing running"
  exit 0
fi

MODEL_OVERRIDE="${1:-}"

# Streamlit is not in the system python here, and the repo's committed .venv was built
# on a drive that is no longer mounted (its bin/python is a dangling file). Prefer an
# explicit venv, then whatever is on PATH.
STREAMLIT=""
for cand in "$REPO/../.venv-cat/bin/streamlit" "$REPO/.venv/bin/streamlit" "$(command -v streamlit || true)"; do
  [[ -x "$cand" ]] && { STREAMLIT="$cand"; break; }
done
if [[ -z "$STREAMLIT" ]]; then
  cat >&2 <<EOF
No usable streamlit found. Build one:
  uv venv --python 3.12 "$REPO/../.venv-cat"
  uv pip install --python "$REPO/../.venv-cat/bin/python" \\
      streamlit numpy pandas openai python-dotenv langfuse
EOF
  exit 1
fi

# branch -> checkout path, from git itself.
declare -A WT
while read -r path _ br; do
  br="${br#[}"; br="${br%]}"
  [[ -n "$br" ]] && WT["$br"]="$path"
done < <(git -C "$REPO" worktree list)

mkdir -p "$LOGDIR"
started=0

for entry in "${BRANCHES[@]}"; do
  branch="${entry%%:*}"; port="${entry##*:}"
  dir="${WT[$branch]:-}"

  if [[ -z "$dir" ]]; then
    echo "!! $branch: not checked out. Add one with:" >&2
    echo "     git -C $REPO worktree add ../wt/${branch:9:2} $branch" >&2
    continue
  fi
  app="$dir/masaar-mcq-cat-test"
  [[ -f "$app/streamlit_app.py" ]] || { echo "!! $branch: no app at $app" >&2; continue; }

  # .env is gitignored, so a fresh worktree has no credentials and the app would come up
  # with no LLM at all — every step silently falling back to coded logic. That is the one
  # thing a human comparison must not do unknowingly, so refuse rather than mislead.
  if [[ ! -f "$app/.env" ]]; then
    echo "!! $branch: no .env at $app — copy one in first:" >&2
    echo "     cp $REPO/masaar-mcq-cat-test/.env $app/.env" >&2
    continue
  fi

  # tracing.py already defaults these per branch; setting them explicitly means a
  # worktree on the wrong commit shows up as the wrong approach in Langfuse rather than
  # quietly pooling with the right one.
  env_vars=("CAT_APPROACH_ID=$branch" "CAT_TRACE_TAGS=run:human-test")
  [[ -n "$MODEL_OVERRIDE" ]] && env_vars+=("LITELLM_MODEL=$MODEL_OVERRIDE")

  ( cd "$app" && env "${env_vars[@]}" "$STREAMLIT" run streamlit_app.py \
      --server.port "$port" --server.headless true \
      --browser.gatherUsageStats false > "$LOGDIR/$branch.log" 2>&1 & )
  echo "  $branch  ->  http://localhost:$port"
  started=$((started + 1))
done

[[ $started -eq 0 ]] && { echo "nothing started" >&2; exit 1; }

echo
# No apostrophe in this default: bash re-parses quotes inside ${...}, so a lone ' in the
# :- text opens a quote the parser never closes and the whole script fails to load.
echo "model: ${MODEL_OVERRIDE:-per-worktree .env}"
echo "logs : $LOGDIR/"
echo "stop : $0 --stop"
echo
for entry in "${BRANCHES[@]}"; do
  branch="${entry%%:*}"; port="${entry##*:}"
  [[ -n "${WT[$branch]:-}" ]] || continue
  for _ in $(seq 1 40); do
    if (exec 3<>/dev/tcp/127.0.0.1/"$port") 2>/dev/null; then
      echo "  ready :$port  $branch"; break
    fi
    sleep 1
  done
done
