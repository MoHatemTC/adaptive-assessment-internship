#!/usr/bin/env python3
"""Phase 0 — the bank reality check. Run before anything else; it can end the study.

    python -m evaluation.bank_check --bank AIE --out eval-results/bank

The validation document asks for Q-07 (exposure) and Q-09 (blueprint compliance) to be
computed against the REAL bank rather than in the abstract, in Phase 0, because three
constraints have to hold simultaneously on a pool that may be too thin for all three:

    the coverage gate      every required sub-competency measured directly
    the modality blueprint at least one code and one voice item per main
    the question cap       twelve items per main

If the required sub-competencies outnumber the cap, the coverage gate can never be
satisfied and every session ends on the budget escape — which reads as a measurement
failure and is a configuration one. And if a main's per-modality sub-pool is smaller than
its blueprint minimum, the blueprint is unmeetable rather than unmet.

It also answers the question the plan's section 27 assumes rather than checks: can this
bank reach the SE target at all? Information adds, so a pool whose items are individually
weak cannot reach any target within a bounded number of questions no matter how selection
works. The repository's own `test_bank_can_support_the_configured_precision_target` already
fails on this bank for C6; this reports it as a number instead of a red test.
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

import numpy as np

SE_TARGET = 0.55
MAX_QUESTIONS = 12
MODALITY_MINIMUMS = {"code": 1, "voice": 1}


def reachable_se(pool, theta: float, budget: int) -> float:
    """Best standard error the `budget` most informative items could reach at `theta`.

    Test information adds, so the floor is 1/sqrt(sum of the top-k informations). This is
    an upper bound on what selection can achieve — no policy beats taking the best items.
    """
    from cat_engine.engine.services.adaptive.irt import fisher_information

    infos = sorted(
        (fisher_information(theta, i.cat.a, i.cat.b, i.cat.c) for i in pool),
        reverse=True,
    )
    total = sum(infos[:budget])
    return float(1.0 / np.sqrt(total)) if total > 0 else float("inf")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", default="AIE")
    parser.add_argument("--out", default="eval-results/bank")
    args = parser.parse_args()

    from cat_engine.engine.services.orchestrator import registry

    bank_id = registry.resolve_bank_id(args.bank)
    bank = registry.get_bank(bank_id)
    graph = (
        registry.get_graph_service(bank_id)
        if hasattr(registry, "get_graph_service")
        else None
    )
    critical_only = registry.coverage_policy(bank_id)

    items = [i for i in bank.all_items() if i.status == "active"]
    mains = bank.variables()

    report: dict = {
        "bank_id": bank_id,
        "items": len(items),
        "mains": mains,
        "coverage_critical_only": critical_only,
        "per_main": {},
    }

    for main in mains:
        pool = bank.shortlist(main, exclude=set())
        by_modality: Counter[str] = Counter(i.modality for i in pool)
        sub_nodes = sorted(
            {
                m.variable
                for i in pool
                for m in i.measures
                if m.variable.startswith(f"{main}.")
            }
        )

        required: list[str] = []
        if graph is not None:
            if critical_only:
                required = sorted(graph.critical_nodes_for_main(main))
            else:
                required = sorted(
                    nid
                    for nid, node in graph.graph.nodes.items()
                    if node.node_type != "main" and main in node.main_competencies
                )

        # Which required nodes the bank can actually measure at all.
        measurable = {m.variable for i in pool for m in i.measures}
        unmeasurable = [node for node in required if node not in measurable]

        # Per-modality sub-pools, after the graph filter would have removed nothing yet.
        per_modality_nodes: dict[str, set[str]] = defaultdict(set)
        for item in pool:
            for measure in item.measures:
                per_modality_nodes[item.modality].add(measure.variable)

        floors = {
            modality: {
                "items": by_modality.get(modality, 0),
                "minimum": minimum,
                "meetable": by_modality.get(modality, 0) >= minimum,
            }
            for modality, minimum in MODALITY_MINIMUMS.items()
        }

        se_by_theta = {
            str(theta): round(reachable_se(pool, theta, MAX_QUESTIONS), 4)
            for theta in (-2.0, -1.0, 0.0, 1.0, 2.0)
        }
        worst = max(se_by_theta.values())

        report["per_main"][main] = {
            "items": len(pool),
            "by_modality": dict(by_modality),
            "sub_competencies_measured": len(sub_nodes),
            "required_nodes": required,
            "required_count": len(required),
            "required_exceeds_question_cap": len(required) > MAX_QUESTIONS,
            "unmeasurable_required_nodes": unmeasurable,
            "modality_floors": floors,
            "best_reachable_se_at_theta": se_by_theta,
            "se_target": SE_TARGET,
            "se_target_reachable_everywhere": worst <= SE_TARGET,
            # Exposure ceiling: with a 12-item cap and this pool, the least any item could
            # be exposed is 12/len(pool) if selection were uniform. It never is, so this is
            # the optimistic bound Q-07 is measured against.
            "uniform_exposure_floor": round(MAX_QUESTIONS / max(len(pool), 1), 4),
        }

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "bank_check.json").write_text(json.dumps(report, indent=1), encoding="utf-8")

    print(f"Bank reality check — {bank_id}: {len(items)} active items, mains {mains}")
    print(
        f"  coverage policy: {'critical sub-competencies only' if critical_only else 'every sub-competency'}"
    )
    print()
    header = f"  {'main':>5} {'items':>6} {'mcq':>5} {'code':>5} {'voice':>6} {'required':>9} {'>cap?':>6} {'worst SE@12':>12} {'target met':>11}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for main, block in report["per_main"].items():
        modality = block["by_modality"]
        worst = max(block["best_reachable_se_at_theta"].values())
        print(
            f"  {main:>5} {block['items']:>6} {modality.get('mcq', 0):>5} "
            f"{modality.get('code', 0):>5} {modality.get('voice', 0):>6} "
            f"{block['required_count']:>9} {block['required_exceeds_question_cap']!s:>6} "
            f"{worst:>12.4f} {block['se_target_reachable_everywhere']!s:>11}"
        )
        if block["unmeasurable_required_nodes"]:
            print(
                f"        required but unmeasurable by this bank: {block['unmeasurable_required_nodes']}"
            )
    print(f"\nwrote {out / 'bank_check.json'}")


if __name__ == "__main__":
    main()
