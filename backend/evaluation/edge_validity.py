#!/usr/bin/env python3
"""Phase 0b — offline prerequisite-edge validity, and the Layer 4 density precondition.

    python -m evaluation.edge_validity --cohort <cohort.json> --runs <runs dir> --out <dir>

TWO CHECKS, BOTH CHEAP, BOTH BEFORE THE MAIN EXPERIMENT

C-DAG-21 — EDGE VALIDITY FROM A LINEAR FORM. `C-DAG-20` measures an edge at runtime, after
it has already affected candidates. The offline version asks the same question of a dense
response matrix — P(pass child | fail parent) — costs nothing, needs no verification arm,
and lets a bad edge be pruned before the experiment rather than priced after it. The
repository already ships `scripts/validate_prerequisite_edges.py`, which computes this from
a corpus of finished sessions; it cannot run before there are sessions. This does the same
arithmetic against the simulated linear form, so the edge set can be judged on day zero.

An edge is INFORMATIVE only where the parent was failed. That is the whole difficulty: a
cohort in which nobody fails the prerequisite carries no evidence about the edge at all,
however many candidates it contains, which is why the count of informative pairs is
reported beside every rate rather than behind it.

A3 — THE REPLAY PRECONDITION. Section 5.4 says a broad response matrix is "preferable".
It is not preferable, it is a hard precondition: to replay an adaptive path you need the
historical candidate to have answered whichever item the CAT selects. This reports the
ON-MATRIX RATE — what share of the adaptive arms' actual selections a linear form of a
given size would have covered — so the required per-candidate coverage is a number rather
than an adjective.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

for key, value in {
    "LITELLM_BASE_URL": "http://localhost:4000",
    "LITELLM_API_KEY": "placeholder",
    "LITELLM_MODEL": "placeholder-model",
    "E2B_API_KEY": "placeholder",
}.items():
    os.environ.setdefault(key, value)

import numpy as np  # noqa: E402

from evaluation import responder  # noqa: E402
from evaluation.dgp import Cohort  # noqa: E402
from evaluation.stats import clopper_pearson  # noqa: E402

# The engine's own propagation thresholds. Read as constants rather than from settings so
# the offline check states the bar it applied, whatever an operator has since moved.
STRONG_SUCCESS = 0.80
STRONG_FAILURE = 0.20

# Below this many informative pairs an edge has not been measured, whatever its rate says.
MINIMUM_INFORMATIVE_PAIRS = 100

# An edge may enforce blocking only if a candidate who fails the parent rarely passes the
# child. Set at the false-blocking gate: if more than 2% of blocked descendants would have
# been mastered, enforcement breaches C-DAG-04 by construction.
ENABLE_BAR_P_PASS_CHILD_GIVEN_FAIL_PARENT = 0.02


def node_scores(cohort: Cohort, bank) -> dict[str, dict[str, float]]:
    """simulee -> node -> mean simulated score over every item measuring that node.

    The dense linear form, generated rather than collected. Every simulee answers every
    item, which is exactly the calibration matrix item parameters would need anyway — run
    it once, use it twice, as A3 says.
    """
    by_node: dict[str, list] = defaultdict(list)
    for item in bank.all_items():
        if item.status != "active":
            continue
        by_node[responder.primary_node(item)].append(item)

    scores: dict[str, dict[str, float]] = {}
    for simulee in cohort.simulees:
        row: dict[str, float] = {}
        for node, items in by_node.items():
            main = node.split(".", 1)[0]
            if main not in simulee.theta:
                continue
            values = []
            for item in items:
                plan = responder.plan_for(simulee, item, main)
                values.append(float(plan.correct) if item.modality == "mcq" else plan.score)
            if values:
                row[node] = float(np.mean(values))
        scores[simulee.simulee_id] = row
    return scores


def edge_report(cohort: Cohort, scores: dict[str, dict[str, float]]) -> list[dict]:
    rows = []
    truth = {s.simulee_id: s.nodes for s in cohort.simulees}
    true_edges = set(cohort.true_prerequisites)

    for parent, child in cohort.graph_prerequisites:
        failed_parent = passed_child_given_fail = 0
        passed_child = passed_parent_given_pass_child = 0
        for simulee_id, row in scores.items():
            p, c = row.get(parent), row.get(child)
            if p is None or c is None:
                continue
            if p <= STRONG_FAILURE:
                failed_parent += 1
                passed_child_given_fail += int(c >= STRONG_SUCCESS)
            if c >= STRONG_SUCCESS:
                passed_child += 1
                passed_parent_given_pass_child += int(p >= STRONG_SUCCESS)

        block_interval = clopper_pearson(passed_child_given_fail, failed_parent)
        informative = failed_parent >= MINIMUM_INFORMATIVE_PAIRS
        # The upper bound, not the point estimate: an edge is enabled on what the data
        # can rule out, not on what it happens to have shown.
        may_block = bool(
            informative and block_interval.upper < ENABLE_BAR_P_PASS_CHILD_GIVEN_FAIL_PARENT
        )
        rows.append(
            {
                "parent": parent,
                "child": child,
                "in_true_structure": (parent, child) in true_edges,
                "n_parent_failures": failed_parent,
                "p_pass_child_given_fail_parent": (
                    round(block_interval.point, 5) if failed_parent else None
                ),
                "upper_95": round(block_interval.upper, 5) if failed_parent else None,
                "n_child_passes": passed_child,
                "p_pass_parent_given_pass_child": (
                    round(passed_parent_given_pass_child / passed_child, 5) if passed_child else None
                ),
                "informative": informative,
                "verdict": (
                    "ENABLE BLOCKING"
                    if may_block
                    else ("KEEP INERT — not measured" if not informative else "KEEP INERT — bar not met")
                ),
            }
        )
    return rows


def on_matrix_rate(runs_dir: Path, bank) -> dict:
    """What share of adaptive selections a linear form of size K would have covered.

    Answers A3 numerically: the replay is only interpretable above an 80% on-matrix rate,
    and this says how large a form that costs on the observed selection distribution.
    """
    served = Counter()
    sessions = 0
    for path in sorted(runs_dir.glob("*.jsonl")):
        for line in path.open(encoding="utf-8"):
            record = json.loads(line)
            served.update(record.get("served_item_ids") or [])
            sessions += 1
    if not served:
        return {}

    total = sum(served.values())
    ordered = [count for _item, count in served.most_common()]
    cumulative = np.cumsum(ordered) / total
    bank_size = len([i for i in bank.all_items() if i.status == "active"])

    def form_size_for(target: float) -> int:
        hit = np.searchsorted(cumulative, target) + 1
        return int(min(hit, len(ordered)))

    return {
        "sessions": sessions,
        "distinct_items_ever_served": len(served),
        "bank_size": bank_size,
        "bank_coverage": round(len(served) / bank_size, 4),
        "on_matrix_rate_by_form_size": {
            str(k): round(float(cumulative[min(k, len(cumulative)) - 1]), 4)
            for k in (10, 20, 30, 40, 60, 80, 100, 150, 200)
            if k <= len(cumulative)
        },
        "form_size_for_80pct_on_matrix": form_size_for(0.80),
        "form_size_for_90pct_on_matrix": form_size_for(0.90),
        "form_size_as_share_of_bank_at_80pct": round(form_size_for(0.80) / bank_size, 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohort", required=True, help="a DGP-1 or DGP-2 cohort")
    parser.add_argument("--runs", default="", help="run directory, for the on-matrix rate")
    parser.add_argument("--out", default="eval-results/edge-validity")
    parser.add_argument("--bank", default="AIE")
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()

    from app.services.orchestrator import registry

    bank = registry.get_bank(registry.resolve_bank_id(args.bank))
    cohort = Cohort.load(args.cohort)
    cohort.simulees = cohort.simulees[: args.limit]

    scores = node_scores(cohort, bank)
    rows = edge_report(cohort, scores)
    matrix = on_matrix_rate(Path(args.runs), bank) if args.runs else {}

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "edge_validity.json").write_text(
        json.dumps(
            {
                "cohort": cohort.dgp,
                "n_simulees": len(cohort.simulees),
                "minimum_informative_pairs": MINIMUM_INFORMATIVE_PAIRS,
                "enable_bar": ENABLE_BAR_P_PASS_CHILD_GIVEN_FAIL_PARENT,
                "edges": rows,
                "replay_precondition": matrix,
            },
            indent=1,
        ),
        encoding="utf-8",
    )

    enable = [r for r in rows if r["verdict"] == "ENABLE BLOCKING"]
    unmeasured = [r for r in rows if not r["informative"]]
    print(f"Phase 0b — offline edge validity on {cohort.dgp}, {len(cohort.simulees)} simulees")
    print(f"  edges asserted by the graph      : {len(rows)}")
    print(f"  edges with >= {MINIMUM_INFORMATIVE_PAIRS} informative pairs : {len(rows) - len(unmeasured)}")
    print(f"  edges that clear the enable bar  : {len(enable)}")
    print()
    print(f"  {'parent':>8} {'child':>8} {'fails':>7} {'P(pass child|fail parent)':>26} {'UCB':>7}  verdict")
    for row in sorted(rows, key=lambda r: (r["p_pass_child_given_fail_parent"] is None, r["p_pass_child_given_fail_parent"] or 0)):
        p = row["p_pass_child_given_fail_parent"]
        u = row["upper_95"]
        print(
            f"  {row['parent']:>8} {row['child']:>8} {row['n_parent_failures']:>7} "
            f"{('—' if p is None else f'{p:.4f}'):>26} {('—' if u is None else f'{u:.4f}'):>7}  {row['verdict']}"
        )
    if matrix:
        print()
        print("  Layer 4 replay precondition (A3):")
        print(f"    distinct items ever served : {matrix['distinct_items_ever_served']} of {matrix['bank_size']}")
        print(f"    linear form for 80% on-matrix : {matrix['form_size_for_80pct_on_matrix']} items "
              f"({100 * matrix['form_size_as_share_of_bank_at_80pct']:.1f}% of the bank)")
        print(f"    linear form for 90% on-matrix : {matrix['form_size_for_90pct_on_matrix']} items")
    print(f"\nwrote {out / 'edge_validity.json'}")


if __name__ == "__main__":
    main()
