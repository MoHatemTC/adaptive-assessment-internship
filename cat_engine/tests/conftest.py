"""Shared fixtures: named banks, and the registry parametrisation.

Most tests want a SPECIFIC bank — they assert on `DA.5` or on a single-track bank — and
pinning them is honest, not lazy: parametrising an assertion about DA over three banks
either weakens it into vacuity or makes it fail on a bank it was never about.

Tests that assert an INVARIANT (every bank loads, every graph pairs with its bank, every
main's coverage requirement is reachable) take `any_profile` and run against all of them.
"""

from __future__ import annotations

import pytest

from cat_engine.engine.services.code_adaptive import session as code_session
from cat_engine.engine.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
from cat_engine.engine.services.code_adaptive.llm_evaluator import LLMEvaluation
from cat_engine.engine.services.orchestrator import registry
from cat_engine.engine.services.orchestrator.grader import GraderAgent
from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator


@pytest.fixture
def stub_boundaries(monkeypatch):
    """Every code submission compiles and passes; no model is ever called.

    Neither boundary is exercised for real: the sandbox costs money and needs network,
    and a model is not deterministic. What is under test is what the loop does with
    whatever they return.
    """

    def fake_run(code, tests, function_name):
        return ExecutionEvidence(
            compiled=True,
            execution_completed=True,
            passed_tests=len(tests),
            total_tests=len(tests),
            test_results=[TestOutcome(t["test_id"], True, 1.0) for t in tests],
        )

    monkeypatch.setattr(code_session, "run_submission", fake_run)
    monkeypatch.setattr(
        code_session, "llm_evaluate", lambda *a, **k: LLMEvaluation(available=False)
    )


@pytest.fixture(scope="session")
def bank_items() -> dict[str, tuple[str, int]]:
    """One active item of each modality from the DA bank, with the MCQ's answer index.

    Read from the engine rather than hardcoded: an item id pinned in a test is an item id
    that stops existing the first time a bank is rebuilt, and the failure then looks like a
    grading bug rather than a stale fixture.
    """
    chosen: dict[str, tuple[str, int]] = {}
    for item in registry.get_bank("DA").all_items():
        if item.status != "active":
            continue
        if item.modality == "mcq" and "mcq" not in chosen:
            chosen["mcq"] = (item.item_id, int(item.payload["answer_index"]))
        elif item.modality == "code" and "code" not in chosen:
            chosen["code"] = (item.item_id, 0)
        elif item.modality in ("open", "voice") and "open" not in chosen:
            chosen["open"] = (item.item_id, 0)
    missing = {"mcq", "code", "open"} - set(chosen)
    assert not missing, f"the DA bank no longer offers {missing}"
    return chosen


@pytest.fixture
def module():
    """A freshly built `AssessmentModule`, with the config guard reset around it.

    Engine settings are a process-wide singleton, so `CatConfig.apply` remembers the
    measurement policy it applied and refuses a second module that disagrees. That guard is
    correct in production and would make every test after the first one fail, so it is
    reset here — before AND after, so a test that builds its own module with different
    policy cannot leak that policy into the next one.
    """
    from cat_engine import AssessmentModule
    from cat_engine.config import _reset_for_tests

    _reset_for_tests()
    try:
        yield AssessmentModule()
    finally:
        _reset_for_tests()
        registry.reset_caches()


@pytest.fixture(params=sorted(registry.REGISTRY), ids=sorted(registry.REGISTRY))
def any_profile(request) -> registry.BankProfile:
    """Every registered bank in turn. For invariants, not for behaviour."""
    return registry.REGISTRY[request.param]


@pytest.fixture(scope="session")
def da_bank():
    """The Data Analysis bank: one main, six sub-competencies, all three modalities."""
    return registry.get_bank("DA")


@pytest.fixture(scope="session")
def da_graph():
    return registry.get_graph_service("DA")


@pytest.fixture(scope="session")
def aie_bank():
    """The AI Engineer bank: three mains, 33 sub-competencies, mcq/code/voice."""
    return registry.get_bank("AIE")


@pytest.fixture(scope="session")
def aie_graph():
    return registry.get_graph_service("AIE")


def orchestrator_for(bank_id: str, grader: GraderAgent | None = None) -> Orchestrator:
    """An orchestrator wired to one registered bank AND its graph.

    Always pass the graph. An orchestrator built without one falls back to the active
    bank's, which is how a DA-bank test ends up evaluating C1/C3/C6 coverage nodes.
    """
    return Orchestrator(
        registry.get_bank(bank_id),
        grader or GraderAgent(),
        graph=registry.get_graph_service(bank_id),
        coverage_critical_only=registry.profile(bank_id).coverage_critical_only,
        bank_id=bank_id,
    )
