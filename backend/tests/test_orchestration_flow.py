"""A whole assessment, end to end, with the sandbox and both models stubbed.

Neither boundary is exercised for real: the sandbox costs money and needs network, and a
model is non-deterministic. What is tested is that the loop does the right thing with
whatever they return — including when they fail.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.code_adaptive import session as code_session_module
from app.services.code_adaptive.bank import JsonQuestionRepository
from app.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
from app.services.code_adaptive.llm_evaluator import LLMEvaluation
from app.services.code_adaptive.session import CodeAdaptiveSession
from app.services.orchestrator import registry
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator
from tests.conftest import orchestrator_for


@pytest.fixture(scope="module")
def bank():
    return registry.get_bank("DA")


@pytest.fixture
def stub_boundaries(monkeypatch):
    """Every code submission passes; no model is ever called."""

    def fake_run(code, tests, function_name):
        return ExecutionEvidence(
            compiled=True,
            execution_completed=True,
            passed_tests=len(tests),
            total_tests=len(tests),
            test_results=[TestOutcome(t["test_id"], True, 1.0) for t in tests],
        )

    monkeypatch.setattr(code_session_module, "run_submission", fake_run)
    monkeypatch.setattr(
        code_session_module, "llm_evaluate", lambda *a, **k: LLMEvaluation(available=False)
    )


@pytest.fixture
def engine(bank, stub_boundaries) -> Orchestrator:
    return orchestrator_for("DA", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository())))


@pytest.fixture(scope="module")
def targets(bank) -> list[str]:
    """Three variables, at least one measured by both modalities."""
    coverage = bank.coverage()
    mixed = [v for v, m in coverage.items() if len(m) > 1][:2]
    single = [v for v, m in coverage.items() if len(m) == 1][:1]
    return mixed + single


ANSWER_TEXT = (
    "A dataframe is a two dimensional labelled structure. I would inspect the schema "
    "first, check the null counts per column, then confirm the dtypes match what the "
    "downstream join expects, because a silent type coercion there is what usually "
    "produces the wrong row count later. I would also check the index for duplicates."
)


def answer_for(item, correct: bool = True):
    """A response of the right shape for the item's modality.

    Open and voice items arrive pre-evaluated — `GraderAgent` grades, it does not
    evaluate, and evaluation is async. The heuristic evaluator is the deterministic,
    offline path the production code already falls back to when the grader is
    unreachable, so it is what a stubbed session should use.
    """
    if item.modality == "mcq":
        index = int(item.payload["answer_index"])
        return index if correct else (index + 1) % len(item.payload["options"])
    if item.modality in ("open", "voice"):
        from app.schemas.voice import GradedVoiceResponse
        from app.services.voice.evaluator import (
            _heuristic_from_rubric,
            _rubric_from_payload,
            package_from_text,
        )

        rubric = _rubric_from_payload(item)
        package = package_from_text(
            item.item_id, ANSWER_TEXT if correct else "I do not know."
        )
        return GradedVoiceResponse(
            package=package,
            evaluation=_heuristic_from_rubric(
                item.item_id, rubric, package, flags=["STUBBED"]
            ),
            rubric=rubric,
        )
    return "def solution(*args, **kwargs):\n    return None\n"


async def run_session(engine, targets, *, correct=True, max_steps=60):
    state = engine.begin(targets)
    rng = np.random.default_rng(7)
    stop_reason = ""
    for _ in range(max_steps):
        state = await engine.fill_queue(state, use_llm=False, rng=rng)
        stop, stop_reason = engine.should_stop(state)
        if stop:
            break
        state = engine.ensure_presenting(state)
        nxt = engine.next_item(state)
        if nxt is None:
            stop_reason = "no_candidates_available"
            break
        item, _candidate = nxt
        state, _graded = engine.record_response(state, item, answer_for(item, correct))
        state = await engine.after_response(state, item, use_llm=False, rng=rng)
    return state, stop_reason


class TestFullSession:
    @pytest.mark.asyncio
    async def test_a_session_terminates_and_reports_every_variable(self, engine, targets):
        state, stop_reason = await run_session(engine, targets)
        report = engine.summarise(state, stop_reason)

        assert stop_reason
        assert {v.variable for v in report.variables} == set(targets)
        assert report.items_administered > 0
        # Every variable either finalised, or the session hit a budget bound.
        assert report.all_finalised or stop_reason in {
            "item_budget", "time_limit", "no_candidates_available"
        }

    @pytest.mark.asyncio
    async def test_no_item_is_ever_administered_twice(self, engine, targets):
        state, _ = await run_session(engine, targets)
        assert len(state.served_item_ids) == len(set(state.served_item_ids))

    @pytest.mark.asyncio
    async def test_both_modalities_are_actually_used(self, engine, targets, bank):
        """The point of the whole exercise: a mixed session must genuinely mix."""
        state, stop_reason = await run_session(engine, targets)
        report = engine.summarise(state, stop_reason)
        used = {m for v in report.variables for m in v.modalities_used}
        assert "mcq" in used
        assert "code" in used

    @pytest.mark.asyncio
    async def test_the_queue_never_exceeds_one_candidate_per_open_variable(self, engine, targets):
        state = engine.begin(targets)
        state = await engine.fill_queue(state, use_llm=False, rng=np.random.default_rng(1))
        assert len(state.queue) <= len(state.open_variables)
        assert set(state.queue) <= set(state.open_variables)

    @pytest.mark.asyncio
    async def test_presenting_excludes_that_competency_from_the_queue(self, engine, targets):
        state = engine.begin(targets)
        state = await engine.fill_queue(state, use_llm=False, rng=np.random.default_rng(2))
        state = engine.ensure_presenting(state)
        if state.presenting is None:
            pytest.skip("nothing ready to present")
        assert state.presenting.variable not in state.queue

    @pytest.mark.asyncio
    async def test_a_finalised_variable_keeps_no_queue_slot(self, engine, targets):
        state, _ = await run_session(engine, targets)
        finalised = {v for v, s in state.variables.items() if s.finalised}
        assert finalised, "expected at least one variable to finalise"
        assert not (finalised & set(state.queue))

    @pytest.mark.asyncio
    async def test_a_strong_candidate_ends_above_a_weak_one(self, engine, targets):
        strong, _ = await run_session(engine, targets, correct=True)
        weak, _ = await run_session(engine, targets, correct=False)
        for variable in targets:
            if strong.variables[variable].observations and weak.variables[variable].observations:
                assert strong.variables[variable].theta_hat > weak.variables[variable].theta_hat

    @pytest.mark.asyncio
    async def test_state_round_trips_through_serialisation(self, engine, targets):
        from app.schemas.orchestration import AssessmentState

        state, _ = await run_session(engine, targets, max_steps=3)
        assert AssessmentState.model_validate(state.model_dump()) == state


class TestInfrastructureFailure:
    """A failure of ours must never be recorded as a candidate's inability."""

    @pytest.mark.asyncio
    async def test_a_sandbox_failure_moves_no_estimate(self, bank, targets, monkeypatch):
        def broken(code, tests, function_name):
            return ExecutionEvidence(
                compiled=None,
                execution_completed=False,
                infrastructure_error=True,
                total_tests=len(tests),
                error_message="sandbox unavailable",
                test_results=[TestOutcome(t["test_id"], False, 0.0, "not_run") for t in tests],
            )

        monkeypatch.setattr(code_session_module, "run_submission", broken)
        monkeypatch.setattr(
            code_session_module, "llm_evaluate", lambda *a, **k: LLMEvaluation(available=False)
        )
        engine = orchestrator_for("DA", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository())))

        # Administered directly rather than through the queue: the point is what an
        # infrastructure failure does to an estimate, not which item selection happened to
        # offer. Every variable carrying code items also carries MCQ ones, so waiting for
        # the queue to serve a code item would make the test depend on the ranking.
        code_item = [i for i in bank.all_items() if i.modality == "code"][0]
        code_main = code_item.measures[0].variable.split(".")[0]

        state = engine.begin([code_main])
        before = state.variables[code_main]
        state, graded = engine.record_response(state, code_item, "def f():\n    pass\n")
        after = state.variables[code_main]

        assert any("SANDBOX_UNAVAILABLE" in f for f in graded.flags)

        assert after.theta_hat == pytest.approx(before.theta_hat)
        assert after.standard_error == pytest.approx(before.standard_error)
        assert after.observations == 0


class TestGrader:
    def test_mcq_grading_is_an_exact_comparison(self, bank):
        item = next(i for i in bank.all_items() if i.modality == "mcq")
        grader = GraderAgent()
        correct = grader.grade(item, int(item.payload["answer_index"]))
        wrong = grader.grade(item, (int(item.payload["answer_index"]) + 1) % len(item.payload["options"]))
        assert correct.outcomes[0]["score"] == 1.0
        assert wrong.outcomes[0]["score"] == 0.0

    def test_an_out_of_range_option_is_refused(self, bank):
        item = next(i for i in bank.all_items() if i.modality == "mcq")
        with pytest.raises(ValueError, match="out of range"):
            GraderAgent().grade(item, 99)

    def test_an_unknown_modality_is_refused_not_guessed(self, bank):
        """A modality with no grader must raise, never fall through to a default."""
        item = next(i for i in bank.all_items() if i.modality == "mcq")
        faked = item.model_copy(update={"modality": "telepathy"})
        with pytest.raises(NotImplementedError):
            GraderAgent().grade(faked, "an essay")

    def test_an_open_item_will_not_grade_raw_text(self, bank):
        """`open`/`voice` are graded, not evaluated, here — the caller must evaluate first.

        Accepting a bare string would mean the grader inventing an evaluation, which is
        precisely the boundary the design forbids.
        """
        item = next(i for i in bank.all_items() if i.modality == "mcq")
        faked = item.model_copy(update={"modality": "open", "open": {}})
        with pytest.raises(TypeError, match="GradedVoiceResponse"):
            GraderAgent().grade(faked, "an essay")


class TestUnknownVariable:
    def test_a_variable_with_no_coverage_is_refused_at_the_door(self, engine):
        """It would otherwise sit open forever and stop the session ever completing."""
        with pytest.raises(ValueError, match="no bank coverage"):
            engine.begin(["NOT-A-VARIABLE"])
