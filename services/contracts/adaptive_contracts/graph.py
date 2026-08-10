"""The competency-graph surface: node state in, a delta out. No session store.

WHY THIS SERVICE IS STATELESS

`MIGRATION.md` planned `POST /sessions/{id}/evidence`, with the graph owning per-session
node state. It does not, and the reason is the property the whole architecture rests on:
the orchestrator is stateless between calls, so an assessment can be persisted between
requests and resumed on a different worker. That only works while `AssessmentState` is the
single source of truth. A graph service holding its own copy of the same session's node
state creates a second one, and the two disagree the first time a request is retried.

So the caller sends what it knows and gets back what changed. The service holds nothing,
scales horizontally for free, and a restart loses nothing.

WHAT COMES BACK CANNOT BE MISTAKEN FOR EVIDENCE

`InferredSignalDTO` has no `score` and no `weight` field. A deduction FROM a response is
not a second response; multiplying it into a likelihood counts one answer twice, and the
damage lands on the standard error, which is what the assessment stops on. Because the
type has no such field, a compromised or buggy graph service cannot hand the orchestrator
something it could mistake for evidence — the boundary is enforced by the contract rather
than by a reviewer noticing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .envelopes import InferredSignalDTO, Modality


class EvidenceOutcomeDTO(BaseModel):
    """One graded statement, as the graph receives it.

    Structurally a `GradedOutcomeDTO`; named separately because what the graph does with
    `score` and `weight` is decide a node's STATUS, never to multiply a likelihood. Two
    names for one shape is cheaper than one name for two meanings.
    """

    variable: str
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0, default=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    source_item_id: str = ""
    modality: Modality | None = None


class PropagateRequest(BaseModel):
    """Everything one response did, plus everything the session already knew.

    `node_states` and `processed_evidence_ids` are the session's own record, sent back on
    every call. `processed_evidence_ids` is what makes a retry safe: evidence ids are
    deterministic, so replaying a response the graph has already absorbed changes nothing.
    """

    bank_id: str
    session_id: str
    item_id: str
    modality: Modality
    outcomes: list[EvidenceOutcomeDTO] = Field(default_factory=list)

    #: The session's graph state, as `AssessmentState.graph_node_states`.
    node_states: dict[str, dict[str, Any]] = Field(default_factory=dict)
    processed_evidence_ids: list[str] = Field(default_factory=list)
    #: The rest of `GraphDelta.restore` — what the session already concluded. Sent so the
    #: returned delta is cumulative and the caller can replace rather than merge.
    prior: dict[str, Any] = Field(default_factory=dict)

    #: Which mains this session is measuring. Bounds `selection_affected_mains`, so a
    #: node shared with a competency nobody is being assessed on cannot invalidate a queue
    #: slot that does not exist.
    session_variables: list[str] = Field(default_factory=list)
    #: How many times this item has been administered in this session, including now.
    #: Part of the evidence id, so a re-administration is not read as a duplicate.
    attempt_no: int = Field(default=1, ge=1)
    #: The item's own confidence floor, when it is stricter than the global one. A per-item
    #: floor exists to tighten, never to loosen.
    item_minimum_confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class PropagateResponse(BaseModel):
    """The delta, in the shape the session persists it.

    `state_update` is applied verbatim to `AssessmentState`. It is a flat dict rather than
    a typed model because it is the projection the session already stores, field for
    field — typing it here would create a second definition of a shape that the engine's
    own schema already owns, and the two would drift.
    """

    state_update: dict[str, Any] = Field(default_factory=dict)
    #: Mains whose ELIGIBILITY changed. A blocked or reopened node makes a queued pick
    #: stale without any estimate having moved, so those slots must be refilled.
    selection_affected_mains: list[str] = Field(default_factory=list)
    #: Reporting only. Never handed to a posterior update — it structurally cannot be.
    inferred_signals: list[InferredSignalDTO] = Field(default_factory=list)
    #: True when the graph was off, unusable, or the batch failed. The caller keeps its
    #: prior state and the candidate pays nothing for a graph problem.
    applied: bool = True
    detail: str = ""


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


class GraphReportRequest(BaseModel):
    """Node-level provenance for a finished (or in-flight) session."""

    bank_id: str
    mains: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    critical_only: bool = False


class GraphReportResponse(BaseModel):
    report: dict[str, Any] = Field(default_factory=dict)


__all__ = [
    "CoverageRequest",
    "CoverageResponse",
    "EvidenceOutcomeDTO",
    "GraphReportRequest",
    "GraphReportResponse",
    "PropagateRequest",
    "PropagateResponse",
]
