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
                question_id=decision.question.item.item_id, answer=0
            ),
        )
    return decision


def test_state_survives_json_serialization_and_restoration(definition):
    compiled = compile_assessment(definition)
    decision = mid_run_decision(compiled)
    payload = json.dumps(decision.state.model_dump(mode="json"))
    restored = AdaptiveState.model_validate(json.loads(payload))
    assert restored.model_dump(mode="json") == decision.state.model_dump(mode="json")


def without_wall_clock(state: AdaptiveState) -> dict:
    """The dump minus the fields that read the real clock.

    Question timing (`presenting_since`, `item_seconds`, `elapsed_minutes`) legitimately
    differs between two live calls made milliseconds apart; everything the DECISIONS are
    made from must not.
    """
    payload = state.model_dump(mode="json")
    for volatile in ("presenting_since", "item_seconds", "elapsed_minutes"):
        payload["engine_state"].pop(volatile, None)
    return payload


def test_restored_state_produces_the_same_next_decision(definition):
    """Selection randomness included: the RNG state travels inside the adaptive state."""
    compiled = compile_assessment(definition)
    decision = mid_run_decision(compiled)
    response = QuestionResponse(
        question_id=decision.question.item.item_id, answer=1
    )

    from_original = advance_assessment(compiled, state=decision.state, response=response)
    restored = AdaptiveState.model_validate(decision.state.model_dump(mode="json"))
    from_restored = advance_assessment(compiled, state=restored, response=response)

    assert from_original.status == from_restored.status
    assert without_wall_clock(from_original.state) == without_wall_clock(
        from_restored.state
    )
    if from_original.status == "question":
        assert (
            from_original.question.item.item_id == from_restored.question.item.item_id
        )


def test_the_state_carries_no_host_concerns(definition):
    """Pinning and engine runtime state only — no users, no host sessions, no storage."""
    compiled = compile_assessment(definition)
    payload = mid_run_decision(compiled).state.model_dump(mode="json")
    assert set(payload) == {
        "schema_version",
        "assessment_id",
        "assessment_version",
        "content_hash",
        "engine_state",
        "rng_state",
    }
