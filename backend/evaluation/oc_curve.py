#!/usr/bin/env python3
"""Deliverable 4: the operating characteristic of the confidence gate.

    python -m evaluation.oc_curve --cohorts eval-results/cohorts --out eval-results/oc

THE ONE QUESTION S1 LEFT OPEN

S1 established that no propagation configuration clears the 3% gate. It also established
something narrower and more actionable: the confidence threshold that licenses propagation
is set at 0.80, and on DGP-3 — the only arm where grader confidence has any variance — that
rejects under 1% of the evidence reaching it. The gate discriminates at 0.95. Nobody has
ever measured where it should sit.

That is what an operating characteristic is for: sweep the threshold, plot what it buys
(a lower wrong-inference rate) against what it costs (firing volume, and coverage), and
draw the gate on it. §10 deliverable 4 asks for exactly this and it was never produced.

WHY ONLY C IS SWEPT

Because it is the only factor left with reachable levels. S1 showed `K >= 2` is
unsatisfiable on this graph, `D > 1` is below the weight floor, and `λ` collapses to an
on/off switch — so a curve in any of those traces a single point. `C` is the one knob whose
levels the engine can actually distinguish, and DGP-3 is the only arm on which it has
anything to distinguish them with.

THE CONFOUND, STATED UP FRONT

`C` is not propagation-only. `is_strong_success` uses it to decide DIRECT node status too,
so raising it does not merely filter inferences — it also reduces what counts as directly
mastered, and therefore coverage. The curve reports both, because a threshold that halves
wrong inference by making the assessment measure less is not a threshold anyone should set.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation import stats  # noqa: E402
from evaluation.analyse import dag_safety  # noqa: E402
from evaluation.dgp import Cohort  # noqa: E402

#: Thresholds to trace. Below 0.80 is pointless — the shipped value already rejects nearly
#: nothing — and 1.00 is included as the limit case: accept only evidence graded with
#: complete certainty, which on this harness means MCQ and code alone.
THRESHOLDS = ("0.80", "0.85", "0.90", "0.93", "0.95", "0.97", "1.00")

#: Everything else held at the only configuration S1 found reachable AND permissive:
#: depth 1 because deeper is below the weight floor, K=1 because K>=2 cannot be satisfied,
#: and the widest modality and edge allowlists so the curve is not confounded by a second
#: filter doing the work.
HELD = {
    "GRAPH_MAXIMUM_PROPAGATION_DEPTH": "1",
    "GRAPH_MINIMUM_CORROBORATIONS": "1",
    "GRAPH_STRONG_SUCCESS_THRESHOLD": "0.80",
    "GRAPH_UPWARD_DECAY": "0.7",
    "GRAPH_INFERENCE_MODALITIES": "code,voice,open",
    "GRAPH_ACCEPTED_VALIDATION_STATUSES": "",
}

WRONG_INFERENCE_GATE = 0.03
FALSE_BLOCKING_GATE = 0.02


def _run(*, threshold, persona, cohort_path, out_dir, limit, arm, dgp):
    cell = out_dir / "cells" / f"C{threshold}__{persona}"
    cell.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({k: "1" for k in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS",
                                 "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")})
    factors = {**HELD, "GRAPH_MINIMUM_PROPAGATION_CONFIDENCE": threshold}
    command = [
        sys.executable, "-m", "evaluation.run_arm",
        "--arm", arm, "--cohort", str(cohort_path), "--out", str(cell),
        "--run-name", f"C{threshold}", "--factors", json.dumps(factors),
        "--limit", str(limit), "--trace-every", "0",
    ]
    with (cell / "stdout.log").open("w", encoding="utf-8") as log:
        done = subprocess.run(command, cwd=str(Path(__file__).resolve().parents[1]),
                              env=env, stdout=log, stderr=subprocess.STDOUT, check=False)
    return threshold, persona, done.returncode, cell / f"C{threshold}.jsonl"


def measure(records: list[dict], cohort: Cohort) -> dict:
    safety = dag_safety(records, cohort)
    wrong = safety["C-DAG-03_wrong_inference"]
    block = safety["C-DAG-04_false_blocking"]
    sessions = max(len(records), 1)

    # COVERAGE, because C is not propagation-only. Raising it also shrinks what counts as
    # directly mastered, so a threshold can "improve" safety by measuring less.
    direct = sum(
        len((r.get("graph") or {}).get("direct_mastered") or []) for r in records
    )
    measured = sum(
        len(set((r.get("graph") or {}).get("direct_mastered") or [])
            | set((r.get("graph") or {}).get("direct_not_mastered") or []))
        for r in records
    )
    return {
        "sessions": sessions,
        "verified_inferences": wrong["verified"],
        "volume_per_session": round(wrong["verified"] / sessions, 4),
        "wrong_rate": wrong["rate"],
        "wrong_ucb": wrong["upper_95"],
        "clears_gate": (
            wrong["upper_95"] is not None and wrong["upper_95"] < WRONG_INFERENCE_GATE
        ),
        "false_block_rate": block["rate"],
        "false_block_ucb": block["upper_95"],
        "direct_mastered_per_session": round(direct / sessions, 3),
        "nodes_measured_per_session": round(measured / sessions, 3),
        "questions_per_session": round(
            sum(r["items_administered"] for r in records) / sessions, 3
        ),
        "exact_level_accuracy": round(
            sum(
                v["common_level"] == v["common_true_level"]
                for r in records for v in r["variables"]
            ) / max(sum(len(r["variables"]) for r in records), 1),
            5,
        ),
    }


def render(document: dict) -> str:
    lines = ["# Operating characteristic — the confidence gate\n"]
    add = lines.append
    add(f"Arm `{document['arm']}` on `{document['dgp']}`, {document['sessions_per_cell']} "
        f"sessions per point. Every other factor held at the configuration S1 found "
        f"reachable and permissive (D=1, K=1, S=0.80, λ=0.7, all modalities, all edges).\n")
    add(f"Gates drawn: wrong inference **< {WRONG_INFERENCE_GATE:.0%}** (95% upper bound), "
        f"false blocking **< {FALSE_BLOCKING_GATE:.0%}**.\n")

    for persona, rows in document["curves"].items():
        add(f"## {persona}\n")
        add("| C | verified | volume/sess | wrong rate | 95% UCB | clears 3%? | "
            "direct mastered/sess | questions | accuracy |")
        add("|---|---:|---:|---:|---:|:--:|---:|---:|---:|")
        for row in rows:
            rate = "—" if row["wrong_rate"] is None else f"{row['wrong_rate']:.4f}"
            ucb = "—" if row["wrong_ucb"] is None else f"{row['wrong_ucb']:.4f}"
            mark = "**yes**" if row["clears_gate"] else "no"
            add(f"| {row['threshold']} | {row['verified_inferences']} | "
                f"{row['volume_per_session']:.4f} | {rate} | {ucb} | {mark} | "
                f"{row['direct_mastered_per_session']:.2f} | "
                f"{row['questions_per_session']:.2f} | {row['exact_level_accuracy']:.4f} |")
        add("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cohorts", required=True)
    parser.add_argument("--out", default="eval-results/oc")
    parser.add_argument("--dgp", default="DGP-3",
                        help="DGP-3 is the only arm where grader confidence has variance")
    parser.add_argument("--persona", nargs="*", default=["P01", "P09"])
    parser.add_argument("--arm", default="C-full")
    parser.add_argument("--n", type=int, default=200)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 8) - 8))
    args = parser.parse_args()

    cohorts_dir, out_dir = Path(args.cohorts), Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    for persona in args.persona:
        matches = sorted(cohorts_dir.glob(f"cohort_{args.dgp}_{persona}_n*.json"))
        if not matches:
            raise SystemExit(f"no cohort for {args.dgp}/{persona} in {cohorts_dir}")
        for threshold in THRESHOLDS:
            jobs.append((threshold, persona, matches[-1]))

    print(f"{len(jobs)} points ({len(THRESHOLDS)} thresholds x {len(args.persona)} personas) "
          f"at n={args.n} on {args.workers} workers")
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(_run, threshold=t, persona=p, cohort_path=c, out_dir=out_dir,
                        limit=args.n, arm=args.arm, dgp=args.dgp)
            for t, p, c in jobs
        ]
        results = [f.result() for f in futures]
    print(f"ran in {(time.time() - started) / 60:.1f} min")

    cohorts = {
        p: Cohort.load(sorted(cohorts_dir.glob(f"cohort_{args.dgp}_{p}_n*.json"))[-1])
        for p in args.persona
    }
    curves: dict[str, list] = {p: [] for p in args.persona}
    for threshold, persona, code, path in results:
        if code != 0 or not path.exists():
            print(f"  FAILED C={threshold} {persona} (exit {code})")
            continue
        records = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
        curves[persona].append({"threshold": threshold, **measure(records, cohorts[persona])})
    for persona in curves:
        curves[persona].sort(key=lambda r: float(r["threshold"]))

    document = {
        "arm": args.arm, "dgp": args.dgp, "sessions_per_cell": args.n,
        "held_factors": HELD, "thresholds": list(THRESHOLDS), "curves": curves,
    }
    (out_dir / "oc_curve.json").write_text(json.dumps(document, indent=1), encoding="utf-8")
    (out_dir / "oc_curve.md").write_text(render(document), encoding="utf-8")
    print(render(document))
    print(f"written to {out_dir}")


if __name__ == "__main__":
    main()
