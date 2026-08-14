"""Every way a run ends, and what each ending claims."""

from __future__ import annotations

import pytest

from adaptive_engine import (
    AssessmentFinished,
    AssessmentPolicy,
    QuestionResponse,
    StopReason,
    advance_assessment,
    compile_assessment,
    start_assessment,
)
from adaptive_engine.tests.conftest import bank_for, definition_with, mcq


def run_to_the_end(compiled, *, seed=5, answer_for=None):
    decision = start_assessment(compiled, seed=seed)
    while decision.status == "question":
        question_id = decision.question.question_id
        answer = answer_for(decision) if answer_for else 0
        decision = advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(question_id=question_id, answer=answer),
        )
    return decision


def test_convergence_returns_status_completed():
    # One competency, informative questions across the whole scale, an alternating
    # answer pattern that keeps the estimate central where the bank can corroborate it.
    definition = definition_with(
        competencies=["python"],
        questions=bank_for("python", 24),
        policy=AssessmentPolicy(
            se_target=0.9,
            min_questions_per_competency=3,
            max_questions_per_competency=24,
            max_questions_total=100,
            exposure_top_k=1,
        ),
    )
    compiled = compile_assessment(definition)
    toggle = iter(range(100))
    decision = run_to_the_end(
        compiled,
        answer_for=lambda d: 0 if next(toggle) % 2 == 0 else 1,
    )
    assert decision.status == "completed"
    assert decision.converged is True
    assert decision.stop_reason is StopReason.ALL_COMPETENCIES_CONVERGED
    assert decision.report is not None
    assert decision.question is None
    assert all(row.converged for row in decision.report.competencies)


def test_the_total_question_budget_returns_status_terminated(definition):
    compiled = compile_assessment(
        definition_with(
            policy=AssessmentPolicy(
                se_target=0.1,
                min_questions_per_competency=1,
                max_questions_per_competency=10,
                max_questions_total=3,
                exposure_top_k=1,
            )
        )
    )
    decision = run_to_the_end(compiled)
    assert decision.status == "terminated"
    assert decision.converged is False
    assert decision.stop_reason is StopReason.QUESTION_BUDGET
    assert decision.report is not None
    assert decision.state.questions_answered == 3


def test_bank_exhaustion_returns_status_terminated():
    definition = definition_with(
        competencies=["python"],
        questions=[mcq("only-1", "python"), mcq("only-2", "python")],
        policy=AssessmentPolicy(
            se_target=0.1,
            min_questions_per_competency=6,
            max_questions_per_competency=12,
            max_questions_total=50,
            exposure_top_k=1,
        ),
    )
    decision = run_to_the_end(compile_assessment(definition))
    assert decision.status == "terminated"
    assert decision.stop_reason is StopReason.QUESTIONS_EXHAUSTED
    assert decision.state.questions_answered == 2


def test_the_per_competency_cap_reports_a_budget_stop():
    definition = definition_with(
        competencies=["python"],
        questions=bank_for("python", 8),
        policy=AssessmentPolicy(
            se_target=0.05,  # unreachable, so the cap is what fires
            min_questions_per_competency=1,
            max_questions_per_competency=4,
            max_questions_total=50,
            exposure_top_k=1,
        ),
    )
    decision = run_to_the_end(compile_assessment(definition))
    assert decision.status == "terminated"
    assert decision.stop_reason is StopReason.QUESTION_BUDGET
    assert decision.state.questions_answered == 4


def test_a_finished_assessment_cannot_accept_another_response(definition):
    compiled = compile_assessment(
        definition_with(
            policy=AssessmentPolicy(
                se_target=0.1,
                min_questions_per_competency=1,
                max_questions_per_competency=10,
                max_questions_total=2,
                exposure_top_k=1,
            )
        )
    )
    decision = run_to_the_end(compiled)
    assert decision.status == "terminated"
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
    administered = decision.state.administered_question_ids
    assert len(administered) == len(set(administered))
