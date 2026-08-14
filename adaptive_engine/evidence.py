"""Grading and the one evidence-application path.

Adaptive updating is kept separate from modality-specific grading by a single normalized
model: ``Evidence``. Deterministic questions (mcq) are graded here, directly from the
definition's answer key. Externally graded questions arrive as a validated
``GradedAnswer`` from the host. Both funnel into the same ``Evidence`` shape and the same
posterior update — there is no second path for any question type, present or future.
"""

from __future__ import annotations

from collections.abc import Mapping

import numpy as np
from pydantic import Field, ValidationError

from adaptive_engine.errors import InvalidAnswer
from adaptive_engine.irt import apply_graded_evidence
from adaptive_engine.models import FrozenModel, Question
from adaptive_engine.state import CompetencyState


class GradedAnswer(FrozenModel):
    """A grading result: how right the answer was, and how sure the grader is.

    For ``externally_graded`` questions this is what the caller supplies as the response
    answer — the host runs its grader (sandbox, rubric, human) and hands the engine only
    the validated outcome. The engine never calls grading infrastructure itself.
    """

    score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class Evidence(FrozenModel):
    """One normalized piece of graded evidence against one competency.

    ``weight`` comes from the question's measurement declaration; ``score`` and
    ``confidence`` from grading. The posterior update raises the response likelihood to
    ``weight * confidence`` — evidence nobody stands behind moves nothing, full-weight
    full-confidence evidence is a complete Bernoulli observation.
    """

    competency_id: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(gt=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    question_id: str = Field(min_length=1)


def grade_answer(question: Question, answer: object) -> GradedAnswer:
    """Grade a raw answer against the question's type. Raises ``InvalidAnswer``.

    mcq grading is fully deterministic — an index compared against the answer key, no
    model, ever. ``bool`` is rejected explicitly for mcq because it satisfies
    ``isinstance(x, int)`` while plainly not being a chosen option.
    """
    if question.kind == "mcq":
        if isinstance(answer, bool) or not isinstance(answer, int):
            raise InvalidAnswer(
                f"question {question.question_id} is mcq: the answer must be an integer "
                f"option index, got {type(answer).__name__}"
            )
        options = question.options or []
        if not 0 <= answer < len(options):
            raise InvalidAnswer(
                f"question {question.question_id}: option index {answer} is out of range "
                f"for {len(options)} options"
            )
        return GradedAnswer(score=1.0 if answer == question.answer_index else 0.0)

    if isinstance(answer, GradedAnswer):
        return answer
    if isinstance(answer, Mapping):
        try:
            return GradedAnswer.model_validate(dict(answer))
        except ValidationError as exc:
            raise InvalidAnswer(
                f"question {question.question_id} is externally graded and the supplied "
                f"grading result is invalid: {exc}"
            ) from exc
    raise InvalidAnswer(
        f"question {question.question_id} is externally graded: the answer must be a "
        f"GradedAnswer, got {type(answer).__name__}"
    )


def evidence_from(question: Question, graded: GradedAnswer) -> tuple[Evidence, ...]:
    """One Evidence per declared measurement — a multi-competency question updates every
    competency it declares, exactly once each."""
    return tuple(
        Evidence(
            competency_id=measurement.competency_id,
            score=graded.score,
            weight=measurement.weight,
            confidence=graded.confidence,
            question_id=question.question_id,
        )
        for measurement in question.measures
    )


def apply_evidence(
    current: CompetencyState, question: Question, evidence: Evidence
) -> CompetencyState:
    """THE evidence-application path: one Bayes update, one observation counted.

    Returns a new CompetencyState; the input is never mutated.
    """
    posterior = apply_graded_evidence(
        np.asarray(current.posterior, dtype=float),
        a=question.discrimination,
        b=question.difficulty,
        c=question.guessing,
        score=evidence.score,
        exponent=evidence.weight * evidence.confidence,
    )
    return CompetencyState(
        posterior=posterior.tolist(),
        observations=current.observations + 1,
    )
