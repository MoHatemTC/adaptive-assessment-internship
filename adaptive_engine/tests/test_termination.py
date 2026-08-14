"""Every way a run ends, and what each ending claims."""

from __future__ import annotations

import pytest

from adaptive_engine import (
    AssessmentFinished,
    QuestionResponse,
    advance_assessment,
    compile_assessment,
    start_assessment,
)
from adaptive_engine.tests.conftest import bank_for, definition_with, mcq_item


def run_to_the_end(compiled, *, seed=5, answer_for=None, limit=80):
    decision = start_assessment(compiled, seed=seed)
    steps = 0
    while decision.status == "question" and steps < limit:
        answer = answer_for(steps) if answer_for else 0
        decision = advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(
                question_id=decision.question.item.item_id, answer=answer
            ),
        )
        steps += 1
    assert decision.status != "question", "the run must actually end"
    return decision


def test_convergence_returns_status_completed(fast_convergence):
    # Highly discriminating items clustered where the alternating answer pattern keeps
    # the estimate, so the shipped band-probability stop can fire quickly.
    items = [
        mcq_item(f"q{i}", "python", a=3.0, b=-0.6 + 0.15 * i) for i in range(9)
    ]
    compiled = compile_assessment(definition_with(items=items))
    decision = run_to_the_end(compiled, answer_for=lambda step: 0 if step % 2 else 1)
    assert decision.status == "completed"
    assert decision.converged is True
    assert decision.stop_reason == "all_variables_finalised"
    assert decision.report is not None and decision.question is None
    assert all(row.converged for row in decision.report.variables)


def test_the_session_item_budget_returns_status_terminated(definition, monkeypatch):
    from cat_engine.engine.config.settings import settings

    monkeypatch.setattr(settings, "orchestrator_max_items", 3)
    compiled = compile_assessment(definition)
    decision = run_to_the_end(compiled)
    assert decision.status == "terminated"
    assert decision.converged is False
    assert decision.stop_reason == "item_budget"
    assert decision.state.questions_answered == 3


def test_bank_exhaustion_terminates_without_claiming_convergence():
    items = [mcq_item("only-1", "python"), mcq_item("only-2", "python", b=1.0)]
    compiled = compile_assessment(definition_with(items=items))
    decision = run_to_the_end(compiled)
    assert decision.status == "terminated"
    assert decision.state.questions_answered == 2
    row = next(r for r in decision.report.variables if r.variable == "python")
    assert row.stop_reason == "bank_exhausted"
    assert row.converged is False


def test_the_per_competency_question_cap_is_a_budget_stop(monkeypatch):
    from cat_engine.engine.config.settings import settings

    monkeypatch.setattr(settings, "cat_max_questions", 2)
    monkeypatch.setattr(settings, "cat_se_target", 0.05)  # unreachable: the cap fires
    compiled = compile_assessment(definition_with(items=bank_for("python", 8)))
    decision = run_to_the_end(compiled)
    assert decision.status == "terminated"
    row = next(r for r in decision.report.variables if r.variable == "python")
    assert row.stop_reason == "question_budget"
    assert row.observations == 2


def test_the_time_budget_is_reachable(definition, monkeypatch):
    """With no time at all, every variable ends as time_budget — never as convergence."""
    from cat_engine.engine.config.settings import settings

    monkeypatch.setattr(settings, "orchestrator_time_limit_minutes", 0)
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=5)
    assert decision.status == "terminated"
    assert decision.converged is False
    assert all(row.stop_reason == "time_budget" for row in decision.report.variables)


def test_a_finished_assessment_cannot_accept_another_response(definition, monkeypatch):
    from cat_engine.engine.config.settings import settings

    monkeypatch.setattr(settings, "orchestrator_max_items", 2)
    compiled = compile_assessment(definition)
    decision = run_to_the_end(compiled)
    with pytest.raises(AssessmentFinished) as caught:
        advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(question_id="python-q0", answer=0),
        )
    assert caught.value.code == "assessment_finished"


def test_no_question_is_administered_twice(definition):
    compiled = compile_assessment(definition)
    decision = run_to_the_end(compiled)
    served = decision.state.engine_state.served_item_ids
    assert len(served) == len(set(served))


def test_unassessed_competencies_are_reported_as_not_assessed(definition, monkeypatch):
    """The review's finding #9: a prior alone must never read as a measured level."""
    from cat_engine.engine.config.settings import settings

    monkeypatch.setattr(settings, "orchestrator_max_items", 1)
    compiled = compile_assessment(definition)
    decision = run_to_the_end(compiled)
    untouched = [row for row in decision.report.variables if row.observations == 0]
    assert untouched, "with a one-item budget, one competency was never asked about"
    assert all(row.decision_status == "not_assessed" for row in untouched)
