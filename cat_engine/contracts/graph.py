"""The propagation surface: node state in, a delta out. No session store.

WHY PROPAGATION HOLDS NOTHING

The graph layer was once planned to own per-session node state. It does not, and the reason
is the property the whole design rests on: the orchestrator is stateless between calls, so
an assessment can be persisted between them and resumed in a different process. That only
works while `AssessmentState` is the single source of truth. A graph holding its own copy of
the same session's node state creates a second one, and the two disagree the first time a
call is retried.

So the caller passes what it knows and gets back what changed. That was true when
propagation was a service and it is true now that it is a function — which is why collapsing
one into the other changed nothing here.

WHAT COMES BACK CANNOT BE MISTAKEN FOR EVIDENCE

`InferredSignalDTO` has no `score` and no `weight` field. A deduction FROM a response is
not a second response; multiplying it into a likelihood counts one answer twice, and the
damage lands on the standard error, which is what the assessment stops on. Because the type
has no such field, a buggy graph cannot hand the loop something it could mistake for
evidence — the boundary is enforced by the contract rather than by a reviewer noticing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .envelopes import InferredSignalDTO, Modality


class EvidenceOutcomeDTO(BaseModel):
    """One graded statement, as the graph receives it.

    Structurally a `GradedOutcomeDTO`; named separately because what the graph does with
    `score` and `weight` is decide a node's STATUS, never multiply a likelihood. Two names
    for one shape is cheaper than one name for two meanings.
    """

    variable: str
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0, default=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    source_item_id: str = ""
    modality: Modality | None = None


class PropagationItemDTO(BaseModel):
    """What the graph needs to know about the item a response answered.

    Not the item — three fields of it. The graph has no business seeing a stem, and the
    only reason it needs `minimum_success_confidence` is that a per-item floor exists to
    TIGHTEN the global one, never to loosen it.
    """

    item_id: str
    modality: Modality
    minimum_success_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PropagateRequest(BaseModel):
    """Everything one response did, plus everything the session already knew.

    `graph_state` is the session's own graph slice — every `graph_*` field of
    `AssessmentState`, sent back on every call. Two of them carry the idempotency:
    `graph_processed_evidence_ids` records what has already been absorbed, and evidence ids
    are deterministic, so replaying a response the graph has already seen changes nothing.
    """

    bank_id: str
    session_id: str
    item: PropagationItemDTO
    outcomes: list[EvidenceOutcomeDTO] = Field(default_factory=list)

    #: The `graph_*` fields of `AssessmentState`, verbatim. A flat dict rather than a typed
    #: model because it IS that projection field for field — typing it here would create a
    #: second definition of a shape the engine's schema owns, and the two would drift.
    graph_state: dict[str, Any] = Field(default_factory=dict)

    #: Which mains this session is measuring. Bounds `selection_affected_mains`, so a node
    #: shared with a competency nobody is being assessed on cannot invalidate a queue slot
    #: that does not exist.
    session_variables: list[str] = Field(default_factory=list)
    #: How many times this item has been administered in this session, including now. Part
    #: of the evidence id, so a re-administration is not read as a duplicate.
    attempt_no: int = Field(default=1, ge=1)


class PropagateResponse(BaseModel):
    """The delta, in the shape the session persists it."""

    #: Applied verbatim to `AssessmentState`. Empty when nothing was applied.
    state_update: dict[str, Any] = Field(default_factory=dict)
    #: Mains whose ELIGIBILITY changed. A blocked or reopened node makes a queued pick
    #: stale without any estimate having moved, so those slots must be refilled.
    selection_affected_mains: list[str] = Field(default_factory=list)
    #: Reporting only. Never handed to a posterior update — it structurally cannot be.
    inferred_signals: list[InferredSignalDTO] = Field(default_factory=list)
    #: The configuration this call ran under, reported by whoever OWNS propagation rather
    #: than recomputed by the caller. Split across services, two answers to "what
    #: configuration is in force" is exactly the failure the manifest exists to detect.
    manifest: dict[str, Any] = Field(default_factory=dict)
    manifest_hash: str = ""
    #: False when the graph was off, unusable, or the batch failed. The caller keeps its
    #: prior state and the candidate pays nothing for a graph problem.
    applied: bool = True
    detail: str = ""


class ManifestResponse(BaseModel):
    """The propagation configuration in force for a bank, and its hash.

    Read at `begin` and stamped into the session, so a result can name the configuration
    that produced it. A number that cannot say what configuration produced it cannot be
    reproduced or believed.
    """

    bank_id: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    manifest_hash: str = ""


class CoverageRequest(BaseModel):
    """Which required sub-competencies of a main still have no direct evidence."""

    bank_id: str
    main: str
    directly_measured: list[str] = Field(default_factory=list)
    #: Critical-only reduces the requirement to the nodes marked critical. Per bank,
    #: because the right answer depends on how many sub-nodes a main declares against how
    #: many questions the budget allows.
    critical_only: bool = False


class CoverageResponse(BaseModel):
    main: str
    required: list[str] = Field(default_factory=list)
    unmeasured: list[str] = Field(default_factory=list)
    satisfied: bool = True


__all__ = [
    "CoverageRequest",
    "CoverageResponse",
    "EvidenceOutcomeDTO",
    "ManifestResponse",
    "PropagateRequest",
    "PropagateResponse",
    "PropagationItemDTO",
]
