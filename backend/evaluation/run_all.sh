#!/usr/bin/env bash
# Run every arm against every DGP cohort, in parallel, then analyse.
#
# Approach B and Approach C live on different branches, so their engines cannot be
# imported into one process — both define `app.*`. Each arm therefore runs as its own
# process, against its own checkout, writing to a shared results directory. That is the
# whole reason the cohort is a file: it is what makes two processes score the same people.
#
#   C_BACKEND=/path/to/graph-branch/backend \
#   B_BACKEND=/path/to/b-branch/backend \
#   PYTHON=/path/to/python \
#   OUT=eval-results \
#   ./evaluation/run_all.sh 4000 42
set -uo pipefail

N="${1:-4000}"
SEED="${2:-42}"
PYTHON="${PYTHON:-python3}"
C_BACKEND="${C_BACKEND:?set C_BACKEND to the graph-augmented branch's backend/}"
B_BACKEND="${B_BACKEND:?set B_BACKEND to the CAT-only branch's backend/}"
OUT="${OUT:-eval-results}"
COHORTS="$OUT/cohorts"
RUNS="$OUT/runs"

mkdir -p "$COHORTS" "$RUNS"

# One BLAS thread per process. The posterior update is a 41-point vector operation, so
# numpy's threaded kernels buy nothing and, at sixteen concurrent arms, oversubscribe the
# machine badly enough to cost an order of magnitude in wall clock.
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1

echo "== Phase 0c: cohorts (n=$N seed=$SEED) =="
(cd "$C_BACKEND" && "$PYTHON" -m evaluation.make_cohort --n "$N" --seed "$SEED" --out "$COHORTS") || exit 1

echo "== Phase 3: arms x DGP, in parallel =="
pids=()
for cohort in "$COHORTS"/cohort_*.json; do
  for arm in C-off C-shipped C-full; do
    (cd "$C_BACKEND" && "$PYTHON" -m evaluation.run_arm --arm "$arm" --cohort "$cohort" \
        --out "$RUNS" --trace-every 200 > "$RUNS/$arm.$(basename "$cohort" .json).log" 2>&1) &
    pids+=($!)
  done
  (cd "$B_BACKEND" && "$PYTHON" -m evaluation.run_arm --arm B --cohort "$cohort" \
      --out "$RUNS" --trace-every 200 > "$RUNS/B.$(basename "$cohort" .json).log" 2>&1) &
  pids+=($!)
done

failed=0
for pid in "${pids[@]}"; do
  wait "$pid" || failed=$((failed + 1))
done
echo "== $failed of ${#pids[@]} arm runs failed =="

echo "== Analysis =="
(cd "$C_BACKEND" && "$PYTHON" -m evaluation.analyse --runs "$RUNS" --cohorts "$COHORTS" --out "$OUT/report")
