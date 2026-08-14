"""Advancing a run: evidence lands where it was declared, invalid transitions are refused."""

from __future__ import annotations

import pytest

from adaptive_engine import (
    GradedAnswer,
    InvalidAnswer,
    InvalidState,
    Measurement,
    Question,
    QuestionResponse,
    StaleResponse,
    StateMismatch,
    advance_assessment,
    compile_assessment,
    start_assessment,
)
from adaptive_engine.tests.conftest import bank_for, definition_with, mcq


def answer_current(compiled, decision, *, answer=0):
    return advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(
            question_id=decision.question.question_id, answer=answer
        ),
    )


def test_an_answer_updates_the_measured_competency_and_no_other(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    measured = decision.question.question_id.split("-q")[0]
    other = next(c for c in compiled.competency_ids if c != measured)

    before = decision.state
    after = answer_current(compiled, decision).state

    assert (
        after.competency_states[measured].posterior
        != before.competency_states[measured].posterior
    )
    assert after.competency_states[measured].observations == 1
    assert (
        after.competency_states[other].posterior
        == before.competency_states[other].posterior
    )
    assert after.competency_states[other].observations == 0
    assert after.administered_question_ids == [decision.question.question_id]


def test_a_multi_competency_question_updates_every_declared_competency_once():
    shared = mcq(
        "shared-1",
        "python",
        extra_measures=[Measurement(competency_id="databases", weight=0.5)],
    )
    definition = definition_with(
        competencies=["python", "databases"],
        questions=[shared, *bank_for("python", 3), *bank_for("databases", 3)],
    )
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    while decision.question.question_id != "shared-1":
        decision = answer_current(compiled, decision)

    before = decision.state
    after = answer_current(compiled, decision).state
    for competency_id in ("python", "databases"):
        assert (
            after.competency_states[competency_id].posterior
            != before.competency_states[competency_id].posterior
        ), competency_id
        assert (
            after.competency_states[competency_id].observations
            == before.competency_states[competency_id].observations + 1
        ), competency_id


def test_a_stale_question_id_is_rejected(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    with pytest.raises(StaleResponse) as caught:
        advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(question_id="not-the-current-one", answer=0),
        )
    assert caught.value.code == "stale_response"


def test_a_delayed_retry_of_an_answered_question_is_rejected(definition):
    compiled = compile_assessment(definition)
    first = start_assessment(compiled, seed=7)
    retry = QuestionResponse(question_id=first.question.question_id, answer=0)
    second = advance_assessment(compiled, state=first.state, response=retry)
    with pytest.raises(StaleResponse, match="already answered"):
        advance_assessment(compiled, state=second.state, response=retry)


def test_an_assessment_state_version_mismatch_is_rejected(definition):
    compiled_v1 = compile_assessment(definition)
    compiled_v2 = compile_assessment(definition_with(version="v2"))
    decision = start_assessment(compiled_v1, seed=7)
    with pytest.raises(StateMismatch) as caught:
        advance_assessment(
            compiled_v2,
            state=decision.state,
            response=QuestionResponse(
                question_id=decision.question.question_id, answer=0
            ),
        )
    assert caught.value.code == "state_mismatch"


def test_a_state_referencing_unknown_content_is_rejected(definition):
    compiled = compile_assessment(definition)
    foreign = compile_assessment(
        definition_with(
            competencies=["python", "databases"],
            questions=[mcq("alien-q", "python"), mcq("alien-q2", "databases")],
        )
    )
    decision = start_assessment(foreign, seed=7)
    with pytest.raises(InvalidState) as caught:
        advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(
                question_id=decision.question.question_id, answer=0
            ),
        )
    assert caught.value.code == "invalid_state"


def test_the_input_state_is_not_mutated(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    snapshot = decision.state.model_dump(mode="json")
    answer_current(compiled, decision)
    assert decision.state.model_dump(mode="json") == snapshot


@pytest.mark.parametrize("bad", [-1, 3, 99, GradedAnswer(score=1.0), 1.5, [0]])
def test_answers_that_do_not_fit_an_mcq_question_are_rejected(definition, bad):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    if isinstance(bad, GradedAnswer):
        response = QuestionResponse(
            question_id=decision.question.question_id, answer=bad
        )
        with pytest.raises(InvalidAnswer) as caught:
            advance_assessment(compiled, state=decision.state, response=response)
        assert caught.value.code == "invalid_answer"
    else:
        # Junk that is not even a valid response shape is refused at the model.
        import pydantic

        with pytest.raises((pydantic.ValidationError, InvalidAnswer)):
            response = QuestionResponse(
                question_id=decision.question.question_id, answer=bad
            )
            advance_assessment(compiled, state=decision.state, response=response)


def test_an_externally_graded_question_takes_a_graded_answer_only():
    external = Question(
        question_id="code-1",
        kind="externally_graded",
        prompt="Write a function.",
        measures=[Measurement(competency_id="python")],
        difficulty=0.0,
        discrimination=2.0,
    )
    definition = definition_with(
        competencies=["python"], questions=[external, *bank_for("python", 4)]
    )
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=3)
    while decision.question.question_id != "code-1":
        decision = advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(
                question_id=decision.question.question_id, answer=0
            ),
        )

    with pytest.raises(InvalidAnswer, match="externally graded"):
        advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(question_id="code-1", answer=1),
        )

    before = decision.state.competency_states["python"]
    after = advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(
            question_id="code-1", answer=GradedAnswer(score=0.9, confidence=0.8)
        ),
    ).state.competency_states["python"]
    assert after.posterior != before.posterior
    assert after.observations == before.observations + 1
