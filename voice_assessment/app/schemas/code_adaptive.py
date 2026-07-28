"""The typed boundary of the code-question engine.

Everything crossing in or out of `CodeAdaptiveSession` is defined here, so a caller can
integrate against these types without reading the engine. The internal dataclasses
(ExecutionEvidence, StaticSignals, CriterionScore) stay in their own modules because they
are implementation detail — what a caller needs is a question to present, a submission to
grade, and a result to report.

`Question` is a validating view over the JSON bank rather than a rewrite of it: the bank
is authored data, and the schema exists to reject a malformed question at load time rather
than at the moment a candidate is waiting for it.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

CriterionId = Literal[
    "functional_correctness", "edge_case_handling", "algorithm_choice", "code_quality"
]


class TestCase(BaseModel):
    """One executable test. `weight` scales its contribution to the passed-test ratio."""

    test_id: str
    input: list[Any]
    expected: Any
    weight: float = 1.0
    visible: bool = False
    # Which criteria and competencies this test bears on. The criterion->competency
    # projection is derived from these rather than declared separately, so it cannot drift
    # from what is actually measured.
    criterion_weights: dict[str, float] = Field(default_factory=dict)
    competency_weights: dict[str, float] = Field(default_factory=dict)


class RubricCriterion(BaseModel):
    """A criterion and its share of the overall score.

    `maximum_score` is a weight, not a cap. It was inert for the whole of the study — the
    overall was a plain mean, so functional correctness could never be worth more than a
    quarter however the bank was authored, and a submission passing 1 of 8 tests scored
    0.61. It is now read.
    """

    criterion_id: CriterionId
    maximum_score: float = Field(default=1.0, gt=0.0)


class CompetencyWeight(BaseModel):
    competency_id: str
    weight: float = Field(ge=0.0, le=1.0)


class Question(BaseModel):
    """One coding question, as authored in the bank."""

    question_id: str
    title: str
    prompt: str
    function_name: str
    starter_code: str = ""
    # On the same 0..1 scale as mastery, so information peaks where the candidate sits.
    difficulty: float = Field(ge=0.0, le=1.0)
    discrimination: float = Field(default=1.0, gt=0.0)
    status: str = "active"
    prerequisites: list[str] = Field(default_factory=list)
    competencies: list[CompetencyWeight]
    rubric_criteria: list[RubricCriterion]
    tests: list[TestCase]


class CompetencySnapshot(BaseModel):
    """One competency's belief at a point in time. `level` is None while unmeasured."""

    competency_id: str
    mastery: float
    standard_error: float
    level: int | None
    band: str
    evidence_count: int
    observed: bool
    misconception_codes: list[str] = Field(default_factory=list)


class SelectedQuestion(BaseModel):
    """What to present next, with the engine's own ranking recorded beside it."""

    question_id: str
    rank: int
    utility: float
    best_utility: float
    normalized_regret: float
    engine_top_pick: str
    chosen_by_llm: bool
    fallback_used: bool
    reason_code: str
    reason: str
    criterion: str  # "KL" or "E[Fisher]" — which information criterion drove the ranking
    shortlist_ids: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class StopDecision(BaseModel):
    should_stop: bool
    reason: str = ""
    converged: bool = False


class SubmissionResult(BaseModel):
    """The outcome of grading one submission, as a caller should report it."""

    question_id: str
    approach: str
    rubric_id: str
    # The split that produced these scores, so the number is reproducible later.
    weight_fingerprint: str
    weight_shares: dict[str, float]
    compiled: bool | None
    passed_tests: int
    total_tests: int
    # None, never 0.0, when nothing could be assessed. An unusable run has no score, and
    # showing 0.0 reads as a measurement of the candidate.
    overall_score: float | None
    criterion_scores: dict[str, float | None]
    misconception_codes: list[str] = Field(default_factory=list)
    diagnostic: str = ""
    llm_used: bool = False
    evaluation_latency_ms: int = 0
    flags: list[str] = Field(default_factory=list)


class SessionState(BaseModel):
    """Everything one assessment needs to resume on a different worker.

    Serialisable by construction: the engine holds nothing between calls, so an assessment
    can span HTTP requests and be persisted between them.
    """

    session_id: str
    target_competency: str
    answered_question_ids: list[str] = Field(default_factory=list)
    # competency_id -> (alpha, beta, evidence_count, misconception_codes)
    competencies: dict[str, dict[str, Any]] = Field(default_factory=dict)
    started_at: float = 0.0
    elapsed_minutes: float = 0.0


class CompetencyReport(BaseModel):
    """The end-of-session summary."""

    session_id: str
    target_competency: str
    mastery: float
    standard_error: float
    level: int | None
    band: str
    questions_answered: int
    converged: bool
    stop_reason: str
    competencies: list[CompetencySnapshot] = Field(default_factory=list)
    open_misconceptions: list[str] = Field(default_factory=list)
