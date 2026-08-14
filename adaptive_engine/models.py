"""The assessment definition: typed input models for an external caller.

This package is called by another service, so every input crossing the boundary is a
declared pydantic model — a caller can generate a schema, and a malformed payload fails
with a field-level error naming exactly what is wrong, never a KeyError three layers
down.

The models deliberately REUSE the engine's own shapes rather than inventing parallel
ones:

- Items are the engine's ``BankItem`` — the one authoritative item schema (modality,
  measures, IRT parameters, payload with grading data). Re-exported here as the public
  input type.
- The graph builds on the engine's wire DTOs (``GraphNodeDTO``/``GraphEdgeDTO``), which
  keep the file format's ``from``/``to`` spelling as aliases — a caller can send exactly
  the graph JSON they would otherwise check in. ``CompetencyGraph`` adds the provenance
  fields this layer's artifacts carry.

Raw JSON dicts still coerce into every model automatically, so hosts sending parsed
JSON keep working — they just get typed validation for free.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from cat_engine.contracts import CompetencyGraphDTO
from cat_engine.engine.schemas.orchestration import BankItem

__all__ = [
    "AssessmentDefinition",
    "BankItem",
    "CompetencyDeclaration",
    "CompetencyGraph",
    "FrozenModel",
    "GraphDerivation",
    "GraphPolicy",
    "MeasurementPolicy",
]


class FrozenModel(BaseModel):
    """Base for this layer's models: immutable, strict about shape and floats."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class GraphPolicy(FrozenModel):
    """The graph's own propagation policy block, as the graph file spells it.

    ``None`` means "no opinion — the deployment decides". A derived graph ships with
    both switches explicitly off, because its edges come from co-measurement rather than
    expertise.
    """

    upward_inference: bool | None = None
    descendant_blocking: bool | None = None
    accepted_validation_statuses: list[str] = Field(default_factory=lambda: ["validated"])
    minimum_failures_to_block: int | None = Field(default=None, ge=1)
    notes: str = ""


class GraphDerivation(FrozenModel):
    """How a derived graph was built — stamped into the artifact, never ambient."""

    basis: str
    relation_threshold: float
    edge_floor: float


class CompetencyGraph(CompetencyGraphDTO):
    """A competency graph in the engine's wire shape, plus this layer's provenance.

    Inherits ``nodes`` (``GraphNodeDTO``) and ``edges`` (``GraphEdgeDTO``, wire aliases
    ``from``/``to``) from the engine's own DTO, so the JSON a caller sends is the JSON
    the graph files use. ``policy`` is typed here; ``derivation`` records the rules a
    derived graph was built under.
    """

    model_config = ConfigDict(frozen=True)

    bank_id: str = ""
    notes: str = ""
    policy: GraphPolicy | None = None  # type: ignore[assignment] — typed, not a raw dict
    derivation: GraphDerivation | None = None

    def wire_format(self) -> dict[str, Any]:
        """The raw dict the engine's graph validator reads (``from``/``to`` spelling)."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class CompetencyDeclaration(FrozenModel):
    """One entry of the optional ``competencies`` block an author supplies alongside
    questions — the two facts a question list cannot imply, plus a display title.

    ``mains`` names every main competency a shared sub-competency serves; left ``None``
    the id prefix decides (``C1.6`` serves ``C1``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    competency_id: str = Field(alias="id", min_length=1)
    title: str = ""
    critical: bool = True
    mains: list[str] | None = None


class MeasurementPolicy(FrozenModel):
    """Optional measurement-policy overrides, mapped onto the engine's settings.

    Every field left ``None`` keeps the engine's shipped default. LIMITATION, BY DESIGN
    OF THE UNDERLYING ENGINE: the measurement policy is process-wide — the engine's
    configuration layer deliberately refuses two different measurement policies in one
    process (``ConfigConflict``), because estimates produced under different stopping
    rules must not be silently mixed. Run differently-tuned assessments in different
    processes until the policy is threaded per-call through the engine.
    """

    #: Posterior standard error at or under which a competency may converge.
    se_target: float | None = Field(default=None, gt=0.0)
    #: Observation floor before any convergence claim.
    min_questions_per_competency: int | None = Field(default=None, ge=1)
    #: Per-competency question cap.
    max_questions_per_competency: int | None = Field(default=None, ge=1)
    #: Whole-assessment question cap.
    max_questions_total: int | None = Field(default=None, ge=1)
    #: Wall-clock limit for the whole assessment.
    time_limit_minutes: int | None = Field(default=None, ge=1)
    #: Master switch for all competency-graph behavior.
    graph_enabled: bool | None = None
    #: Whether graph coverage requires only critical sub-competencies.
    coverage_critical_only: bool | None = None

    def overrides(self) -> dict[str, Any]:
        """The engine-settings overrides this policy asks for, by engine field name."""
        names = {
            "se_target": "cat_se_target",
            "min_questions_per_competency": "cat_min_questions",
            "max_questions_per_competency": "cat_max_questions",
            "max_questions_total": "orchestrator_max_items",
            "time_limit_minutes": "orchestrator_time_limit_minutes",
            "graph_enabled": "competency_graph_enabled",
            "coverage_critical_only": "graph_coverage_critical_only",
        }
        return {
            names[field]: value
            for field, value in self.model_dump().items()
            if value is not None
        }


class AssessmentDefinition(FrozenModel):
    """Everything the engine needs to run one assessment, supplied by the caller.

    ``items`` are engine ``BankItem`` models — modality, measures (sub-competency
    loadings), IRT parameters, and the modality payload including grading data where
    grading is deterministic. THIS MODEL IS NOT CANDIDATE-SAFE; only
    ``decision.question`` ever is.

    ``targets``: the main competencies to assess. None assesses every main the items
    measure.
    """

    assessment_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    items: list[BankItem] = Field(min_length=1)
    graph: CompetencyGraph | None = None
    targets: list[str] | None = None
    policy: MeasurementPolicy | None = None
