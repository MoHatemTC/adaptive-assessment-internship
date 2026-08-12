"""The claim the whole collapse rests on: the facade changed nothing.

WHAT THIS DOES

Runs one identical, seeded assessment twice — once through `AssessmentModule`, and once by
driving `Orchestrator` directly the way `cat_engine/evaluation/` does — and asserts the two
produce the same report. Same theta, same standard error, same band, same stopping reason,
same items, in the same order.

WHY IT IS THE TEST THAT MATTERS

This is the direct successor to `test_parity_inprocess_vs_services.py`, which asserted the
same equality across an HTTP boundary and was the evidence that splitting the monolith into
services changed no measurement. The boundary is gone; the question it answered is not.
Every other test in this suite checks that something does what its docstring says. None of
them would notice the failure that actually matters here: that the FACADE — its answer
coercion, its scope handling, its session store, its ordering of `evaluate_voice` against
`record_response` — quietly produces a different session from the one the engine would
have run on its own.

A drift of one item in a shortlist, or one response evaluated in the wrong order, produces
a session that is internally consistent, plausible, and not the one the evaluation harness
measured — and the harness is where every claim about this engine's accuracy comes from.

If this ever fails, the number to distrust is not the one in the report. It is every number
in `docs/evidence.md`.

WHAT IS EXCLUDED FROM THE COMPARISON, AND WHY THAT IS SAFE

Wall clock. `started_at`, `elapsed_minutes` and `seconds_by_item` are measured, not
computed, and two runs of anything differ in them. Nothing downstream of them enters an
estimate — they inform the time-aware selection budget, which is why the two sessions are
run back to back rather than compared across a suite.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pytest

#: TWO BANKS, BECAUSE ONE OF THEM DOES NOT EXERCISE THE PATH THAT CHANGED.
#:
#: `DA` administers only mcq and code under every seed tried, so a parity run over it alone
#: would never evaluate a rubric — and the rubric ordering (`evaluate_voice` before
#: `record_response`) is the single non-mechanical thing the collapse had to get right. `AIE`
#: administers all three modalities, so it is the case that actually covers it. `DA` is kept
#: because it is the bank the service-era parity test used, and dropping it would quietly
#: narrow what this file inherited.
BANKS = ("DA", "AIE")
SEED = 4242
#: A ceiling, not an expectation. Both runs stop on their own rule; this only stops a broken
#: one spinning.
MAXIMUM_ITEMS = 60

TYPED_ANSWER = (
    "I would profile the query first, add an index on the join column, and measure "
    "again before changing anything else rather than guessing at the cause."
)
CODE_ANSWER = "def solve(x):\n    return x\n"


def answer_for(item, bank):
    """The same answer for the same item, whichever path is asking.

    Deterministic, and correct where correctness is knowable, because two runs that answered
    differently would prove nothing about the collapse.
    """
    if item.modality == "mcq":
        return int(bank.get(item.item_id).payload["answer_index"])
    if item.modality == "code":
        return CODE_ANSWER
    return TYPED_ANSWER


def comparable(report: dict) -> dict:
    """The report minus the two things that identify a RUN rather than a measurement.

    THE CLOCK. `seconds_by_item` is measured, not computed, and two runs of anything differ
    in it. Nothing downstream of it enters an estimate.

    THE SESSION ID. It is a uuid per run and it is embedded in every evidence id —
    `{session}:{item}:{attempt}:{modality}:{node}:{criterion}` — because that is what makes
    an evidence id unique and a replay recognisable. Two runs are two sessions, so those ids
    differ by construction and by design.

    Normalising it rather than dropping the ids: their SHAPE is the thing worth comparing.
    An evidence id naming a different item, a different attempt number or a different node
    would still fail this, which is the failure that would matter.

    THE SCOPE ANNOTATION. `scope` is a facade-level field with no engine counterpart, and it
    is None for exactly the assessments this test runs — whole-bank ones. Dropped outright
    rather than special-cased on None, so a scoped session leaking into this comparison
    fails somewhere honest instead of quietly comparing equal.
    """
    stripped = {k: v for k, v in report.items() if k not in ("seconds_by_item", "scope")}
    text = json.dumps(stripped, sort_keys=True, default=str)
    text = re.sub(r"asmt_[0-9a-f]{12}", "asmt_SESSION", text)
    return json.loads(text)


@pytest.fixture(params=BANKS, ids=BANKS)
def bank_id(request) -> str:
    """The bank both runs use. Parametrised so each case is a separate, named failure."""
    return request.param


@pytest.fixture()
def engine_report(stub_boundaries, bank_id):
    """One assessment, driven exactly as `cat_engine/evaluation/` drives it."""
    import asyncio

    from cat_engine.engine.schemas.voice import VoiceResponsePackage
    from cat_engine.engine.services.code_adaptive import (
        CodeAdaptiveSession,
        JsonQuestionRepository,
    )
    from cat_engine.engine.services.orchestrator import registry
    from cat_engine.engine.services.orchestrator.grader import GraderAgent
    from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator
    from cat_engine.engine.services.voice.evaluator import evaluate as evaluate_voice

    bank = registry.get_bank(bank_id)
    orchestrator = Orchestrator(
        bank,
        GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository())),
        graph=registry.get_graph_service(bank_id),
        coverage_critical_only=registry.coverage_policy(bank_id),
        bank_id=bank_id,
    )

    async def run() -> dict:
        rng = np.random.default_rng(SEED)
        state = orchestrator.begin(bank.variables())
        state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
        state = orchestrator.ensure_presenting(state)

        for _ in range(MAXIMUM_ITEMS):
            stop, reason = orchestrator.should_stop(state)
            if stop:
                return orchestrator.summarise(state, reason).model_dump()
            pair = orchestrator.next_item(state)
            if pair is None:
                break
            item, _candidate = pair
            response = answer_for(item, bank)
            if item.modality in ("open", "voice"):
                # THE ORDERING THE FACADE HAS TO MATCH. Evaluation is async and
                # `record_response` is sync, so the rubric call happens HERE, before
                # grading. `facade._coerce_answer` does the same two steps in the same
                # order; this fixture is what proves it.
                package = VoiceResponsePackage.from_text(item.item_id, response)
                response = await evaluate_voice(item, package, use_llm=False)
            state, _graded = orchestrator.record_response(state, item, response)
            state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)
            state = orchestrator.ensure_presenting(state)

        stop, reason = orchestrator.should_stop(state)
        return orchestrator.summarise(state, reason).model_dump()

    return asyncio.run(run())


@pytest.fixture()
def facade_report(stub_boundaries, module, bank_id):
    """The same assessment, through the surface a host actually calls."""
    import asyncio

    from cat_engine.engine.services.orchestrator import registry

    bank = registry.get_bank(bank_id)

    async def run() -> dict:
        state = await module.begin(bank_id=bank_id, use_llm=False, seed=SEED)
        for _ in range(MAXIMUM_ITEMS):
            if state.stop:
                break
            item = bank.get(state.presenting.item.item_id)
            response = answer_for(item, bank)
            kwargs: dict = {"use_llm": False}
            if item.modality == "mcq":
                kwargs["mcq"] = response
            elif item.modality == "code":
                kwargs["code"] = response
            else:
                kwargs["transcript"] = response
            state = await module.answer(state.session_id, **kwargs)

        assert state.stop, "the facade run never stopped"
        return state.report.model_dump()

    return asyncio.run(run())


class TestOneAssessmentRunBothWays:
    def test_the_two_reports_are_identical(self, engine_report, facade_report):
        """Field for field, competency for competency.

        Not "close enough". A posterior is a 41-point grid of floats and the update is
        arithmetic; if the two paths agree at all they agree exactly, and a tolerance here
        would hide the one class of bug this test exists to find.
        """
        assert comparable(facade_report) == comparable(engine_report)

    def test_the_same_items_were_administered_in_the_same_order(
        self, engine_report, facade_report
    ):
        """Stated separately because it is the first thing to look at when the above fails.

        Selection diverging is a different bug from the posterior diverging, and the
        difference decides where to look — a shortlist that ranked differently, or an update
        that arrived differently.
        """
        assert list(facade_report["seconds_by_item"]) == list(
            engine_report["seconds_by_item"]
        )

    def test_every_competency_reaches_the_same_estimate_and_stop(
        self, engine_report, facade_report
    ):
        """The measurement itself, spelled out — so a failure names theta, the standard
        error or the reason, rather than pointing at a dict."""
        expected = {v["variable"]: v for v in engine_report["variables"]}
        actual = {v["variable"]: v for v in facade_report["variables"]}
        assert set(actual) == set(expected)
        for variable, want in expected.items():
            got = actual[variable]
            assert got["theta_hat"] == want["theta_hat"], variable
            assert got["standard_error"] == want["standard_error"], variable
            assert got["level"] == want["level"], variable
            assert got["band"] == want["band"], variable
            assert got["stop_reason"] == want["stop_reason"], variable
            assert got["observations"] == want["observations"], variable
            assert got["converged"] == want["converged"], variable

    def test_the_graph_reached_the_same_conclusions(self, engine_report, facade_report):
        """Propagation ran through `InProcessPropagation` on both sides, but the facade
        reaches it through `Wiring` and the engine path builds its own.

        Node status, mastery, the evidence ledger and the unvalidated-edge forecast, all the
        way down.
        """
        assert comparable(facade_report)["graph"] == comparable(engine_report)["graph"]
