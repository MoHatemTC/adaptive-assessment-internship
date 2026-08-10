"""The bank-registry surface: what a bank is on the wire, and what it takes to post one.

TWO READ SHAPES, AND THE SPLIT IS THE POINT

`BankItemRef` (in `envelopes`) carries what SELECTION needs — a, b, c, which variables the
item measures, how long it takes. It carries no stem, no options and no answer.
`BankItemFull` adds the modality payload, and is what RENDERING and GRADING need.

Collapsing them would silently give one caller's authorisation to both: the orchestrator
ranks the whole eligible pool on every step and must never be able to read a question, and
the grader needs hidden tests and answer keys that must never reach a candidate's browser.

THE WRITE SHAPE

`BankSubmission` is the whole of what another service posts to register a bank. Items and
graph arrive together on purpose. A bank without its graph pairs with nothing, and the
coverage gate then asks a graph that was authored for a different bank which
sub-competencies a main requires — which marks every required node unmeasured and vetoes
convergence for the rest of the session. Making the pair the unit of submission is what
stops that being possible to get wrong.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .envelopes import BankItemRef, CatParameters, MeasuredVariableRef, Modality


class BankItemFull(BankItemRef):
    """A ref plus the modality payload. For rendering and for grading, never for ranking.

    The payload stays an open dict because the GRADER owns its shape — forcing it through
    a union here would make every new modality a change to this file and to everything
    that imports it, which is the coupling the envelope split exists to avoid.
    """

    status: str = "active"
    competency: str = ""
    sub_competency: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class BankSummary(BaseModel):
    """One row of the bank catalogue. What a picker, an operator or a frontend lists."""

    bank_id: str
    title: str
    #: Content hash over items, graph and profile. Served as an ETag and pinned into the
    #: assessment state, so replacing a bank cannot change the pool under a live session.
    version: str = ""
    mains: list[str] = Field(default_factory=list)
    items: int = 0
    modalities: list[str] = Field(default_factory=list)
    has_graph: bool = False
    coverage_critical_only: bool = False
    #: Where this bank came from. `seed` is checked in and read-only; `stored` was posted.
    source: Literal["seed", "stored"] = "seed"
    #: Set when the bank could not be read. A broken bank must not hide the working ones
    #: from a picker, so it is listed with its error rather than omitted.
    error: str = ""


class GraphNodeDTO(BaseModel):
    competency_id: str
    title: str = ""
    node_type: Literal["main", "sub_competency"] = "sub_competency"
    critical: bool = False
    context_specific: bool = False
    main_competencies: list[str] = Field(default_factory=list)


class GraphEdgeDTO(BaseModel):
    """One edge, in the same shape the graph FILE uses.

    `from` and `to` are Python keywords, so the attributes are `from_id`/`to_id` and the
    wire names are aliases. That is worth the small awkwardness: it means a bank author can
    POST exactly the graph JSON they would otherwise check in, rather than learning a
    second spelling of a format they already have.
    """

    model_config = ConfigDict(populate_by_name=True)

    from_id: str = Field(alias="from")
    to_id: str = Field(alias="to")
    relation: str
    strength: float = 1.0
    weight: float = 1.0
    #: The AUTHORED flags. What the edge is permitted to do after policy resolution is in
    #: `PolicyDTO.edges`, which is a different question and is answered per deployment.
    allow_upward_inference: bool = True
    allow_downward_blocking: bool = True


class CompetencyGraphDTO(BaseModel):
    schema_version: str = "1.0"
    nodes: list[GraphNodeDTO] = Field(default_factory=list)
    edges: list[GraphEdgeDTO] = Field(default_factory=list)
    policy: dict[str, Any] = Field(default_factory=dict)


class EdgeDecisionDTO(BaseModel):
    """What one edge is permitted to do, and — when it is not — why.

    `*_allowed` is all three levels: deployment, bank and edge. `*_authorised_by_bank`
    excludes the deployment switch. The split matters because deployment governs
    ENFORCEMENT while bank and edge govern VALIDITY, and the audit mirror exists to record
    what would have happened had enforcement been on.
    """

    parent: str
    child: str
    validation_status: str = ""
    inference_allowed: bool = False
    blocking_allowed: bool = False
    inference_authorised_by_bank: bool = False
    blocking_authorised_by_bank: bool = False
    inference_blocked_by: str = ""
    blocking_blocked_by: str = ""


class PolicyDTO(BaseModel):
    """The resolved propagation policy, so an operator can ask "why is this edge inert?"
    without reading three files and doing the AND in their head."""

    prerequisite_edges: int = 0
    inference_enabled: int = 0
    blocking_enabled: int = 0
    minimum_failures_to_block: int = 2
    deployment: dict[str, Any] = Field(default_factory=dict)
    bank_policy: dict[str, Any] = Field(default_factory=dict)
    inert_because: dict[str, int] = Field(default_factory=dict)
    edges: list[EdgeDecisionDTO] = Field(default_factory=list)


class ParityRowDTO(BaseModel):
    """One variable's information-parity diagnostic.

    Separates the two reasons a modality can lose a ranking: `rarely_selected` means it
    loses WITH loading applied, which is expected and correct — an item loading 0.2 on a
    variable genuinely tells you less. `miscalibrated` means it loses even at full loading,
    which means the item parameters are wrong.
    """

    variable: str
    verdict: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class BankItemSubmission(BaseModel):
    """One item as another service posts it.

    The bounds on `cat` are enforced here rather than at first use. A bank whose
    discrimination is 12.0 is not a bank that produces slightly odd rankings; it is one
    that produces meaningless information scores, and the door is the cheapest place to
    find out.
    """

    item_id: str
    modality: Modality
    status: str = "active"
    competency: str = ""
    sub_competency: str = ""
    measures: list[MeasuredVariableRef] = Field(min_length=1)
    cat: CatParameters
    estimated_time_seconds: float | None = Field(default=None, gt=0.0)
    minimum_success_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    #: The modality payload, keyed by modality name — exactly one key, matching `modality`.
    payload: dict[str, Any] = Field(default_factory=dict)


class BankSubmission(BaseModel):
    """A whole bank, posted by another service. Items and graph together — see the module
    docstring for why the pair is the unit."""

    bank_id: str = Field(min_length=1, max_length=64)
    title: str = ""
    items: list[BankItemSubmission] = Field(min_length=1)
    graph: CompetencyGraphDTO | None = None
    #: None defers to the deployment default. Set per bank because the right answer depends
    #: on how many sub-competencies sit under a main against how many questions the budget
    #: allows — a main with sixteen sub-nodes and a twelve-question cap can never satisfy
    #: full coverage, so every session would end on the budget escape.
    coverage_critical_only: bool | None = None


class ValidationFinding(BaseModel):
    """One thing wrong, or one thing worth knowing, about a submitted bank."""

    severity: Literal["error", "warning"]
    code: str
    message: str
    #: Which item, node or edge. Empty when the finding is about the bank as a whole.
    subject: str = ""


class BankValidationReport(BaseModel):
    """Why a bank was accepted or refused, in enough detail to fix it.

    Warnings never block. A bank whose code items rarely win a ranking is a bank worth
    knowing about, not a bank worth refusing — the distinction between "loses because it
    genuinely measures less" and "loses because its parameters are wrong" is the whole
    point of the parity diagnostic, and only the second is an error.
    """

    bank_id: str
    accepted: bool
    version: str = ""
    items: int = 0
    mains: list[str] = Field(default_factory=list)
    modalities: list[str] = Field(default_factory=list)
    findings: list[ValidationFinding] = Field(default_factory=list)

    @property
    def errors(self) -> list[ValidationFinding]:
        return [f for f in self.findings if f.severity == "error"]
