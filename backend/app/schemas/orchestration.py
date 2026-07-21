"""The typed boundary of the orchestrated, multi-modality assessment.

One envelope for every item, whatever grades it. The envelope carries what the ENGINE
needs — which variables the item measures and its CAT parameters on theta — and the
modality payload carries what the GRADER needs. That split is what lets a new modality be
added without touching selection, and what stops selection from having to know what a test
case or an answer index is.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Modality = Literal["mcq", "code", "open"]


class CatParameters(BaseModel):
    """Item parameters ON THETA, for every modality.

    The invariant the whole design rests on. MCQ items arrive calibrated; code items are
    mapped here by `orchestrator.calibration`, which is provisional. Bounds match the MCQ
    engine's Item schema so a mis-calibrated bank is caught at the door rather than
    producing silently meaningless information scores.
    """

    a: float = Field(gt=0.0, le=3.0, description="discrimination")
    b: float = Field(ge=-4.0, le=4.0, description="difficulty on the ability scale")
    c: float = Field(ge=0.0, lt=1.0, default=0.0, description="guessing floor")


class MeasuredVariable(BaseModel):
    """A variable this item measures, and how much of it the item carries.

    The architecture's "required variables". An MCQ item names one; a code question names
    several with weights, because one submission genuinely evidences several competencies.
    """

    variable: str
    weight: float = Field(gt=0.0, le=1.0, default=1.0)


class BankItem(BaseModel):
    """One item of any modality."""

    item_id: str
    modality: Modality
    status: str = "active"

    # Human-facing taxonomy, shared by both source banks already.
    competency: str = ""
    sub_competency: str = ""

    measures: list[MeasuredVariable] = Field(min_length=1)
    cat: CatParameters

    # Exactly one of these is populated, matching `modality`. Kept as open dicts because
    # the grader owns their shape: forcing them through a union here would make every new
    # modality a change to this file and to everything that imports it.
    mcq: dict[str, Any] | None = None
    code: dict[str, Any] | None = None
    open: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _payload_matches_modality(self) -> "BankItem":
        payload = getattr(self, self.modality, None)
        if payload is None:
            raise ValueError(f"item {self.item_id}: modality {self.modality!r} has no payload")
        return self

    @property
    def payload(self) -> dict[str, Any]:
        return getattr(self, self.modality)

    def loading(self, variable: str) -> float:
        """How much of this item measures `variable`. 0.0 when it does not at all."""
        for entry in self.measures:
            if entry.variable == variable:
                return entry.weight
        return 0.0


class VariableState(BaseModel):
    """The live estimate of one variable — the architecture's Examinee Variables, per entry.

    The posterior is carried as a plain list so the state round-trips through JSON. A
    session may be persisted between requests, and summarising to (mean, sd) instead would
    be lossy: a 3PL posterior is genuinely skewed near the guessing floor and the selection
    criterion integrates over its real shape.
    """

    variable: str
    posterior: list[float]
    theta_hat: float = 0.0
    standard_error: float = 2.0
    observations: int = 0
    served_item_ids: list[str] = Field(default_factory=list)
    band_history: list[int] = Field(default_factory=list)

    # Set once a stopping rule fires. A finalised variable is never picked for again.
    finalised: bool = False
    stop_reason: str = ""
    converged: bool = False


class QueuedCandidate(BaseModel):
    """One item waiting in the queue, with why it was chosen."""

    variable: str
    item_id: str
    modality: Modality
    criterion: Literal["KL", "E[Fisher]"]
    information: float
    utility: float
    best_information: float
    normalized_regret: float
    engine_top_pick: str
    chosen_by_llm: bool
    reason_code: str = ""
    reason: str = ""
    shortlist_ids: list[str] = Field(default_factory=list)


class GradedResponse(BaseModel):
    """What the grader returns for one administered item, before it touches any estimate."""

    item_id: str
    modality: Modality
    outcomes: list[dict[str, Any]] = Field(default_factory=list)
    # Modality-specific detail for the audit record: the code path carries a full
    # SubmissionResult, MCQ carries the chosen index.
    detail: dict[str, Any] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)


class AssessmentState(BaseModel):
    """Everything one orchestrated assessment needs to resume on a different worker."""

    session_id: str
    variables: dict[str, VariableState] = Field(default_factory=dict)
    queue: dict[str, QueuedCandidate] = Field(default_factory=dict)
    served_item_ids: list[str] = Field(default_factory=list)
    items_administered: int = 0
    started_at: float = 0.0
    elapsed_minutes: float = 0.0

    @property
    def open_variables(self) -> list[str]:
        """Variables still being measured. The only ones the picker is invoked for."""
        return sorted(v for v, s in self.variables.items() if not s.finalised)


class VariableReport(BaseModel):
    variable: str
    theta_hat: float
    standard_error: float
    certainty_pct: float
    level: int | None
    band: str
    observations: int
    finalised: bool
    converged: bool
    stop_reason: str
    modalities_used: list[str] = Field(default_factory=list)


class AssessmentReport(BaseModel):
    session_id: str
    items_administered: int
    variables: list[VariableReport] = Field(default_factory=list)
    all_finalised: bool = False
    stop_reason: str = ""
