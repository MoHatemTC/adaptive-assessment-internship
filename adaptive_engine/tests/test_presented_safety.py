"""The candidate boundary: what a presented question may and may not carry."""

from __future__ import annotations

import json

from adaptive_engine import (
    PresentedQuestion,
    compile_assessment,
    present_question,
    start_assessment,
)


def test_presented_questions_contain_no_answer_keys(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    presented = decision.question
    assert isinstance(presented, PresentedQuestion)
    assert set(presented.model_dump()) == {"question_id", "kind", "prompt", "options"}
    assert not hasattr(presented, "answer_index")


def test_nothing_in_a_serialized_decision_leaks_grading_data(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    over_the_wire = json.dumps(decision.model_dump(mode="json"))
    assert "answer_index" not in over_the_wire
    assert "discrimination" not in over_the_wire


def test_the_projection_is_the_only_route_and_copies_its_options(definition):
    question = definition.questions[0]
    presented = present_question(question)
    assert presented.options == question.options
    assert (
        presented.options is not question.options
    ), "a shared list would let a host mutate the definition"
