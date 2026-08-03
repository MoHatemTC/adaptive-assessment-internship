"""Propagation policy, and the one place it is constructed.

Every threshold here used to be a literal default constructed at five call sites, none of
which could be changed without a deploy. They are the constants the specification itself
admits are uncalibrated, so they are exactly the ones an operator needs to move while
watching a metric.

`graph_enabled()` is the master switch. It was declared in settings and read by nothing —
worse than absent, because an operator will reach for it during an incident and nothing
will happen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping

from app.config.settings import settings

# How much a direct hit in one modality says about a PREREQUISITE (review C10).
#
# Applied to inference strength only, never to direct evidence weight: the graders already
# attenuate by modality (`_grade_code` uses evidence strength, `grade_voice` multiplies it
# by grader confidence), so applying it again there would double-discount — and under the
# posterior-isolation rule the CAT update must not see graph configuration at all.
DEFAULT_SOURCE_MULTIPLIERS: Mapping[str, float] = MappingProxyType(
    {"mcq": 0.80, "code": 1.00, "voice": 0.75, "open": 0.80}
)


@dataclass(frozen=True)
class PropagationConfig:
    """Thresholds and policies for graph propagation.

    Defaults are the specification's suggested values. They are hypotheses: nothing here
    has been calibrated against candidate data, which is why inference stays flag-gated
    and why the edges that would carry it ship inert.
    """

    strong_success_threshold: float = 0.8
    strong_failure_threshold: float = 0.2
    minimum_propagation_confidence: float = 0.8

    upward_decay: float = 0.7
    minimum_inferred_weight: float = 0.15
    maximum_inferred_weight: float = 0.6

    downward_block_confidence: float = 0.85
    maximum_propagation_depth: int = 4

    allow_mcq_single_hit_upward_inference: bool = False
    allow_code_upward_inference: bool = True
    allow_voice_upward_inference: bool = True

    # Compute the same conclusions a second time with the per-edge validation gates
    # waived, for the record only. See `preview` in `propagation.py`.
    preview_unvalidated_edges: bool = True

    source_multipliers: Mapping[str, float] = field(
        default_factory=lambda: DEFAULT_SOURCE_MULTIPLIERS
    )

    def source_multiplier(self, modality: str) -> float:
        """Attenuation for an inference drawn from this modality. Unknown -> 1.0.

        Failing open here is safe because `upward_inference_allowed` has already refused
        an unknown modality outright; this only ever scales something already permitted.
        """
        return float(self.source_multipliers.get((modality or "").strip().lower(), 1.0))

    def upward_inference_allowed(self, modality: str) -> bool:
        """Whether ONE direct hit in this modality may imply prerequisite mastery.

        Fails closed on an unknown modality. A single MCQ answer does not, by default:
        the specification asks for two confirming items before a multiple-choice hit
        licenses an inference, and that is not implemented, so one hit licenses nothing.
        """
        normalized = (modality or "").strip().lower()
        if normalized == "mcq":
            return self.allow_mcq_single_hit_upward_inference
        if normalized == "code":
            return self.allow_code_upward_inference
        if normalized in {"open", "voice", "audio"}:
            return self.allow_voice_upward_inference
        return False


def propagation_config_from_settings() -> PropagationConfig:
    """The only place a PropagationConfig is built from configuration."""
    return PropagationConfig(
        strong_success_threshold=settings.graph_strong_success_threshold,
        strong_failure_threshold=settings.graph_strong_failure_threshold,
        minimum_propagation_confidence=settings.graph_minimum_propagation_confidence,
        upward_decay=settings.graph_upward_decay,
        minimum_inferred_weight=settings.graph_minimum_inferred_weight,
        maximum_inferred_weight=settings.graph_maximum_inferred_weight,
        downward_block_confidence=settings.graph_downward_block_confidence,
        maximum_propagation_depth=settings.graph_maximum_propagation_depth,
        allow_mcq_single_hit_upward_inference=settings.graph_allow_mcq_single_hit_inference,
        allow_code_upward_inference=settings.graph_allow_code_upward_inference,
        allow_voice_upward_inference=settings.graph_allow_voice_upward_inference,
        preview_unvalidated_edges=edge_preview_enabled(),
    )


def graph_enabled() -> bool:
    """The master kill switch. Every graph entry point checks it first."""
    return settings.competency_graph_enabled


def shadow_mode_enabled() -> bool:
    return graph_enabled() and settings.graph_shadow_mode


def filtering_enabled() -> bool:
    return graph_enabled() and settings.graph_filtering_enabled


def upward_inference_enabled() -> bool:
    return graph_enabled() and settings.graph_upward_inference_enabled


def descendant_blocking_enabled() -> bool:
    return graph_enabled() and settings.graph_descendant_blocking_enabled


def utility_enabled() -> bool:
    return graph_enabled() and settings.graph_utility_enabled


def convergence_gate_enabled() -> bool:
    return graph_enabled() and settings.graph_convergence_gate_enabled


def edge_preview_enabled() -> bool:
    """Record what the unvalidated edges would conclude. Never act on it."""
    return graph_enabled() and settings.graph_edge_preview_enabled
