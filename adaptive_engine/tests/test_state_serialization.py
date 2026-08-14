"""The state is the whole memory: it must survive JSON and resume identically."""

from __future__ import annotations

import json

from adaptive_engine import (
    AdaptiveState,
    QuestionResponse,
    advance_assessment,
    compile_assessment,
    start_assessment,
)


def mid_run_decision(compiled, steps=4, seed=11):
    decision = start_assessment(compiled, seed=seed)
    for _ in range(steps):
        assert decision.status == "question"
        decision = advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(
                question_id=decision.question.question_id, answer=0
            ),
        )
    return decision


def test_state_survives_json_serialization_and_restoration(definition):
    compiled = compile_assessment(definition)
    decision = mid_run_decision(compiled)
    payload = json.dumps(decision.state.model_dump(mode="json"))
    restored = AdaptiveState.model_validate(json.loads(payload))
    assert restored == decision.state


def test_restored_state_produces_the_same_next_decision(definition):
    compiled = compile_assessment(definition)
    decision = mid_run_decision(compiled)
    response = QuestionResponse(
        question_id=decision.question.question_id, answer=1
    )

    from_original = advance_assessment(compiled, state=decision.state, response=response)
    restored = AdaptiveState.model_validate(decision.state.model_dump(mode="json"))
    from_restored = advance_assessment(compiled, state=restored, response=response)

    assert from_original.status == from_restored.status
    assert from_original.state == from_restored.state
    if from_original.status == "question":
        assert from_original.question == from_restored.question


def test_the_dump_contains_no_derived_ability_summaries(definition):
    """theta-hat and the standard error are read from the posterior, never stored beside
    it where the two could drift apart."""
    compiled = compile_assessment(definition)
    payload = mid_run_decision(compiled).state.model_dump(mode="json")
    for competency_payload in payload["competency_states"].values():
        assert set(competency_payload) == {"posterior", "observations"}


def test_the_state_carries_no_host_concerns(definition):
    compiled = compile_assessment(definition)
    payload = mid_run_decision(compiled).state.model_dump(mode="json")
    assert set(payload) == {
        "schema_version",
        "assessment_id",
        "assessment_version",
        "competency_states",
        "administered_question_ids",
        "current_question_id",
        "graph_evidence",
        "random_seed",
    }
