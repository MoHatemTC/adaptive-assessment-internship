"""Advancing a run: evidence lands where grading said, invalid transitions are refused."""

from __future__ import annotations

import pytest

from adaptive_engine import (
    GradedAnswer,
    InvalidAnswer,
    QuestionResponse,
    StaleResponse,
    StateMismatch,
    advance_assessment,
    compile_assessment,
    start_assessment,
)
from adaptive_engine.tests.conftest import (
    bank_for,
    code_item,
    definition_with,
)


def answer_current(compiled, decision, *, answer=0):
    return advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(
            question_id=decision.question.item.item_id, answer=answer
        ),
    )


def advance_until(compiled, decision, item_id):
    while decision.question.item.item_id != item_id:
        decision = answer_current(compiled, decision)
    return decision


def test_an_mcq_answer_updates_the_measured_competency_and_no_other(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    measured = decision.question.variable
    other = next(v for v in compiled.targets if v != measured)

    before = decision.state.engine_state
    after = answer_current(compiled, decision).state.engine_state

    assert after.variables[measured].posterior != before.variables[measured].posterior
    assert after.variables[measured].observations == 1
    assert after.variables[other].posterior == before.variables[other].posterior
    assert after.variables[other].observations == 0
    assert after.served_item_ids == [decision.question.item.item_id]


def test_host_graded_evidence_can_differ_per_competency():
    """The review's finding #1: one response, different evidence per competency."""
    items = [
        code_item("code-1", [("python", 0.8), ("databases", 0.8)]),
        *bank_for("python", 3),
        *bank_for("databases", 3),
    ]
    compiled = compile_assessment(definition_with(items=items))
    decision = start_assessment(compiled, seed=3)
    decision = advance_until(compiled, decision, "code-1")

    before = decision.state.engine_state
    after = advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(
            question_id="code-1",
            answer={
                "python": GradedAnswer(score=0.95, confidence=0.9),
                "databases": GradedAnswer(score=0.05, confidence=0.9),
            },
        ),
    ).state.engine_state

    python_moved = (
        after.variables["python"].theta_hat - before.variables["python"].theta_hat
    )
    databases_moved = (
        after.variables["databases"].theta_hat
        - before.variables["databases"].theta_hat
    )
    assert python_moved > 0, "a strong python showing must raise the python estimate"
    assert databases_moved < 0, "a weak databases showing must lower that estimate"
    assert after.variables["python"].observations == 1
    assert after.variables["databases"].observations == 1


def test_zero_weight_evidence_is_not_counted_as_an_observation():
    """The review's finding #2, preserved by reuse: hollow evidence moves nothing."""
    items = [code_item("code-1", [("python", 0.8)]), *bank_for("python", 3)]
    compiled = compile_assessment(definition_with(items=items))
    decision = start_assessment(compiled, seed=3)
    decision = advance_until(compiled, decision, "code-1")

    before = decision.state.engine_state.variables["python"]
    after = advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(
            question_id="code-1",
            answer=GradedAnswer(score=0.5, confidence=1.0, weight=0.0),
        ),
    ).state.engine_state.variables["python"]

    assert after.observations == before.observations
    assert after.posterior == before.posterior


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
    retry = QuestionResponse(question_id=first.question.item.item_id, answer=0)
    second = advance_assessment(compiled, state=first.state, response=retry)
    with pytest.raises(StaleResponse, match="already answered"):
        advance_assessment(compiled, state=second.state, response=retry)


def test_an_assessment_version_mismatch_is_rejected(definition):
    compiled_v1 = compile_assessment(definition)
    compiled_v2 = compile_assessment(definition_with(version="v2"))
    decision = start_assessment(compiled_v1, seed=7)
    with pytest.raises(StateMismatch) as caught:
        answer_current(compiled_v2, decision)
    assert caught.value.code == "state_mismatch"


def test_edited_content_behind_an_unchanged_version_is_rejected(definition):
    """The review's finding #10: the content hash catches silent definition edits."""
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)

    edited_items = [item.model_dump() for item in definition.items]
    edited_items[0]["mcq"] = {**edited_items[0]["mcq"], "answer_index": 2}
    edited = compile_assessment(definition_with(items=edited_items))

    with pytest.raises(StateMismatch, match="content"):
        answer_current(edited, decision)


def test_the_input_state_is_not_mutated(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    snapshot = decision.state.model_dump(mode="json")
    terminal = answer_current(compiled, decision)
    assert decision.state.model_dump(mode="json") == snapshot
    # Even a terminal transition (which stamps stop reasons) must not touch the input.
    while terminal.status == "question":
        snapshot = terminal.state.model_dump(mode="json")
        following = answer_current(compiled, terminal)
        assert terminal.state.model_dump(mode="json") == snapshot
        terminal = following


def test_answers_that_do_not_fit_the_modality_are_rejected(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)  # presents an mcq item

    with pytest.raises(InvalidAnswer) as caught:
        answer_current(compiled, decision, answer=GradedAnswer(score=1.0))
    assert caught.value.code == "invalid_answer"

    with pytest.raises(InvalidAnswer, match="out of range"):
        answer_current(compiled, decision, answer=99)


def test_an_externally_graded_item_refuses_an_option_index():
    items = [code_item("code-1", [("python", 0.8)]), *bank_for("python", 3)]
    compiled = compile_assessment(definition_with(items=items))
    decision = start_assessment(compiled, seed=3)
    decision = advance_until(compiled, decision, "code-1")
    with pytest.raises(InvalidAnswer, match="GradedAnswer"):
        answer_current(compiled, decision, answer=1)


def test_graded_answers_for_unmeasured_competencies_are_rejected():
    items = [code_item("code-1", [("python", 0.8)]), *bank_for("python", 3)]
    compiled = compile_assessment(definition_with(items=items))
    decision = start_assessment(compiled, seed=3)
    decision = advance_until(compiled, decision, "code-1")
    with pytest.raises(InvalidAnswer, match="does not measure"):
        answer_current(
            compiled,
            decision,
            answer={"kubernetes": GradedAnswer(score=0.5)},
        )
