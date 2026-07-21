"""End-to-end behaviour of the public entry point, with the sandbox and model stubbed.

Neither boundary is exercised for real: the sandbox costs money and needs network, and the
model is non-deterministic. What matters here is that the engine does the right thing with
whatever those boundaries return — including when they fail.
"""

from __future__ import annotations

import pytest

from app.schemas.code_adaptive import SessionState
from app.services.code_adaptive import session as session_module
from app.services.code_adaptive.bank import JsonQuestionRepository
from app.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
from app.services.code_adaptive.llm_evaluator import LLMEvaluation
from app.services.code_adaptive.session import CodeAdaptiveSession


@pytest.fixture(scope="module")
def repository() -> JsonQuestionRepository:
    return JsonQuestionRepository()


@pytest.fixture
def engine(repository) -> CodeAdaptiveSession:
    return CodeAdaptiveSession(repository)


@pytest.fixture
def target(repository) -> str:
    return max(repository.coverage(), key=repository.coverage().get)


def stub_execution(question: dict, *, passing: bool) -> ExecutionEvidence:
    tests = question["tests"]
    return ExecutionEvidence(
        compiled=True,
        execution_completed=True,
        passed_tests=len(tests) if passing else 0,
        total_tests=len(tests),
        test_results=[
            TestOutcome(t["test_id"], passing, 1.0 if passing else 0.0,
                        "" if passing else "wrong_output")
            for t in tests
        ],
    )


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(session_module, "llm_evaluate", lambda *a, **k: LLMEvaluation(available=False))


def install_execution(monkeypatch, repository, *, passing: bool):
    by_function = {q.function_name: q.model_dump() for q in repository.all_questions()}

    def fake(code, tests, function_name):
        return stub_execution(by_function[function_name], passing=passing)

    monkeypatch.setattr(session_module, "run_submission", fake)


class TestLifecycle:
    def test_a_new_session_reports_nothing_as_measured(self, engine, target):
        state = engine.begin(target)
        assert state.answered_question_ids == []
        snapshot = engine.summarise(state, _stop()).competencies
        assert all(c.level is None for c in snapshot)

    def test_a_self_rating_seeds_the_prior_without_becoming_a_measurement(self, engine, target):
        rated = engine.begin(target, self_rating=5)
        state = rated.competencies[target]
        assert state["alpha"] > 1.0
        # evidence_count stays 0, so `observed` is False and no level is reported
        assert state["evidence_count"] == 0
        assert engine.summarise(rated, _stop()).level is None

    def test_state_round_trips_through_serialisation(self, engine, target):
        state = engine.begin(target, self_rating=3)
        restored = SessionState.model_validate(state.model_dump())
        assert restored == state

    def test_next_question_offers_something_eligible(self, engine, target):
        selected = engine.next_question(engine.begin(target), use_llm=False)
        assert selected is not None
        assert selected.rank == 1
        assert selected.criterion == "KL"      # first question, estimate still vague
        assert selected.normalized_regret == pytest.approx(0.0)


class TestSubmission:
    def test_passing_everything_raises_mastery_and_lowers_the_error(
        self, engine, repository, target, monkeypatch, no_llm
    ):
        install_execution(monkeypatch, repository, passing=True)
        state = engine.begin(target)
        selected = engine.next_question(state, use_llm=False)

        before = engine.summarise(state, _stop())
        state, result, _ = engine.record_submission(state, selected.question_id, "code")
        after = engine.summarise(state, _stop())

        assert result.passed_tests == result.total_tests
        assert after.mastery > before.mastery
        assert after.standard_error < before.standard_error
        assert after.level is not None      # now measured

    def test_failing_everything_lowers_mastery(
        self, engine, repository, target, monkeypatch, no_llm
    ):
        install_execution(monkeypatch, repository, passing=False)
        state = engine.begin(target)
        selected = engine.next_question(state, use_llm=False)
        before = engine.summarise(state, _stop()).mastery
        state, _, _ = engine.record_submission(state, selected.question_id, "code")
        assert engine.summarise(state, _stop()).mastery < before

    def test_a_question_is_never_served_twice(
        self, engine, repository, target, monkeypatch, no_llm
    ):
        install_execution(monkeypatch, repository, passing=True)
        state = engine.begin(target)
        served = []
        for _ in range(3):
            selected = engine.next_question(state, use_llm=False)
            if selected is None:
                break
            served.append(selected.question_id)
            state, _, _ = engine.record_submission(state, selected.question_id, "code")
        assert len(served) == len(set(served))

    def test_the_scoring_split_is_recorded_with_every_result(
        self, engine, repository, target, monkeypatch, no_llm
    ):
        """A score is not interpretable without knowing which weights produced it."""
        install_execution(monkeypatch, repository, passing=True)
        state = engine.begin(target)
        selected = engine.next_question(state, use_llm=False)
        _, result, _ = engine.record_submission(state, selected.question_id, "code")
        assert result.weight_fingerprint
        assert result.weight_shares
        assert sum(result.weight_shares.values()) == pytest.approx(1.0, abs=0.01)

    def test_an_unknown_question_is_refused(self, engine, target):
        with pytest.raises(ValueError):
            engine.record_submission(engine.begin(target), "no-such-question", "code")


class TestInfrastructureFailure:
    """A failure of ours must never be recorded as a candidate's inability."""

    def test_a_sandbox_failure_scores_nothing_and_moves_no_estimate(
        self, engine, repository, target, monkeypatch, no_llm
    ):
        def broken(code, tests, function_name):
            return ExecutionEvidence(
                compiled=None,                 # not False: we do not KNOW whether it compiles
                execution_completed=False,
                infrastructure_error=True,
                total_tests=len(tests),
                error_message="sandbox unavailable",
                test_results=[TestOutcome(t["test_id"], False, 0.0, "not_run") for t in tests],
            )

        monkeypatch.setattr(session_module, "run_submission", broken)
        state = engine.begin(target)
        selected = engine.next_question(state, use_llm=False)
        before = engine.summarise(state, _stop())

        state, result, _ = engine.record_submission(state, selected.question_id, "code")
        after = engine.summarise(state, _stop())

        assert result.overall_score is None
        assert result.compiled is None
        assert all(v is None for v in result.criterion_scores.values())
        assert any("SANDBOX_UNAVAILABLE" in f for f in result.flags)
        assert after.mastery == pytest.approx(before.mastery)
        assert after.standard_error == pytest.approx(before.standard_error)


class TestStopping:
    def test_a_session_stops_and_reports_why(
        self, engine, repository, target, monkeypatch, no_llm
    ):
        install_execution(monkeypatch, repository, passing=True)
        state = engine.begin(target)
        stop = _stop()
        for _ in range(12):
            selected = engine.next_question(state, use_llm=False)
            if selected is None:
                break
            state, _, stop = engine.record_submission(state, selected.question_id, "code")
            if stop.should_stop:
                break
        assert stop.should_stop is True
        assert stop.reason
        report = engine.summarise(state, stop)
        assert report.questions_answered >= 1
        assert report.band != "Not assessed"


def _stop():
    from app.schemas.code_adaptive import StopDecision

    return StopDecision(should_stop=False)
