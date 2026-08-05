#!/usr/bin/env python3
"""Measure what a session costs, then size the cells from that rather than from hope.

    python -m evaluation.probe --cohorts eval-results/cohorts --out eval-results/probe.json \\
                               --sessions 50 --hours 3 --workers 24

WHY SIZING IS A MEASUREMENT AND NOT A CHOICE

The pre-registration's §4.4 asks for 1,600 sessions per screening cell. That number came
from an assumed throughput and an assumed variance. Both are measurable in ten minutes,
and a cell size derived from measurement can be defended when the answer is unwelcome —
which it will be, because §4.3's arithmetic says a configuration conservative enough to be
safe fires so rarely that proving it safe costs 10-50x the sessions.

WHAT COMES OUT

  t_session         wall-clock seconds per session, per D x K corner. The corners differ:
                    a deeper traversal costs more, and sizing from the cheapest corner
                    would overrun.
  E[L]              mean items per session. Feeds the order-free persona surrogates,
                    which express "the first three items" as a rate over session length.
  firing volume     verified inferences per session. §4.3's whole argument is that
                    tightening a factor shrinks this, and a cell below 2% of baseline is
                    UNMEASURABLE, not safe.
  psi               level discordance, which sets the n the accuracy endpoint needs.
  m_bar, r          inference cluster size and within-candidate error correlation, which
                    set the design effect. Ignoring it reports an interval too narrow by
                    sqrt(deff).

AND WHAT IT REFUSES TO DO

It does not pick a cell size that makes a gate decidable when the budget cannot. Where the
achieved n cannot clear a gate EVEN AT A TRUE RATE OF ZERO, that is reported as "not
decidable at n=..." rather than as a wide upper bound, because a wide bound reads like a
failure and an undecidable gate is not a failure — it is an absence of evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation import personas as personas_module  # noqa: E402
from evaluation import stats  # noqa: E402
from evaluation.design import FACTORS  # noqa: E402

#: The four D x K corners. Cost varies with traversal depth and with how much work the
#: corroboration bookkeeping does, so a single-corner probe would size the sweep from
#: whichever corner it happened to pick.
CORNERS = [
    ("D-lo_K-lo", {"D": "low", "K": "low"}),
    ("D-lo_K-hi", {"D": "low", "K": "high"}),
    ("D-hi_K-lo", {"D": "high", "K": "low"}),
    ("D-hi_K-hi", {"D": "high", "K": "high"}),
]

#: §4.3. Below this share of baseline firing volume a cell cannot be measured, whatever
#: its point estimate looks like.
UNMEASURABLE_VOLUME_FRACTION = 0.02

#: The safety gates the study has to decide, and their thresholds.
SAFETY_GATES = {"C-DAG-03_wrong_inference": 0.03, "C-DAG-04_false_blocking": 0.02}


def corroboration_feasibility(bank_id: str = "AIE", max_depth: int = 4) -> dict:
    """Can the corroboration requirement fire on this graph AT ALL, at each K?

    A STRUCTURAL CHECK, COSTING ZERO SESSIONS. Under `source_node` independence — the only
    setting under which K is a safety control rather than a decoration — an ancestor needs
    K DISTINCT descendants strongly passed before it may be inferred. If the graph does
    not give it K descendants, no session length and no cohort size will ever produce one.

    This is worth its own function because the failure is invisible downstream: a cell
    where K cannot fire reports 0 wrong inferences out of 0 verified, which reads as a
    perfect safety record rather than as an absence of evidence. Knowing beforehand that a
    factor level is structurally inert is the difference between a null result and a
    misread one.
    """
    from app.services.orchestrator import registry

    service = registry.get_graph_service(bank_id)
    if service is None:
        return {"bank_id": bank_id, "available": False}

    children: dict[str, list[str]] = {}
    for edge in service.graph.edges:
        if edge.relation == "PREREQUISITE":
            children.setdefault(edge.from_id, []).append(edge.to_id)

    def descendants_within(node: str, depth: int) -> set[str]:
        seen: set[str] = set()
        frontier = [node]
        for _ in range(depth):
            nxt: list[str] = []
            for current in frontier:
                for child in children.get(current, []):
                    if child not in seen:
                        seen.add(child)
                        nxt.append(child)
            frontier = nxt
        return seen

    by_depth = {}
    for depth in range(1, max_depth + 1):
        reach = {p: descendants_within(p, depth) for p in children}
        by_depth[depth] = {
            str(k): sum(1 for v in reach.values() if len(v) >= k) for k in (1, 2, 3, 4)
        }

    direct = {p: len(c) for p, c in children.items()}
    return {
        "bank_id": bank_id,
        "available": True,
        "prerequisite_edges": sum(len(c) for c in children.values()),
        "parents": len(children),
        "direct_children_per_parent": {
            str(n): sum(1 for v in direct.values() if v == n) for n in sorted(set(direct.values()))
        },
        # parents that could satisfy K, at each depth
        "parents_supporting_k_by_depth": by_depth,
        "k_structurally_possible_at_depth_1": {
            str(k): by_depth[1][str(k)] > 0 for k in (1, 2, 3, 4)
        },
    }


def _corner_env(levels: dict[str, str]) -> dict[str, str]:
    env = {}
    for factor, spec in FACTORS.items():
        level = levels.get(factor, "low")
        env[spec["env"]] = spec[level]
    return env


def _run_corner(*, name, levels, cohort_path, out_dir, sessions, arm) -> dict:
    cell_dir = out_dir / "corners" / name
    cell_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    command = [
        sys.executable, "-m", "evaluation.run_arm",
        "--arm", arm,
        "--cohort", str(cohort_path),
        "--out", str(cell_dir),
        "--run-name", name,
        "--factors", json.dumps(_corner_env(levels)),
        "--limit", str(sessions),
        "--trace-every", "0",
    ]
    started = time.time()
    with (cell_dir / "stdout.log").open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=str(Path(__file__).resolve().parents[1]),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
    elapsed = time.time() - started

    results_path = cell_dir / f"{name}.jsonl"
    if completed.returncode != 0 or not results_path.exists():
        return {"corner": name, "ok": False, "returncode": completed.returncode}

    records = [json.loads(line) for line in results_path.open(encoding="utf-8") if line.strip()]
    return {
        "corner": name,
        "ok": True,
        "sessions": len(records),
        # Wall clock includes interpreter start, bank load and graph resolution — which a
        # sweep pays once per cell, so charging it to the probe is the honest accounting.
        "wall_seconds": round(elapsed, 2),
        "seconds_per_session": round(elapsed / max(len(records), 1), 3),
        "records_path": str(results_path),
        "cohort_path": str(cohort_path),
    }


def _measure(records: list[dict], cohort) -> dict:
    """Everything the sizing arithmetic needs, from one corner's records."""
    items = [r["items_administered"] for r in records]
    truth = {s.simulee_id: s.nodes for s in cohort.simulees}

    verified = wrong = 0
    firing_sessions = 0
    cluster_sizes: list[int] = []
    # Per (candidate, target node): the correctness indicators that share a candidate.
    for record in records:
        nodes = truth.get(record["simulee_id"], {})
        graph = record.get("graph") or {}
        inferred = list(
            graph.get("inferred_mastered") or graph.get("shadow_inferred_mastered") or []
        )
        verifiable = [n for n in inferred if n in nodes]
        if verifiable:
            firing_sessions += 1
            cluster_sizes.append(len(verifiable))
        for node in verifiable:
            verified += 1
            wrong += int(nodes[node] == 0)

    # psi: the share of (candidate, main) pairs where the reported level is wrong. The
    # accuracy endpoint's n scales with this, and it is the one variance input that cannot
    # be guessed from the design.
    discordant = total = 0
    for record in records:
        for variable in record["variables"]:
            total += 1
            discordant += int(variable["common_level"] != variable["common_true_level"])

    return {
        "sessions": len(records),
        "items_per_session_mean": round(statistics.fmean(items), 3) if items else 0.0,
        "items_per_session_p90": (
            round(sorted(items)[int(0.9 * (len(items) - 1))], 3) if items else 0.0
        ),
        "verified_inferences": verified,
        "verified_per_session": round(verified / max(len(records), 1), 4),
        "firing_session_rate": round(firing_sessions / max(len(records), 1), 4),
        "wrong_inference_rate": round(wrong / verified, 5) if verified else None,
        "mean_cluster_size": (
            round(statistics.fmean(cluster_sizes), 3) if cluster_sizes else 0.0
        ),
        "multi_record_clusters": sum(1 for c in cluster_sizes if c >= 2),
        "psi_level_discordance": round(discordant / max(total, 1), 5),
    }


def size_cells(
    *,
    measurements: dict[str, dict],
    hours: float,
    workers: int,
    personas: list[str],
    cells: int,
    assumed_r: float = 0.2,
) -> dict:
    """Turn the measurements into a defensible sessions-per-cell figure.

    Uses the SLOWEST corner. Sizing from the mean would overrun by however much the
    corners differ, and a sweep that overruns is one that gets killed partway — which is
    the failure mode the resume path exists for and the design-integrity check exists to
    refuse.
    """
    usable = [m for m in measurements.values() if m.get("ok")]
    if not usable:
        raise SystemExit("no corner completed; cannot size anything")

    slowest = max(usable, key=lambda m: m["seconds_per_session"])
    per_session = slowest["seconds_per_session"]

    weight_total = sum(personas_module.weight_for(p) for p in personas)
    budget_seconds = hours * 3600.0 * workers
    # Each cell also pays a fixed start-up cost (interpreter, bank, graph). It is folded
    # into seconds_per_session above at the probe's own n, which is small — so at larger n
    # this OVER-estimates cost, and the resulting size is conservative in the safe
    # direction.
    n_per_cell = int(budget_seconds / max(per_session * cells * weight_total, 1e-9))

    # The design effect from clustering. Inferences from one candidate are not independent
    # observations, and treating them as such reports an interval narrower than the data
    # supports by sqrt(deff).
    m_bar = max(
        (m["mean_cluster_size"] for m in usable if m["mean_cluster_size"]), default=1.0
    )
    deff = 1.0 + max(m_bar - 1.0, 0.0) * assumed_r
    n_effective = int(n_per_cell / deff)

    return {
        "slowest_corner": slowest.get("corner"),
        "seconds_per_session": per_session,
        "workers": workers,
        "hours": hours,
        "cells": cells,
        "personas": personas,
        "persona_weight_total": weight_total,
        "sessions_per_cell_raw": n_per_cell,
        "mean_cluster_size": round(m_bar, 3),
        "assumed_r_for_deff": assumed_r,
        "design_effect": round(deff, 3),
        "sessions_per_cell": max(50, n_effective),
        "total_sessions": max(50, n_effective) * cells * weight_total,
    }


def power_report(*, sizing: dict, measurements: dict[str, dict]) -> dict:
    """What that n can and cannot decide. Written BEFORE the run, not after it.

    The point of computing this first is that an undecidable gate is a fact about the
    budget, not about propagation — and reporting it afterwards, as a wide upper bound
    next to a gate threshold, reads as a failure of the thing being measured.
    """
    n = sizing["sessions_per_cell"]
    usable = [m for m in measurements.values() if m.get("ok")]
    baseline_volume = max((m["verified_per_session"] for m in usable), default=0.0)

    rows = []
    for gate_name, threshold in SAFETY_GATES.items():
        needed_at_zero = stats.events_needed_for_upper_bound(threshold, 0.0)
        events_available = n * baseline_volume
        rows.append(
            {
                "gate": gate_name,
                "threshold": threshold,
                "events_needed_at_true_rate_zero": needed_at_zero,
                "events_expected_at_baseline_volume": round(events_available, 1),
                "decidable": events_available >= needed_at_zero,
                "verdict": (
                    "decidable"
                    if events_available >= needed_at_zero
                    else f"NOT decidable at n={n}: needs {needed_at_zero} verified events "
                         f"even at a true rate of zero, and this n yields "
                         f"~{events_available:.0f}"
                ),
            }
        )

    psi = max((m["psi_level_discordance"] for m in usable), default=0.25)
    return {
        "sessions_per_cell": n,
        "baseline_verified_inferences_per_session": round(baseline_volume, 4),
        "unmeasurable_volume_floor": round(
            baseline_volume * UNMEASURABLE_VOLUME_FRACTION, 5
        ),
        "unmeasurable_note": (
            "A cell whose firing volume falls below this is declared UNMEASURABLE, not "
            "safe. An unfalsifiable 'looks fine' is the failure mode this exists to stop."
        ),
        "accuracy_endpoint": {
            "psi_level_discordance": round(psi, 5),
            "detectable_margin_pp_at_80_power": round(
                stats.detectable_margin(n, psi) * 100, 3
            ),
            "n_for_2pp_margin_at_80_power": stats.n_for_paired_binary(0.02, psi),
            "n_for_2pp_margin_at_90_power": stats.n_for_paired_binary(0.02, psi, power=0.90),
        },
        "safety_gates": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cohorts", required=True)
    parser.add_argument("--out", default="eval-results/probe.json")
    parser.add_argument("--dgp", default="DGP-2")
    parser.add_argument("--persona", nargs="*", default=["P01", "P02", "P09"])
    parser.add_argument("--arm", default="C-full")
    parser.add_argument("--sessions", type=int, default=50)
    parser.add_argument("--hours", type=float, default=3.0)
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 8) - 8))
    parser.add_argument("--cells", type=int, default=36)
    args = parser.parse_args()

    from evaluation.dgp import Cohort

    cohorts_dir = Path(args.cohorts)
    matches = sorted(cohorts_dir.glob(f"cohort_{args.dgp}_P01_n*.json")) or sorted(
        cohorts_dir.glob(f"cohort_{args.dgp}_n*.json")
    )
    if not matches:
        raise SystemExit(f"no P01 cohort for {args.dgp} in {cohorts_dir}")
    cohort_path = matches[-1]

    out_path = Path(args.out)
    out_dir = out_path.parent / (out_path.stem + "_runs")
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"probing {len(CORNERS)} corners x {args.sessions} sessions on {cohort_path.name}")
    with ThreadPoolExecutor(max_workers=min(len(CORNERS), args.workers)) as pool:
        futures = {
            name: pool.submit(
                _run_corner,
                name=name,
                levels=levels,
                cohort_path=cohort_path,
                out_dir=out_dir,
                sessions=args.sessions,
                arm=args.arm,
            )
            for name, levels in CORNERS
        }
        runs = {name: future.result() for name, future in futures.items()}

    cohort = Cohort.load(cohort_path)
    measurements: dict[str, dict] = {}
    for name, run in runs.items():
        if not run.get("ok"):
            print(f"  {name}: FAILED (exit {run.get('returncode')})")
            measurements[name] = run
            continue
        records = [
            json.loads(line)
            for line in Path(run["records_path"]).open(encoding="utf-8")
            if line.strip()
        ]
        measured = {**run, **_measure(records, cohort)}
        measurements[name] = measured
        print(
            f"  {name}: {measured['seconds_per_session']:.2f}s/session, "
            f"E[L]={measured['items_per_session_mean']:.1f}, "
            f"{measured['verified_per_session']:.2f} verified inferences/session, "
            f"psi={measured['psi_level_discordance']:.3f}"
        )

    sizing = size_cells(
        measurements=measurements,
        hours=args.hours,
        workers=args.workers,
        personas=args.persona,
        cells=args.cells,
    )
    power = power_report(sizing=sizing, measurements=measurements)

    feasibility = corroboration_feasibility(cohort.bank_id or "AIE")
    document = {
        "cohort": str(cohort_path),
        "arm": args.arm,
        "dgp": args.dgp,
        "probe_sessions_per_corner": args.sessions,
        "corroboration_feasibility": feasibility,
        "corners": measurements,
        "expected_session_length": max(
            (m.get("items_per_session_mean", 0.0) for m in measurements.values()), default=18.0
        ),
        "sizing": sizing,
        "power": power,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(document, indent=1), encoding="utf-8")

    if feasibility.get("available"):
        possible = feasibility["k_structurally_possible_at_depth_1"]
        inert = [k for k, ok in possible.items() if not ok and k != "1"]
        print(
            f"\nCorroboration feasibility ({feasibility['bank_id']}): "
            f"{feasibility['prerequisite_edges']} edges, "
            f"children per parent {feasibility['direct_children_per_parent']}"
        )
        if inert:
            print(
                f"  K={','.join(inert)} CANNOT FIRE AT DEPTH 1 on this graph: an ancestor "
                "needs K distinct descendants and does not have them. Those cells will "
                "report 0 wrong out of 0 verified, which is an absence of evidence and "
                "not a safety record."
            )

    print(f"\nsessions/cell: {sizing['sessions_per_cell']} "
          f"({sizing['total_sessions']:,} sessions total over {args.cells} cells "
          f"x {sizing['personas']})")
    print(f"E[L] = {document['expected_session_length']:.1f} items")
    print(f"\nAccuracy endpoint: detectable margin "
          f"{power['accuracy_endpoint']['detectable_margin_pp_at_80_power']:.2f}pp at 80% power "
          f"(2pp would need n={power['accuracy_endpoint']['n_for_2pp_margin_at_80_power']:,})")
    print("\nSafety gates:")
    for row in power["safety_gates"]:
        print(f"  {row['gate']}: {row['verdict']}")
    print(f"\nprobe -> {out_path}")


if __name__ == "__main__":
    main()
