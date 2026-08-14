"""The assessment definition: the one model of what an assessment contains.

The caller supplies an immutable ``AssessmentDefinition`` holding assessment content and
measurement configuration — competencies, the prerequisite graph over them, questions
with their measurements and adaptive parameters, grading data where grading is
deterministic, and the stopping policy. The engine never stores any of it; the caller
owns persistence and versioning.

Every model here is frozen, rejects unknown fields, and rejects NaN/infinite floats at
the door — a definition either validates completely or is refused with a field-level
error, never half-loaded.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

QuestionKind = Literal["mcq", "externally_graded"]


class FrozenModel(BaseModel):
    """Base for every model in this package: immutable, strict about shape and floats."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Competency(FrozenModel):
    """One thing the assessment measures. Competencies are the graph's nodes."""

    competency_id: str = Field(min_length=1)
    title: str = ""


class GraphEdge(FrozenModel):
    """``source`` is a prerequisite of ``target``.

    Prerequisite is the only relation the engine acts on: it steers competency selection
    away from dependents whose prerequisite is demonstrably failing, and it shapes the
    final report. It never manufactures evidence — only a directly observed response
    moves a competency's posterior.
    """

    source: str = Field(min_length=1)
    target: str = Field(min_length=1)


class AssessmentGraph(FrozenModel):
    """The prerequisite structure over competencies. Part of the immutable definition."""

    edges: list[GraphEdge] = Field(default_factory=list)


class Measurement(FrozenModel):
    """A question's claim on one competency.

    ``weight`` is how much of a full observation this question contributes to that
    competency — 1.0 for a question squarely about it, less for a question that only
    touches it.
    """

    competency_id: str = Field(min_length=1)
    weight: float = Field(default=1.0, gt=0.0, le=1.0)


class Question(FrozenModel):
    """One question, including its grading data. THIS MODEL IS NOT CANDIDATE-SAFE.

    ``answer_index`` is the answer key; it exists only inside the definition and the
    compiled assessment. The candidate-facing shape is ``PresentedQuestion``, produced by
    the one safe projection in ``runtime.present_question``.

    Two kinds exist because two genuinely different grading boundaries exist:

    - ``mcq`` is graded deterministically by the engine from ``answer_index``.
    - ``externally_graded`` covers modalities the host grades outside the engine (code,
      free text, voice). The response must carry a validated ``GradedAnswer``; the engine
      applies it through the same evidence path as every other question.
    """

    question_id: str = Field(min_length=1)
    kind: QuestionKind = "mcq"
    prompt: str = Field(min_length=1)
    options: list[str] | None = None
    answer_index: int | None = Field(default=None, ge=0)
    measures: list[Measurement] = Field(min_length=1)

    # Adaptive parameters, bounded tightly enough to catch a mis-calibrated bank and
    # loosely enough not to reject a legitimate one.
    difficulty: float = Field(default=0.0, ge=-4.0, le=4.0, description="b: on the ability scale")
    discrimination: float = Field(default=1.0, gt=0.0, le=3.0, description="a")
    guessing: float = Field(default=0.0, ge=0.0, lt=1.0, description="c: floor probability")

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> Question:
        if self.kind == "mcq":
            if self.options is None or len(self.options) < 2:
                raise ValueError(
                    f"question {self.question_id}: an mcq question needs at least 2 options"
                )
            if self.answer_index is None:
                raise ValueError(
                    f"question {self.question_id}: an mcq question needs an answer_index"
                )
            if self.answer_index >= len(self.options):
                raise ValueError(
                    f"question {self.question_id}: answer_index {self.answer_index} is out of "
                    f"range for {len(self.options)} options"
                )
        else:
            if self.options is not None or self.answer_index is not None:
                raise ValueError(
                    f"question {self.question_id}: options and answer_index belong to mcq "
                    "questions only"
                )
        seen: set[str] = set()
        for measurement in self.measures:
            if measurement.competency_id in seen:
                raise ValueError(
                    f"question {self.question_id}: measures competency "
                    f"{measurement.competency_id!r} more than once"
                )
            seen.add(measurement.competency_id)
        return self


class AssessmentPolicy(FrozenModel):
    """Question limits and the stopping policy. Measurement policy, not measurement.

    The defaults are the ones the previous engine shipped and validated in simulation:
    an SE target of 0.55 (band-width precision on the 1-5 scale), a six-question floor so
    two lucky high-discrimination hits cannot end a competency, and a twelve-question
    per-competency cap.
    """

    se_target: float = Field(default=0.55, gt=0.0)
    min_questions_per_competency: int = Field(default=6, ge=1)
    max_questions_per_competency: int = Field(default=12, ge=1)
    max_questions_total: int = Field(default=120, ge=1)
    # Size of the randomized exposure window. 1 disables exposure control entirely and
    # makes selection a pure argmax; k > 1 administers a uniformly random rank from the
    # top k (randomesque, Kingsbury & Zara 1989) so the item sequence is not predictable
    # from the definition alone. The randomness is driven by the seed stored in
    # AdaptiveState, so replay stays deterministic.
    exposure_top_k: int = Field(default=3, ge=1)

    @model_validator(mode="after")
    def _floors_below_caps(self) -> AssessmentPolicy:
        if self.max_questions_per_competency < self.min_questions_per_competency:
            raise ValueError(
                "max_questions_per_competency must be >= min_questions_per_competency"
            )
        return self


class AssessmentDefinition(FrozenModel):
    """Everything the engine needs, supplied by the caller, owned by the caller."""

    assessment_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    competencies: list[Competency] = Field(min_length=1)
    graph: AssessmentGraph = Field(default_factory=AssessmentGraph)
    questions: list[Question] = Field(min_length=1)
    policy: AssessmentPolicy = Field(default_factory=AssessmentPolicy)
