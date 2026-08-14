"""Starting a run: seeding beliefs, validating them, presenting the first question."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adaptive_engine import (
    InitialCompetency,
    InvalidInitialCompetency,
    compile_assessment,
    start_assessment,
)


def test_starting_returns_an_initial_question_and_state(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    assert decision.status == "question"
    assert decision.question is not None
    assert decision.state.current_question_id == decision.question.item.item_id
    assert decision.report is None and decision.stop_reason is None
    assert decision.converged is False


def test_initial_beliefs_do_not_count_as_answered_questions(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(
        compiled,
        initial_competencies={"python": InitialCompetency(level=4, confidence=0.9)},
        seed=7,
    )
    assert decision.state.questions_answered == 0
    assert all(
        variable.observations == 0
        for variable in decision.state.engine_state.variables.values()
    )


def test_initial_beliefs_seed_the_prior(definition):
    compiled = compile_assessment(definition)
    neutral = start_assessment(compiled, seed=7)
    believed = start_assessment(
        compiled,
        initial_competencies={"python": InitialCompetency(level=5, confidence=0.9)},
        seed=7,
    )
    flat = neutral.state.engine_state.variables["python"]
    seeded = believed.state.engine_state.variables["python"]
    assert seeded.posterior != flat.posterior
    assert seeded.theta_hat > flat.theta_hat
    assert seeded.standard_error < flat.standard_error


def test_confidence_selects_the_narrow_or_wide_prior(definition):
    compiled = compile_assessment(definition)
    trusted = start_assessment(
        compiled,
        initial_competencies={"python": InitialCompetency(level=3, confidence=0.9)},
        seed=1,
    )
    tentative = start_assessment(
        compiled,
        initial_competencies={"python": InitialCompetency(level=3, confidence=0.2)},
        seed=1,
    )
    assert (
        trusted.state.engine_state.variables["python"].standard_error
        < tentative.state.engine_state.variables["python"].standard_error
    )


def test_the_same_seed_reproduces_the_same_run_start(definition):
    compiled = compile_assessment(definition)
    first = start_assessment(compiled, seed=99)
    second = start_assessment(compiled, seed=99)
    assert first.question.item.item_id == second.question.item.item_id
    assert first.state.rng_state == second.state.rng_state


def test_an_initial_belief_for_an_unknown_competency_is_rejected(definition):
    compiled = compile_assessment(definition)
    with pytest.raises(InvalidInitialCompetency) as caught:
        start_assessment(
            compiled,
            initial_competencies={"kubernetes": InitialCompetency(level=3, confidence=0.5)},
        )
    assert caught.value.code == "invalid_initial_competency"


@pytest.mark.parametrize("level", [0, 6, -1, True, 3.0, "3", None])
def test_invalid_initial_levels_are_rejected(level):
    with pytest.raises(ValidationError):
        InitialCompetency(level=level, confidence=0.5)


@pytest.mark.parametrize(
    "confidence", [-0.1, 1.1, True, "0.5", float("nan"), float("inf"), None]
)
def test_invalid_initial_confidence_values_are_rejected(confidence):
    with pytest.raises(ValidationError):
        InitialCompetency(level=3, confidence=confidence)


def test_raw_initial_values_are_validated_through_start(definition):
    compiled = compile_assessment(definition)
    with pytest.raises(InvalidInitialCompetency):
        start_assessment(
            compiled, initial_competencies={"python": {"level": 9, "confidence": 0.5}}
        )


def test_a_bool_seed_is_rejected(definition):
    compiled = compile_assessment(definition)
    with pytest.raises(TypeError):
        start_assessment(compiled, seed=True)
