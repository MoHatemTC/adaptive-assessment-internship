"""Shared builders for the adaptive_engine suite.

One way to build a definition, parameterised just enough for each test to say what it
needs. ``exposure_top_k=1`` by default so tests that assert on WHICH question was chosen
are exact; tests about exposure control set their own k.
"""

from __future__ import annotations

import pytest

from adaptive_engine import (
    AssessmentDefinition,
    AssessmentGraph,
    AssessmentPolicy,
    Competency,
    GraphEdge,
    Measurement,
    Question,
)


def mcq(
    question_id: str,
    competency_id: str,
    *,
    difficulty: float = 0.0,
    discrimination: float = 2.0,
    answer_index: int = 0,
    extra_measures: list[Measurement] | None = None,
) -> Question:
    measures = [Measurement(competency_id=competency_id), *(extra_measures or [])]
    return Question(
        question_id=question_id,
        prompt=f"Prompt for {question_id}?",
        options=["option a", "option b", "option c"],
        answer_index=answer_index,
        measures=measures,
        difficulty=difficulty,
        discrimination=discrimination,
    )


def bank_for(competency_id: str, count: int = 12) -> list[Question]:
    """A spread of difficulties wide enough for the run to localise any ability."""
    return [
        mcq(
            f"{competency_id}-q{i}",
            competency_id,
            difficulty=min(4.0, (i % 9) - 4.0 + (i // 9) * 0.1),
        )
        for i in range(count)
    ]


def definition_with(
    *,
    competencies: list[str] | None = None,
    questions: list[Question] | None = None,
    edges: list[tuple[str, str]] | None = None,
    policy: AssessmentPolicy | None = None,
    version: str = "v1",
) -> AssessmentDefinition:
    competency_ids = competencies or ["python", "databases"]
    return AssessmentDefinition(
        assessment_id="python-backend",
        version=version,
        competencies=[Competency(competency_id=c) for c in competency_ids],
        graph=AssessmentGraph(
            edges=[GraphEdge(source=s, target=t) for s, t in (edges or [])]
        ),
        questions=(
            questions
            if questions is not None
            else [q for c in competency_ids for q in bank_for(c)]
        ),
        policy=policy
        or AssessmentPolicy(
            se_target=0.8,
            min_questions_per_competency=3,
            max_questions_per_competency=10,
            max_questions_total=40,
            exposure_top_k=1,
        ),
    )


@pytest.fixture()
def definition() -> AssessmentDefinition:
    return definition_with()
