"""Shared fixtures: named banks, and the registry parametrisation.

Most tests want a SPECIFIC bank — they assert on `DA.5` or on a single-track bank — and
pinning them is honest, not lazy: parametrising an assertion about DA over three banks
either weakens it into vacuity or makes it fail on a bank it was never about.

Tests that assert an INVARIANT (every bank loads, every graph pairs with its bank, every
main's coverage requirement is reachable) take `any_profile` and run against all of them.
"""

from __future__ import annotations

import os

import pytest

from cat_engine.engine.services.code_adaptive import session as code_session
from cat_engine.engine.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
from cat_engine.engine.services.code_adaptive.llm_evaluator import LLMEvaluation
from cat_engine.engine.services.orchestrator import registry
from cat_engine.engine.services.orchestrator.grader import GraderAgent
from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator


@pytest.fixture(autouse=True)
def process_wide_state_is_restored():
    """Give every test the store topology it was written against.

    WHY THIS EXISTS

    Two pieces of state in this engine are deliberately process-wide — `registry.STORE`,
    because a deployment resolves its bank store once at startup, and the session table,
    because sessions are shared across replicas. Both are correct designs and both are
    invisible to a test that assumes it starts clean.

    They are invisible until a DSN is exported. With `BANK_DATABASE_URL` and
    `SESSION_DATABASE_URL` set for the whole suite — which is how anyone would validate a
    Postgres deployment — every `AssessmentModule()` rebinds `registry.STORE` to a
    SQL-backed store and every session it opens is written to a table nothing truncates.
    54 tests failed as a result, and almost none of them failed for a reason connected to
    what they were testing:

        test_facade    capacity fails closed → CapacityReached, because 300 earlier
                       tests' sessions were still in the table
        test_sql_*     unknown bank 'DA'; registered: [] → a fixture had DELETEd the bank
                       rows and a later test read the store it left behind

    The database paths were always covered — the three Postgres-gated files pass 74/74 on
    their own. What was missing was isolation BETWEEN files, so the two facts were
    indistinguishable: "the SQL store is broken" and "the previous test left it dirty".

    Restoring rather than resetting is deliberate: a test that legitimately installs its own
    store (`test_catalogue`, `test_facade`, `test_ingest`, `test_sql_backed_registry` all do)
    keeps doing so, and only the leak across the boundary is closed.
    """
    from cat_engine.engine.services.orchestrator import registry as _registry

    before = _registry.STORE
    try:
        yield
    finally:
        if _registry.STORE is not before:
            _registry.use_store(before)
        _registry.reset_caches()
        _clear_sql_sessions()


def _clear_sql_sessions() -> None:
    """Empty the shared session table, when there is one.

    A no-op in the default topology — sessions live in an `InMemorySessionStore` owned by
    the module instance, so they cannot outlive it. Only a configured DSN makes them
    persistent, and persistent-by-design is exactly what a test suite must undo.
    """
    dsn = os.environ.get("SESSION_DATABASE_URL", "").strip()
    if not dsn:
        return
    try:
        import psycopg

        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM assessment_session")
            conn.commit()
    except Exception:  # pragma: no cover - the DB is not up, or has no schema yet
        # Never fail a test because cleanup could not run: a test that passed did pass,
        # and the next one will rebuild the schema it needs.
        pass


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
