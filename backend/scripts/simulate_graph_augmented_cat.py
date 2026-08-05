#!/usr/bin/env python3
from __future__ import annotations

"""
Run a deterministic simulation harness for the CAT engine.

This is meant for flag experiments in local/dev environments, not for measuring
real-world model quality.
"""

import argparse
import asyncio
import os
from dataclasses import dataclass

import numpy as np


def _set_env_bool(name: str, value: bool) -> None:
    os.environ[name] = "true" if value else "false"


def _install_code_stubs() -> None:
    """Disable networked LLM/sandbox paths in the code engine."""
    from app.services.code_adaptive import session as code_session_module
    from app.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
    from app.services.code_adaptive.llm_evaluator import LLMEvaluation

    def fake_run(code: str, tests: list[dict], function_name: str):
        return ExecutionEvidence(
            compiled=True,
            execution_completed=True,
            passed_tests=len(tests),
            total_tests=len(tests),
            test_results=[TestOutcome(t["test_id"], True, 1.0) for t in tests],
        )

    code_session_module.run_submission = fake_run  # type: ignore[assignment]
    code_session_module.llm_evaluate = lambda *a, **k: LLMEvaluation(available=False)  # type: ignore[assignment]


@dataclass(frozen=True)
class SimulationResult:
    items_administered: int
    stop_reason: str
    variables_finalised: int


async def run_one_session(*, orchestrator, bank, targets: list[str], seed: int, max_steps: int) -> SimulationResult:
    state = orchestrator.begin(targets)
    rng = np.random.default_rng(seed)

    stop_reason = ""
    for _ in range(max_steps):
        state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
        state = orchestrator.ensure_presenting(state)
        stop, stop_reason = orchestrator.should_stop(state)
        if stop:
            break
        nxt = orchestrator.next_item(state)
        if nxt is None:
            stop_reason = "no_candidates_available"
            break
        item, _candidate = nxt

        if item.modality == "mcq":
            chosen = int(item.payload["answer_index"])
            response = chosen
        else:
            # Code grader expects source text.
            response = "def solve(*args, **kwargs):\n    return None\n"

        state, _graded = orchestrator.record_response(state, item, response)
        state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)

    finalised = sum(1 for s in state.variables.values() if s.finalised)
    return SimulationResult(
        items_administered=state.items_administered,
        stop_reason=stop_reason,
        variables_finalised=finalised,
    )


class FilteredBank:
    """Drop modalities that require networked evaluation."""

    def __init__(self, base, allowed_modalities: set[str]) -> None:
        self._base = base
        self._allowed = allowed_modalities

    def all_items(self):
        return [i for i in self._base.all_items() if i.modality in self._allowed]

    def get(self, item_id: str):
        item = self._base.get(item_id)
        return item if item is not None and item.modality in self._allowed else None

    def shortlist(self, variable: str, exclude: set[str]):
        return [i for i in self._base.shortlist(variable, exclude=exclude) if i.modality in self._allowed]

    def variables(self):
        return self._base.variables()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--max-steps", type=int, default=30)
    parser.add_argument("--bank", default=None, help="registered bank id (default: ACTIVE_BANK)")
    parser.add_argument("--mains", type=int, default=2, help="how many main competencies to run")

    # Graph flags
    parser.add_argument("--graph-filtering", action="store_true")
    parser.add_argument("--graph-upward-inference", action="store_true")
    parser.add_argument("--graph-descendant-blocking", action="store_true")
    parser.add_argument("--graph-utility", action="store_true")
    parser.add_argument("--graph-convergence-gate", action="store_true")
    parser.add_argument("--graph-shadow-mode", action="store_true")

    args = parser.parse_args()

    # Set env vars before importing settings.
    _set_env_bool("GRAPH_FILTERING_ENABLED", args.graph_filtering)
    _set_env_bool("GRAPH_UPWARD_INFERENCE_ENABLED", args.graph_upward_inference)
    _set_env_bool("GRAPH_DESCENDANT_BLOCKING_ENABLED", args.graph_descendant_blocking)
    _set_env_bool("GRAPH_UTILITY_ENABLED", args.graph_utility)
    _set_env_bool("GRAPH_CONVERGENCE_GATE_ENABLED", args.graph_convergence_gate)
    _set_env_bool("GRAPH_SHADOW_MODE", args.graph_shadow_mode)

    # Install deterministic stubs for the code engine.
    _install_code_stubs()

    from app.config.settings import settings
    from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository
    from app.services.orchestrator import registry
    from app.services.orchestrator.grader import GraderAgent
    from app.services.orchestrator.orchestrator import Orchestrator

    bank_id = registry.resolve_bank_id(args.bank)
    base_bank = registry.get_bank(bank_id)
    # Open/voice items need a networked evaluator, so the simulation runs on the
    # deterministic modalities only. Reported below, never silently assumed.
    bank = FilteredBank(base_bank, allowed_modalities={"mcq", "code"})

    code_engine = CodeAdaptiveSession(JsonQuestionRepository())
    orchestrator = Orchestrator(
        bank,
        GraderAgent(code_engine=code_engine),
        graph=registry.get_graph_service(bank_id),
        coverage_critical_only=registry.profile(bank_id).coverage_critical_only,
        bank_id=bank_id,
    )

    targets = base_bank.variables()[: max(1, args.mains)]
    if not targets:
        raise SystemExit("No variables available in bank.")

    results: list[SimulationResult] = []
    for i in range(args.sessions):
        res = await run_one_session(
            orchestrator=orchestrator,
            bank=base_bank,
            targets=targets,
            seed=args.seed + i,
            max_steps=args.max_steps,
        )
        results.append(res)

    mean_items = float(np.mean([r.items_administered for r in results])) if results else 0.0
    mean_finalised = float(np.mean([r.variables_finalised for r in results])) if results else 0.0

    summary = {
        "bank_id": bank_id,
        "simulated_modalities": ["mcq", "code"],
        "settings": {
            "graph_filtering_enabled": settings.graph_filtering_enabled,
            "graph_upward_inference_enabled": settings.graph_upward_inference_enabled,
            "graph_descendant_blocking_enabled": settings.graph_descendant_blocking_enabled,
            "competency_graph_enabled": settings.competency_graph_enabled,
            "graph_utility_enabled": settings.graph_utility_enabled,
            "graph_convergence_gate_enabled": settings.graph_convergence_gate_enabled,
            "graph_shadow_mode": settings.graph_shadow_mode,
        },
        "targets": targets,
        "sessions": args.sessions,
        "mean_items_administered": mean_items,
        "mean_variables_finalised": mean_finalised,
        "per_session": [r.__dict__ for r in results],
    }
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())

