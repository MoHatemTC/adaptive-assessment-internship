#!/usr/bin/env python3
"""Run one arm of the B-vs-C experiment over one frozen cohort.

    python -m evaluation.run_arm --arm C-full --cohort <cohort.json> --out <dir>

Writes `<out>/<arm>__<dgp>.jsonl`, one JSON record per simulee, plus a manifest recording
every setting that was in force. The manifest is not decoration: section 6 requires the
experiment to be reproducible from it, and the validation document's pre-registration
amendment requires the configuration to be fixed before the data exists rather than
described after it.

ENVIRONMENT IS SET BEFORE THE APP IS IMPORTED. `Settings` is a pydantic-settings object
instantiated at module import, so a flag applied afterwards would be read by nothing while
appearing to have been applied — the exact failure mode the graph master switch was added
to prevent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.arms import ARMS, FROZEN_ENV  # noqa: E402


def _apply_env(arm_name: str) -> dict[str, str]:
    """Freeze list first, then the arm's own flags. Returns what was applied.

    Refuses to proceed if `Settings` already exists. A flag applied after that object is
    built is read by nothing while looking applied: the run completes, the manifest records
    the arm's name, and every arm silently produces the default configuration's numbers.
    An assertion is the only defence, because nothing downstream can tell the difference.
    """
    if arm_name not in ARMS:
        raise SystemExit(f"unknown arm {arm_name!r}; expected one of {sorted(ARMS)}")
    if "app.config.settings" in sys.modules:
        raise SystemExit(
            "app.config.settings was imported before the arm was applied — every flag in "
            f"arm {arm_name!r} would be ignored. Import order in run_arm.py is load-bearing."
        )
    applied = {**FROZEN_ENV, **ARMS[arm_name].env}
    os.environ.update(applied)
    return applied


def _force_enable_edges(graph_service):
    """Rebuild a graph with every PREREQUISITE edge permitted to infer and to block.

    The authored edges ship with both permissions false and `validation_status:
    unvalidated`, so on the shipped configuration Approach C's propagation cannot fire at
    all. Enabling them here — in the harness, never in the bank data — is what makes the
    C-full arm a measurement of the architecture rather than of an inert feature flag.
    """
    from dataclasses import replace

    from app.services.competency_graph.graph import CompetencyGraphService

    graph = graph_service.graph
    edges = tuple(
        replace(edge, allow_upward_inference=True, allow_downward_blocking=True)
        if edge.relation == "PREREQUISITE"
        else edge
        for edge in graph.edges
    )
    return CompetencyGraphService(replace(graph, edges=edges))


async def _run_all(*, arm_name, cohort, out_dir, limit, max_steps, trace_every):
    from evaluation import HAS_GRAPH, responder
    from evaluation.session_runner import run_session
    from app.config.settings import settings
    from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository
    from app.services.orchestrator.grader import GraderAgent
    from app.services.orchestrator.orchestrator import Orchestrator

    responder.install_stubs()

    if HAS_GRAPH:
        from app.services.orchestrator import registry

        bank_id = registry.resolve_bank_id(cohort.bank_id)
        bank = registry.get_bank(bank_id)
        graph = registry.get_graph_service(bank_id)
        if graph is not None and ARMS[arm_name].force_enable_edges:
            graph = _force_enable_edges(graph)
        orchestrator = Orchestrator(
            bank,
            GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository())),
            graph=graph,
            coverage_critical_only=registry.profile(bank_id).coverage_critical_only,
            bank_id=bank_id,
        )
    else:
        # Approach B: no registry, no graph. The bank file is the same one, addressed
        # directly — the two branches ship identical `question_bank_AIE.json`.
        from app.services.orchestrator.bank import JsonUnifiedBank

        bank_path = Path(__file__).resolve().parents[1] / "app" / "data" / "question_bank_AIE.json"
        bank = JsonUnifiedBank(bank_path)
        orchestrator = Orchestrator(
            bank, GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository()))
        )

    available = set(bank.variables())
    mains = [m for m in cohort.mains if m in available]
    if not mains:
        raise SystemExit(f"cohort mains {cohort.mains} are not in this bank ({sorted(available)})")

    # STRIDE, NOT TRUNCATE. `build_cohort` emits stratum 0's simulees, then stratum 1's,
    # and so on, so `simulees[:limit]` is not a smaller cohort — it is the WEAKEST
    # candidates only. At limit=1200 of 4000 that is strata 0-2 of 8, and every absolute
    # accuracy, reliability and calibration figure computed from it describes the bottom
    # third of the ability range while reading like a population number.
    # ...and SHUFFLED, deterministically, so that any PREFIX is representative too. A
    # strided sample is still emitted in ascending-ability order, so a run stopped early —
    # which is how every run here has ended — would again describe only the weak end.
    # Shuffling makes "stopped at k sessions" a random subsample instead of a truncation.
    import numpy as _np

    simulees = list(cohort.simulees)
    if limit and limit < len(simulees):
        step = len(simulees) / limit
        simulees = [simulees[int(i * step)] for i in range(limit)]
    order = _np.random.default_rng(cohort.seed).permutation(len(simulees))
    simulees = [simulees[int(i)] for i in order]
    out_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / f"{arm_name}__{cohort.dgp}.jsonl"

    written = 0
    with results_path.open("w", encoding="utf-8") as handle:
        for index, simulee in enumerate(simulees):
            record = await run_session(
                orchestrator=orchestrator,
                bank=bank,
                simulee=simulee,
                mains=mains,
                arm=arm_name,
                max_steps=max_steps,
                keep_trace=(trace_every > 0 and index % trace_every == 0),
            )
            record["dgp"] = cohort.dgp
            handle.write(json.dumps(record) + "\n")
            written += 1
            if written % 100 == 0:
                print(f"  {arm_name} {cohort.dgp}: {written}/{len(simulees)}", flush=True)

    manifest = {
        "arm": arm_name,
        "arm_description": ARMS[arm_name].description,
        "dgp": cohort.dgp,
        "cohort_seed": cohort.seed,
        "n": written,
        "bank_id": cohort.bank_id,
        "mains": mains,
        "has_graph_module": HAS_GRAPH,
        "force_enable_edges": ARMS[arm_name].force_enable_edges,
        "results": results_path.name,
        "settings": {
            key: getattr(settings, key)
            for key in sorted(type(settings).model_fields)
            if key.startswith(("cat_", "graph_", "competency_", "orchestrator_", "code_"))
            and not key.endswith(("_api_key", "_key"))
        },
    }
    (out_dir / f"{arm_name}__{cohort.dgp}.manifest.json").write_text(
        json.dumps(manifest, indent=1), encoding="utf-8"
    )
    print(f"{arm_name} {cohort.dgp}: {written} sessions -> {results_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arm", required=True, choices=sorted(ARMS))
    parser.add_argument("--cohort", required=True, help="cohort JSON from make_cohort.py")
    parser.add_argument("--out", default="eval-results/runs")
    parser.add_argument("--limit", type=int, default=0, help="0 = the whole cohort")
    parser.add_argument("--max-steps", type=int, default=60)
    parser.add_argument(
        "--trace-every",
        type=int,
        default=50,
        help="keep a full step trace for every Nth session (0 = never)",
    )
    args = parser.parse_args()

    _apply_env(args.arm)

    from evaluation.dgp import Cohort

    cohort = Cohort.load(args.cohort)
    asyncio.run(
        _run_all(
            arm_name=args.arm,
            cohort=cohort,
            out_dir=Path(args.out),
            limit=args.limit,
            max_steps=args.max_steps,
            trace_every=args.trace_every,
        )
    )


if __name__ == "__main__":
    main()
