"""What a candidate chooses at the start, and what they may run before submitting."""

from __future__ import annotations

import pytest

from app.config.settings import settings
from app.services.code_adaptive import trial
from app.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
from app.services.orchestrator.bank import JsonUnifiedBank
from app.services.orchestrator.grader import GraderAgent


@pytest.fixture
def bank() -> JsonUnifiedBank:
    return JsonUnifiedBank()


# --- tracks ------------------------------------------------------------------
def test_the_bank_offers_exactly_the_five_tracks(bank):
    tracks = bank.tracks()
    assert [t["code"] for t in tracks] == ["T1", "T2", "T3", "T4", "T5"]
    for track in tracks:
        assert track["name"], f"{track['code']} has no name to show a candidate"
        assert track["items"] > 0


def test_a_track_is_named_by_the_items_that_are_about_it(bank):
    """Not by every item that touches it.

    A pandas question measures T2.1 at 0.8 and T1.1 at 0.2. Grouping by every measured
    variable would file it under both tracks, and a candidate choosing Python would be
    handed questions about dataframes. `tracks()` groups by the heaviest measure, which is
    what an item is actually about — this asserts the outcome, not the mechanism.
    """
    names = {t["code"]: t["name"] for t in bank.tracks()}
    assert "Python" in names["T1"]
    assert "Data" in names["T2"]
    assert "MLOps" in names["T5"]


def test_own_variables_are_the_ones_the_track_is_responsible_for(bank):
    for track in bank.tracks():
        assert track["own_variables"], f"{track['code']} owns nothing"
        assert all(v.startswith(track["code"] + ".") for v in track["own_variables"])
        # Cross-loaded variables stay assessable, so `variables` is the wider set.
        assert set(track["own_variables"]) <= set(track["variables"])


def test_every_track_can_actually_be_assessed(bank):
    """A track a candidate can choose but the engine cannot serve is a dead end."""
    for track in bank.tracks():
        for variable in track["own_variables"]:
            assert bank.shortlist(variable, exclude=set()), (
                f"{track['code']}: {variable} is offered but has no active items"
            )


# --- trial runs --------------------------------------------------------------
def _code_question(bank: JsonUnifiedBank) -> dict:
    item = next(i for i in bank.all_items() if i.modality == "code")
    return GraderAgent.as_code_question(item)


def test_only_public_cases_are_ever_runnable(bank):
    """The invariant the whole feature depends on.

    If a trial run could reach the hidden cases, a candidate could converge on a lookup
    table by trial and error and the score would measure persistence, not competency.
    """
    for item in bank.all_items():
        if item.modality != "code":
            continue
        question = GraderAgent.as_code_question(item)
        for test in trial.public_tests(question, limit=99):
            assert test["visibility"] == "public"


def test_an_unmarked_case_is_treated_as_hidden():
    """Fails closed. An authoring slip must cost an example, never leak one."""
    question = {"tests": [{"test_id": "t1"}, {"test_id": "t2", "visibility": "public"}]}
    assert [t["test_id"] for t in trial.public_tests(question, limit=99)] == ["t2"]


def test_the_number_of_examples_is_bounded_by_configuration(bank):
    question = _code_question(bank)
    assert len(trial.public_tests(question)) <= settings.code_trial_run_tests


def test_a_trial_run_reports_per_case_outcomes(bank, monkeypatch):
    question = _code_question(bank)
    tests = trial.public_tests(question)

    def _fake_run(code, given_tests, function_name):
        assert given_tests == tests  # public only reaches the sandbox
        return ExecutionEvidence(
            compiled=True,
            execution_completed=True,
            passed_tests=1,
            total_tests=len(given_tests),
            test_results=[
                TestOutcome(t["test_id"], index == 0, 1.0, "" if index == 0 else "wrong_output",
                            "" if index == 0 else "returned 0")
                for index, t in enumerate(given_tests)
            ],
        )

    monkeypatch.setattr(trial, "run_submission", _fake_run)
    result = trial.trial_run(question, "def f(): pass")

    assert result.available and result.compiled is True
    assert result.total == len(tests) and result.passed == 1
    assert result.cases[0].passed and not result.cases[1].passed
    assert result.cases[1].detail == "returned 0"


def test_a_sandbox_failure_is_not_reported_as_the_candidate_failing(bank, monkeypatch):
    """The same distinction the graded path draws. `compiled` stays unknown, not False."""
    def _broken(code, tests, function_name):
        return ExecutionEvidence(
            compiled=None,
            execution_completed=False,
            infrastructure_error=True,
            total_tests=len(tests),
            error_message="quota exceeded",
        )

    monkeypatch.setattr(trial, "run_submission", _broken)
    result = trial.trial_run(_code_question(bank), "def f(): pass")

    assert result.available is False
    assert result.compiled is None
    assert result.cases == []
    assert "sandbox" in result.error_message.lower()


def test_a_trial_result_carries_no_score(bank, monkeypatch):
    """Structural, not incidental: there is no field through which one could travel.

    The graded path is the only thing that may move an estimate. A trial result that
    carried a score would be one refactor away from being fed to the learner model.
    """
    monkeypatch.setattr(
        trial, "run_submission",
        lambda code, tests, fn: ExecutionEvidence(
            compiled=True, execution_completed=True, total_tests=len(tests),
            test_results=[TestOutcome(t["test_id"], True, 1.0) for t in tests],
        ),
    )
    result = trial.trial_run(_code_question(bank), "def f(): pass")
    fields = set(vars(result)) | {"passed", "total"}
    assert not {"score", "overall_score", "mastery", "theta"} & fields
