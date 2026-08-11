"""The assessment-orchestrator surface — the contract a frontend is built against.

THE CANDIDATE BOUNDARY IS IN THESE TYPES, NOT IN THE CLIENT

`PresentedItemDTO` is the only item shape a candidate-facing response ever carries, and it
has no `answer_index`, no test cases and no `reference_solution`. `GradeReceiptDTO`
acknowledges a response without describing it. Both are deliberate: `GradedResponse.detail`
is an internal audit record that contains the correct answer for MCQ and hidden-test
diagnostics for code, and returning it after each answer would let a client harvest the
bank and let grading feedback coach later responses — which changes what the assessment is
measuring, not just what it reveals.

A tester or an author needs all of it, which is what `/diagnostics` and `/state` are for.
They are separate endpoints, refused unless the deployment enables them, so the boundary
is enforced by the service rather than by whichever client chooses not to render a field.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .envelopes import Modality
from .scope import ScopeSelection, ScopeSummaryDTO


class CreateAssessmentRequest(BaseModel):
    """Begin an assessment over several main competencies."""

    #: None uses the deployment's active bank. The id then travels with the session state
    #: rather than with each request: a client that began against one bank must not be
    #: able to answer it against another, and re-sending the id would make that possible
    #: by omission.
    bank_id: str | None = None
    #: None assesses every main the bank measures.
    target_variables: list[str] | None = None
    #: Narrow the assessment to some of the bank's competencies, at MAIN or SUB-COMPETENCY
    #: granularity. None assesses the whole bank, exactly as before — so every existing
    #: caller is unaffected and this field is additive.
    #:
    #: The SELECTION travels rather than a scope id. `scope_id` is a hash of its inputs and
    #: cannot be inverted, so the orchestrator rebuilds the manifest and pins what it built
    #: — which also means it never applies an allowlist it did not derive itself. A client
    #: that previewed the scope gets the identical `scope_id` back, because the manifest is
    #: a pure function of the bank version and the selection.
    #:
    #: Mutually exclusive with `target_variables`, which is the same idea restricted to
    #: mains and is kept for callers that already use it.
    scope: ScopeSelection | None = None
    #: Self-rating per main, 1-5. Seeds a narrower prior than flat.
    intake: dict[str, int] | None = None
    #: Whether each self-rating is trusted. An untrusted rating seeds a wide prior, which
    #: is the difference between using intake and merely recording it.
    confidence: dict[str, bool] | None = None
    use_llm: bool = True
    #: Explicit seeds are for deterministic tests and replay. Omission draws OS entropy —
    #: exposure control must not be predictable in production.
    seed: int | None = None


class AnswerRequest(BaseModel):
    """Record one response. `type` must match the modality currently presented."""

    type: Modality
    chosen_index: int | None = None
    code: str | None = None
    transcript: str | None = None
    use_llm: bool = True


class PresentedItemDTO(BaseModel):
    """An item as a CANDIDATE may see it. Never the answer.

    One flat model across modalities rather than a union: a client renders by `modality`
    and the absent fields are absent, which is easier to consume than four types and
    impossible to get wrong by reading the one that does not apply.
    """

    item_id: str
    modality: Modality
    competency: str = ""
    sub_competency: str = ""
    estimated_time_seconds: float = 0.0

    # mcq
    stem: str = ""
    options: list[str] = Field(default_factory=list)

    # code — `starter_code` may be a deliberately incomplete scaffold. The reference
    # solution is grader-only and never crosses this boundary; doing so would turn every
    # code item into an answer key.
    prompt: str = ""
    language: str = "python"
    function_name: str = ""
    starter_code: str = ""

    # open / voice
    question: str = ""
    answer_format: str = ""
    #: Voice is answered by speaking; open may be typed. The client needs to know which
    #: without inspecting a payload it is not allowed to see.
    spoken: bool = False


class PresentingDTO(BaseModel):
    """Which variable is being probed, why this item, and the item itself."""

    variable: str
    criterion: Literal["KL", "E[Fisher]"]
    item: PresentedItemDTO


class GradeReceiptDTO(BaseModel):
    """Minimal acknowledgement, safe to return while the assessment is still running.

    `flags` carries INFRASTRUCTURE_* only. A candidate is entitled to know the sandbox
    failed — that response moved no estimate and they may be asked again — and entitled to
    nothing else until the session ends.
    """

    item_id: str
    modality: Modality
    accepted: bool = True
    flags: list[str] = Field(default_factory=list)


class VariableReportDTO(BaseModel):
    """One competency's result.

    `standard_error` is the REPORTED value, widened when the deployment applies a
    calibration correction; `standard_error_raw` is the posterior's own, which is what the
    stopping rule read. Both are present because a single widened number cannot be checked
    against the rule that produced the session, and a single raw number is the
    overconfident one that was measured 33-45% too narrow.

    Report the BAND RANGE, not the point band. At the SE target, within-one-level accuracy
    runs about 99% while exact-band accuracy runs about 65%.
    """

    variable: str
    theta_hat: float
    standard_error: float
    standard_error_raw: float = 0.0
    interval_widening_factor: float = 1.0
    #: A monotone remap of the posterior SD. NOT the probability the reported level is
    #: correct — at SE 0.55 this reads 90 while P(correct band) is about 0.64 at a band
    #: centre and 0.47 near a boundary. `p_reported_band` is the number a reader assumes
    #: they are being given.
    precision_index_pct: float = 0.0
    certainty_pct: float = 0.0
    level: int | None = None
    band: str = ""
    p_reported_band: float = 0.0
    band_probability: float = 0.0
    most_probable_band: int | None = None
    credible_interval_95: tuple[float, float] | None = None
    aberrant_response_count: int = 0
    graph_coverage_satisfied: bool = True
    graph_unmeasured_nodes: list[str] = Field(default_factory=list)
    observations: int = 0
    finalised: bool = False
    converged: bool = False
    #: `converged` is an engine outcome; certification is a separate release decision.
    #: Until external exact-band calibration passes, even a converged estimate is
    #: presented as provisional.
    decision_status: Literal["not_assessed", "provisional", "certified"] = "provisional"
    stop_reason: str = ""
    modalities_used: list[str] = Field(default_factory=list)


class AssessmentReportDTO(BaseModel):
    session_id: str
    items_administered: int = 0
    variables: list[VariableReportDTO] = Field(default_factory=list)
    all_finalised: bool = False
    stop_reason: str = ""
    #: Node-level provenance, populated whenever a graph loads whatever the enforcement
    #: flags say — a report that only appears once a feature is on cannot inform the
    #: decision to turn it on.
    graph: dict[str, Any] = Field(default_factory=dict)
    aberrant_responses: list[dict[str, Any]] = Field(default_factory=list)
    seconds_by_item: dict[str, float] = Field(default_factory=dict)
    #: The scope this assessment was measured under, when it was narrowed to one. Absent
    #: for a whole-bank assessment.
    #:
    #: It carries `partial_mains` because a partially scoped main is estimated from a
    #: corner of itself — the exact thing the coverage gate exists to prevent when it
    #: happens by accident. Legitimate on purpose, but a number that does not say so can be
    #: compared against a whole-bank score, and the two do not mean the same thing.
    scope: ScopeSummaryDTO | None = None


class AssessmentStateResponse(BaseModel):
    """The candidate-facing view of a session. Returned by every lifecycle call.

    One shape for begin, poll and answer, so a client has one thing to render and one
    place to look for "what now" — `presenting` when there is a question, `report` when
    there is not.
    """

    session_id: str
    bank_id: str
    #: The bank version this session began against. Pinned at creation: replacing a bank
    #: mid-session must not change the pool underneath a candidate.
    bank_version: str = ""
    stop: bool = False
    stop_reason: str = ""
    items_administered: int = 0
    open_variables: list[str] = Field(default_factory=list)
    presenting: PresentingDTO | None = None
    last_graded: GradeReceiptDTO | None = None
    report: AssessmentReportDTO | None = None


class VariableDiagnosticsDTO(BaseModel):
    """What an assessment author needs to see mid-session. Not candidate-safe."""

    variable: str
    theta_hat: float
    standard_error: float
    precision_index_pct: float
    level: int | None = None
    band: str = ""
    credible_interval_95: tuple[float, float] | None = None
    observations: int
    finalised: bool
    converged: bool
    stop_reason: str = ""
    #: KL for the first three observations, E[Fisher] thereafter.
    criterion: str = ""
    served_item_ids: list[str] = Field(default_factory=list)
    score_history: list[float] = Field(default_factory=list)
    band_history: list[int] = Field(default_factory=list)
    #: The information the CURRENT item carries for this variable, when it is the one being
    #: presented. This is what makes a selection auditable rather than merely logged.
    presenting_information: float | None = None
    graph_unmeasured_nodes: list[str] = Field(default_factory=list)


class DiagnosticsResponse(BaseModel):
    session_id: str
    bank_id: str
    variables: list[VariableDiagnosticsDTO] = Field(default_factory=list)
    #: The full queued candidate for each open variable: criterion, information, utility,
    #: what the engine would have picked, whether the model overrode it and why.
    queue: dict[str, dict[str, Any]] = Field(default_factory=dict)
    presenting: dict[str, Any] | None = None
    #: Per-variable graph node state and coverage.
    graph: dict[str, Any] = Field(default_factory=dict)
    elapsed_minutes: float = 0.0
    seconds_by_item: dict[str, float] = Field(default_factory=dict)
    aberrant_responses: list[dict[str, Any]] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """Every non-2xx body. One shape, so a client has one error path."""

    detail: str
    #: A stable, greppable code. `detail` is for a human; this is for a client's branch.
    code: str = ""
