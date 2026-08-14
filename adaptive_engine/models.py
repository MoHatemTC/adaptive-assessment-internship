"""The assessment definition: what the caller supplies, in the engine's own shapes.

This layer deliberately does NOT invent a second content model. ``items`` are bank-item
payloads exactly as ``cat_engine``'s ``BankItem`` schema defines them (and as the
authoring branch produces them), and ``graph`` is the competency-graph JSON the graph
validator already parses. Compilation validates both with the engine's own validators, so
there is one definition of "a valid item" and "a valid graph" in the whole repository.

Everything here is immutable and validated at the door; the caller owns persistence and
versioning of the definition itself.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrozenModel(BaseModel):
    """Base for this layer's models: immutable, strict about shape and floats."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


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

    ``items``: bank items in the engine's ``BankItem`` wire shape — modality, measures
    (sub-competency loadings), IRT parameters, and the modality payload including grading
    data where grading is deterministic. THIS MODEL IS NOT CANDIDATE-SAFE; only
    ``decision.question`` ever is.

    ``graph``: the competency-graph JSON (nodes, edges, optional bank policy) as the
    authoring branch emits it, or None for an ungated assessment.

    ``targets``: the main competencies to assess. None assesses every main the items
    measure.
    """

    assessment_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    items: list[dict[str, Any]] = Field(min_length=1)
    graph: dict[str, Any] | None = None
    targets: list[str] | None = None
    policy: MeasurementPolicy | None = None
