"""Wire types. Deliberately anaemic — no behaviour, no imports from any service."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# Bumped when a field is removed or its meaning changes. Additive fields do not bump it.
#
# 1.1.0 — `BankSubmission` and `BankItemSubmission` removed. Writing a bank moved to
# `bank-ingest`, which takes an uploaded file rather than a JSON body, so the types that
# described that body describe nothing. A removal, so the version moves; `scope` on
# `CreateAssessmentRequest` and `status` on `BankItemRef` arrived in the same release and
# are additive, so they did not.
# Every service reports the version it was built against on `GET /health`, so a mismatch is
# visible in a dashboard rather than in a decoding error three hops away.
SCHEMA_VERSION = "1.1.0"

Modality = Literal["mcq", "code", "open", "voice"]


class CatParameters(BaseModel):
    """Item parameters ON THETA, for every modality.

    The invariant the whole design rests on: a code question and a multiple-choice question
    are comparable because both are calibrated to the same latent scale. A service that
    returns items without these is not a bank.
    """

    a: float = Field(gt=0.0, le=3.0, description="discrimination")
    b: float = Field(ge=-4.0, le=4.0, description="difficulty on the ability scale")
    c: float = Field(ge=0.0, lt=1.0, default=0.0, description="guessing floor")


class MeasuredVariableRef(BaseModel):
    variable: str
    weight: float = Field(gt=0.0, le=1.0, default=1.0)


class BankItemRef(BaseModel):
    """What SELECTION needs about an item. Never the payload.

    The split is what lets the orchestrator rank items without being able to read a
    question, and it is why `bank-registry` can serve two different callers — one that
    ranks and one that renders — with different authorisation.
    """

    item_id: str
    modality: Modality
    measures: list[MeasuredVariableRef] = Field(min_length=1)
    cat: CatParameters
    estimated_time_seconds: float | None = Field(default=None, gt=0.0)
    minimum_success_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    #: WHETHER SELECTION MAY ADMINISTER THIS ITEM AT ALL, and therefore part of the ranking
    #: view rather than of the payload. Omitting it was a defect: this model defaults it to
    #: "active" exactly as `BankItem` does, so an item the bank had retired came back over
    #: the wire indistinguishable from a live one and the orchestrator ranked it. `JAI-600`
    #: retires 64 code items as `inactive_missing_hidden_tests` — items whose test suite is
    #: incomplete — so the HTTP path was selecting, and grading against, questions the
    #: in-process engine refuses to serve.
    status: str = "active"


class GradedOutcomeDTO(BaseModel):
    """One graded statement about one variable, from any modality. The narrow waist.

    `weight` is how much of an observation this is worth, in [0, 1], and it is separate
    from `score` on purpose: a submission that failed to compile scores 0 but demonstrates
    very little, and punishing a competency at full strength for a typo measures the wrong
    thing. Weight 0 means the response carries no evidence and must move nothing.
    """

    variable: str
    score: float = Field(ge=0.0, le=1.0)
    weight: float = Field(ge=0.0, le=1.0, default=1.0)
    confidence: float = Field(ge=0.0, le=1.0, default=1.0)
    source_item_id: str = ""
    modality: Modality | Literal[""] = ""


class GradedResponseDTO(BaseModel):
    item_id: str
    modality: Modality
    outcomes: list[GradedOutcomeDTO] = Field(default_factory=list)
    detail: dict[str, Any] = Field(default_factory=dict)
    flags: list[str] = Field(default_factory=list)


class InferredSignalDTO(BaseModel):
    """What the graph concluded about a node it did not directly test.

    CARRIES NO SCORE AND NO WEIGHT, and that is structural rather than stylistic. A
    deduction FROM a response is not a second response; multiplying it into a likelihood
    counts one answer twice, and the damage lands on the standard error, which is what the
    assessment stops on. Because this type has no `score` field, a graph service cannot
    hand the orchestrator something it could mistake for evidence — the boundary is
    enforced by the contract rather than by a reviewer noticing.
    """

    node: str
    source_node: str
    distance: int
    strength: float
    source_evidence_id: str
    modality: Modality
