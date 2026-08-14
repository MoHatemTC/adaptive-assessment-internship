"""Starting a run: seeding beliefs, validating them, and presenting the first question."""

from __future__ import annotations

import math

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
    assert decision.state.current_question_id == decision.question.question_id
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
    assert decision.state.administered_question_ids == []
    assert all(
        competency.observations == 0
        for competency in decision.state.competency_states.values()
    )


def test_different_initial_beliefs_can_influence_initial_selection(definition):
    compiled = compile_assessment(definition)

    neutral = start_assessment(compiled, seed=7)
    confident_databases = start_assessment(
        compiled,
        initial_competencies={"databases": InitialCompetency(level=3, confidence=0.9)},
        seed=7,
    )

    # With no beliefs both competencies tie at maximum uncertainty and the id breaks the
    # tie toward databases. A confident databases belief narrows its posterior, so python
    # becomes the most uncertain competency and is asked about first.
    assert neutral.question.question_id.startswith("databases-")
    assert confident_databases.question.question_id.startswith("python-")


def test_the_same_seed_reproduces_the_same_first_question(definition):
    compiled = compile_assessment(definition)
    first = start_assessment(compiled, seed=99)
    second = start_assessment(compiled, seed=99)
    assert first.question.question_id == second.question.question_id
    assert first.state == second.state


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


def test_confidence_widens_or_narrows_the_prior(definition):
    compiled = compile_assessment(definition)
    sure = start_assessment(
        compiled,
        initial_competencies={"python": InitialCompetency(level=3, confidence=1.0)},
        seed=1,
    )
    unsure = start_assessment(
        compiled,
        initial_competencies={"python": InitialCompetency(level=3, confidence=0.0)},
        seed=1,
    )
    spread = lambda posterior: math.sqrt(  # noqa: E731
        sum(w * (i - 20) ** 2 for i, w in enumerate(posterior))
    )
    assert spread(sure.state.competency_states["python"].posterior) < spread(
        unsure.state.competency_states["python"].posterior
    )
