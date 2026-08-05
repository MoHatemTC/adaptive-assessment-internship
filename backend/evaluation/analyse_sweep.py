#!/usr/bin/env python3
"""Read a screening sweep and say what it does and does not establish.

    python -m evaluation.analyse_sweep --sweep eval-results/sweep_s1 \\
                                       --cohorts eval-results/cohorts \\
                                       --out eval-results/sweep_s1/report

WHAT THIS REFUSES TO DO

Report a main effect from an incomplete design without saying so. A fractional factorial
is orthogonal only when complete: drop one cell and the effects are no longer clear of the
two-factor interactions the resolution promised. The numbers still compute. They are no
longer estimates of what their labels say.

Report a rate of 0 wrong out of 0 verified as a safety result. That is an absence of
evidence, and §4.3 is explicit that a cell firing below 2% of baseline volume is
UNMEASURABLE rather than safe. Cells that cannot fire are named as such, and excluded from
the effect estimates rather than entered as zeros — a zero from a cell that never fired
would drag a main effect toward "this factor helps" for the worst possible reason.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation import stats  # noqa: E402
from evaluation.analyse import dag_safety  # noqa: E402
from evaluation.design import FACTORS  # noqa: E402
from evaluation.dgp import Cohort  # noqa: E402

#: §4.3. A cell firing below this share of the best cell's volume cannot be measured.
UNMEASURABLE_FRACTION = 0.02

#: §9. Above this, corroboration cannot reach the gate and the K sweep stops.
R_KILL_THRESHOLD = 0.4


def _load_cells(sweep_dir: Path) -> list[dict]:
    design = json.loads((sweep_dir / "design.json").read_text(encoding="utf-8"))
    levels_by_id = {c["cell_id"]: c["levels"] for c in design["cells"]}
    kind_by_id = {c["cell_id"]: c["kind"] for c in design["cells"]}

    cells = []
    for cell_dir in sorted((sweep_dir / "cells").glob("*")):
        if not cell_dir.is_dir():
            continue
        name = cell_dir.name
        cell_id, _, persona = name.partition("__")
        results = cell_dir / f"{cell_id}.jsonl"
        manifest = cell_dir / f"{cell_id}.manifest.json"
        if not results.exists():
            continue
        records = [json.loads(line) for line in results.open(encoding="utf-8") if line.strip()]
        cells.append(
            {
                "cell_id": cell_id,
                "persona": persona or "P01",
                "kind": kind_by_id.get(cell_id, "factorial"),
                "levels": levels_by_id.get(cell_id, {}),
                "records": records,
                "manifest": (
                    json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
                ),
            }
        )
    return cells


def _pairs_by_candidate(records: list[dict], truth: dict[str, dict]) -> dict[str, list[int]]:
    """Correctness indicators (1 = wrong) per candidate, for the `r` estimate."""
    out: dict[str, list[int]] = defaultdict(list)
    for record in records:
        nodes = truth.get(record["simulee_id"], {})
        if not nodes:
            continue
        graph = record.get("graph") or {}
        inferred = list(
            graph.get("inferred_mastered") or graph.get("shadow_inferred_mastered") or []
        )
        for node in inferred:
            if node in nodes:
                out[record["simulee_id"]].append(int(nodes[node] == 0))
    return dict(out)


def _exposure(records: list[dict]) -> dict:
    """Item exposure from the served ids. Computed nowhere before this."""
    counts: dict[str, int] = defaultdict(int)
    sessions = 0
    for record in records:
        sessions += 1
        for item_id in record.get("served_item_ids") or []:
            counts[item_id] += 1
    if not counts:
        return {"items_served": 0}
    served = np.array(sorted(counts.values()), dtype=float)
    rates = served / max(sessions, 1)
    # Gini over the served items, as a single number for how concentrated selection is.
    n = len(served)
    cumulative = np.cumsum(served)
    gini = float((n + 1 - 2 * np.sum(cumulative) / cumulative[-1]) / n) if cumulative[-1] else 0.0
    return {
        "sessions": sessions,
        "distinct_items_served": n,
        "max_exposure_rate": round(float(rates.max()), 5),
        "mean_exposure_rate": round(float(rates.mean()), 5),
        "gini": round(gini, 5),
    }


def _stop_reasons(records: list[dict]) -> dict:
    counts: dict[str, int] = defaultdict(int)
    total = 0
    for record in records:
        for variable in record["variables"]:
            counts[variable.get("stop_reason") or "<BLANK>"] += 1
            total += 1
    return {
        "total": total,
        "distribution": {k: round(v / total, 5) for k, v in sorted(counts.items())} if total else {},
        "blank": counts.get("<BLANK>", 0),
    }


def analyse(sweep_dir: Path, cohorts_dir: Path) -> dict:
    index_path = sweep_dir / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    cells = _load_cells(sweep_dir)
    if not cells:
        raise SystemExit(f"no cells with results under {sweep_dir}")

    dgp = index.get("dgp", "DGP-2")
    cohorts: dict[str, Cohort] = {}
    for persona in {c["persona"] for c in cells}:
        matches = sorted(cohorts_dir.glob(f"cohort_{dgp}_{persona}_n*.json")) or sorted(
            cohorts_dir.glob(f"cohort_{dgp}_n*.json")
        )
        if matches:
            cohorts[persona] = Cohort.load(matches[-1])

    per_cell = []
    for cell in cells:
        cohort = cohorts.get(cell["persona"])
        if cohort is None:
            continue
        safety = dag_safety(cell["records"], cohort)
        truth = {s.simulee_id: s.nodes for s in cohort.simulees}
        wrong = safety["C-DAG-03_wrong_inference"]
        blocking = safety["C-DAG-04_false_blocking"]
        sessions = len(cell["records"])
        per_cell.append(
            {
                "cell_id": cell["cell_id"],
                "persona": cell["persona"],
                "kind": cell["kind"],
                "levels": cell["levels"],
                "sessions": sessions,
                "verified_inferences": wrong["verified"],
                "volume_per_session": round(wrong["verified"] / max(sessions, 1), 5),
                "wrong_inference_rate": wrong["rate"],
                "wrong_inference_ucb": wrong["upper_95"],
                "false_blocking_rate": blocking["rate"],
                "false_blocking_ucb": blocking["upper_95"],
                "questions_per_session": round(
                    float(np.mean([r["items_administered"] for r in cell["records"]])), 4
                )
                if cell["records"]
                else None,
                "exact_level_accuracy": round(
                    float(
                        np.mean(
                            [
                                v["common_level"] == v["common_true_level"]
                                for r in cell["records"]
                                for v in r["variables"]
                            ]
                        )
                    ),
                    5,
                )
                if cell["records"]
                else None,
                "stop_reasons": _stop_reasons(cell["records"]),
                "exposure": _exposure(cell["records"]),
                "depth_strata": safety["C-DAG-03d_wrong_inference_by_depth"],
                "_pairs": _pairs_by_candidate(cell["records"], truth),
            }
        )

    # Firing volume, and which cells cannot be measured at all.
    baseline = max((c["volume_per_session"] for c in per_cell), default=0.0)
    floor = baseline * UNMEASURABLE_FRACTION
    for cell in per_cell:
        cell["measurable"] = bool(cell["verified_inferences"] > 0 and cell["volume_per_session"] > floor)
        if not cell["measurable"]:
            cell["verdict"] = (
                "UNMEASURABLE, not safe: firing volume "
                f"{cell['volume_per_session']:.4f}/session is below the "
                f"{UNMEASURABLE_FRACTION:.0%} floor of {floor:.4f}"
            )

    # `r`, pooled across every measurable cell. It is a property of the candidates and the
    # graph rather than of one configuration, so pooling is what gives it enough clusters
    # to estimate at all.
    pooled: dict[str, list[int]] = defaultdict(list)
    for cell in per_cell:
        if not cell["measurable"]:
            continue
        for candidate, values in cell["_pairs"].items():
            pooled[f"{cell['persona']}:{candidate}"].extend(values)
    r_estimate = stats.within_candidate_error_correlation(dict(pooled))
    if r_estimate.get("estimable"):
        upper = (r_estimate.get("ci95") or [None, None])[1]
        r_estimate["kill_criterion"] = {
            "threshold": R_KILL_THRESHOLD,
            "evaluated_on": "one-sided upper bound of the 95% CI",
            "upper_bound": upper,
            "fires": bool(upper is not None and upper > R_KILL_THRESHOLD),
        }

    # Main effects, per persona, on the two responses the screening question is about.
    effects = {}
    for persona in sorted({c["persona"] for c in per_cell}):
        rows = [c for c in per_cell if c["persona"] == persona]
        factorial = [c for c in rows if c["kind"] == "factorial"]
        centre = [c for c in rows if c["kind"] == "centre"]

        for response_name, key in (
            ("wrong_inference_rate", "wrong_inference_rate"),
            ("questions_per_session", "questions_per_session"),
            ("exact_level_accuracy", "exact_level_accuracy"),
        ):
            # Only measurable cells contribute to a safety response. A cell that never
            # fired has no rate; entering it as 0 would pull the effect toward "this
            # factor makes propagation safer" precisely because it made it inoperative.
            eligible = (
                [c for c in factorial if c["measurable"]]
                if key == "wrong_inference_rate"
                else factorial
            )
            eligible_centre = (
                [c for c in centre if c["measurable"]] if key == "wrong_inference_rate" else centre
            )
            responses = {
                c["cell_id"]: float(c[key])
                for c in eligible
                if c.get(key) is not None and np.isfinite(float(c[key]))
            }
            levels = {c["cell_id"]: c["levels"] for c in eligible}
            centre_values = [
                float(c[key]) for c in eligible_centre if c.get(key) is not None
            ]
            effects.setdefault(persona, {})[response_name] = stats.main_effects(
                responses, levels, list(FACTORS), centre_values
            )

    design_complete = bool(index.get("design_complete", False))
    unmeasurable = [c["cell_id"] for c in per_cell if not c["measurable"]]

    for cell in per_cell:
        cell.pop("_pairs", None)

    return {
        "sweep": str(sweep_dir),
        "design_complete": design_complete,
        "cells": len(per_cell),
        "cells_unmeasurable": len(unmeasurable),
        "unmeasurable_cell_ids": sorted(set(unmeasurable)),
        "baseline_volume_per_session": round(baseline, 5),
        "unmeasurable_floor": round(floor, 6),
        "within_candidate_error_correlation": r_estimate,
        "main_effects": effects,
        "per_cell": per_cell,
    }


def render(report: dict) -> str:
    lines: list[str] = []
    add = lines.append

    add("# S1 screening — results\n")
    if not report["design_complete"]:
        add("> **DESIGN INTEGRITY WARNING.** The sweep index does not report a complete")
        add("> design. A fractional factorial is orthogonal only when complete; with cells")
        add("> missing, the main effects below are not clear of the two-factor interactions")
        add("> the resolution promises. Treat them as descriptive.\n")

    add(f"- cells analysed: **{report['cells']}**")
    add(f"- baseline firing volume: **{report['baseline_volume_per_session']:.4f}** verified inferences/session")
    add(
        f"- cells declared UNMEASURABLE (below {UNMEASURABLE_FRACTION:.0%} of baseline): "
        f"**{report['cells_unmeasurable']}**"
    )
    add("")

    r = report["within_candidate_error_correlation"]
    add("## Within-candidate error correlation `r`\n")
    if not r.get("estimable"):
        add(f"**Not estimable.** {r.get('reason')}")
        add(f"Clusters with 2+ verified inferences: {r.get('clusters_with_multiple')}; "
            f"with exactly one: {r.get('clusters_with_one')}.\n")
        add("This is reported as an absence rather than as a number. `r` decides whether")
        add("corroboration can work at all, and a fabricated value would decide it wrongly.\n")
    else:
        add(f"- point estimate: **{r['point']}** (cross-check {r['cross_check_bonett_price']})")
        add(f"- 95% CI: {r['ci95']}")
        add(f"- clusters: {r['clusters_with_multiple']} with 2+, {r['clusters_with_one']} with one")
        add(f"- design effect: **{r['design_effect']}**")
        kill = r.get("kill_criterion", {})
        add(f"- §9 kill criterion (r > {kill.get('threshold')}): "
            f"**{'FIRES' if kill.get('fires') else 'does not fire'}** "
            f"on the {kill.get('evaluated_on')}\n")

    add("## Main effects\n")
    for persona, responses in sorted(report["main_effects"].items()):
        add(f"### {persona}\n")
        for response, block in sorted(responses.items()):
            add(f"**{response}** — {block['cells_used']} cells, "
                f"pure error SD {block['pure_error_sd']} on {block['pure_error_df']} df, "
                f"SE(effect) {block['effect_standard_error']}")
            if block["cells_dropped"]:
                add(f"  dropped: {', '.join(block['cells_dropped'])}")
            add("")
            add("| factor | effect | mean(low) | mean(high) | active |")
            add("|---|---:|---:|---:|:--:|")
            for factor, row in block["effects"].items():
                if row.get("effect") is None:
                    add(f"| {factor} | — | — | — | n/a |")
                    continue
                mark = "**yes**" if row["active"] else "no"
                add(f"| {factor} | {row['effect']:+.5f} | {row['mean_low']:.5f} | "
                    f"{row['mean_high']:.5f} | {mark} |")
            add("")
            add(f"_{block['activity_rule']}_\n")

    add("## Cells\n")
    add("| cell | persona | n | verified | volume/sess | wrong rate | UCB | measurable |")
    add("|---|---|---:|---:|---:|---:|---:|:--:|")
    for cell in sorted(report["per_cell"], key=lambda c: (c["persona"], c["cell_id"])):
        rate = "—" if cell["wrong_inference_rate"] is None else f"{cell['wrong_inference_rate']:.4f}"
        ucb = "—" if cell["wrong_inference_ucb"] is None else f"{cell['wrong_inference_ucb']:.4f}"
        add(
            f"| {cell['cell_id']} | {cell['persona']} | {cell['sessions']} | "
            f"{cell['verified_inferences']} | {cell['volume_per_session']:.4f} | {rate} | "
            f"{ucb} | {'yes' if cell['measurable'] else '**NO**'} |"
        )
    add("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sweep", required=True)
    parser.add_argument("--cohorts", required=True)
    parser.add_argument("--out", default="")
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="analyse a design with missing cells, stamping every effect as non-orthogonal",
    )
    args = parser.parse_args()

    report = analyse(Path(args.sweep), Path(args.cohorts))
    if not report["design_complete"] and not args.allow_incomplete:
        raise SystemExit(
            "the sweep index does not report a complete design. Main effects from a "
            "partial fractional factorial are not clear of two-factor interactions. "
            "Re-run the sweep with --resume, or pass --allow-incomplete to analyse it "
            "anyway and read the integrity warning the report will carry."
        )

    out_dir = Path(args.out) if args.out else Path(args.sweep) / "report"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "sweep_results.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out_dir / "sweep_report.md").write_text(render(report), encoding="utf-8")
    print(render(report))
    print(f"\nwritten to {out_dir}")


if __name__ == "__main__":
    main()
