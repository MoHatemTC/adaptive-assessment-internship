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

from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.config.settings import settings

if TYPE_CHECKING:  # pragma: no cover - import cycle; only needed for the annotation
    from .inference import InferredNodeSignal


class CorroborationIndependence(StrEnum):
    """What makes two observations of the same ancestor count as two, not one.

    Ordered weakest to strongest. The choice is a safety control, not a detail:
    `minimum_corroborations` is only worth anything if the observations it counts are
    actually independent, and the cheapest way to satisfy a corroboration requirement is
    to answer the same question twice.

    EVIDENCE keys on the evidence id, which embeds item and attempt — so N near-identical
    items yield N keys and the requirement is farmable by construction. It exists to be
    swept against, and to model what a naive implementation would have done.

    SOURCE_NODE keys on the sub-competency the direct hit landed on. Near-identical items
    measure the same node, so any number of clones collapse to one observation forever.
    That is the default, and it is the only setting under which K is a safety control
    rather than a decoration.
    """

    EVIDENCE = "evidence"
    ITEM = "item"
    SOURCE_NODE_MODALITY = "source_node_modality"
    SOURCE_NODE = "source_node"


# Every modality an inference may be drawn FROM. `audio` is a transport, not a modality:
# it is normalised onto `voice`. `open` and `voice` are deliberately separate members —
# they were previously collapsed onto one flag, so a deployment could not permit a graded
# code interview while refusing free-text, and the allowlist could not express the
# difference the graders themselves make.
KNOWN_INFERENCE_MODALITIES: frozenset[str] = frozenset({"mcq", "code", "voice", "open"})
DEFAULT_INFERENCE_MODALITIES: frozenset[str] = frozenset({"code", "voice", "open"})


def normalise_modality(modality: str) -> str:
    """Canonical modality name. Unknown values are returned as-is, to be refused later."""
    text = (modality or "").strip().lower()
    return "voice" if text == "audio" else text


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

    # Inference and blocking depth, resolved through `inference_depth` / `blocking_depth`.
    #
    # SPLIT ON PURPOSE. `maximum_propagation_depth` governed both, so sweeping the
    # inference depth silently swept the blocking frontier with it and the two effects
    # arrived confounded in the same number. They are different mechanisms with different
    # costs — a deep inference wastes a question, a deep block denies a candidate the
    # chance to answer one — and a factorial design that cannot separate them cannot
    # attribute either.
    #
    # None means "defer to maximum_propagation_depth", which is what keeps every existing
    # caller and every direct construction behaving exactly as before.
    maximum_inference_depth: int | None = None
    maximum_blocking_depth: int | None = None

    # Consistent direct failures of a prerequisite before its descendants may be blocked.
    # See `propagation.apply_direct_evidence` for the arithmetic; 1 reproduces the old
    # behaviour and the false-blocking rate that came with it.
    minimum_failures_to_block: int = 2

    # Independent strong successes before an ancestor may be inferred mastered. The
    # counterpart to `minimum_failures_to_block`, and deliberately NOT the same knob: the
    # asymmetry in the comment above is real, so the two move independently.
    #
    # 1 reproduces the behaviour whose wrong-inference rate was measured at 22.4%.
    minimum_corroborations: int = 1
    corroboration_independence: CorroborationIndependence = (
        CorroborationIndependence.SOURCE_NODE
    )

    # Modalities an inference may be drawn from. None derives the set from the three
    # legacy booleans below, so a deployment configured before the allowlist existed keeps
    # its exact behaviour.
    allowed_inference_modalities: frozenset[str] | None = None

    # Edge validation statuses this DEPLOYMENT will act on. None means "no opinion", and
    # the bank's own list stands. A deployment may only ever narrow — see `resolve_policy`.
    accepted_validation_statuses: tuple[str, ...] | None = None

    allow_mcq_single_hit_upward_inference: bool = False
    allow_code_upward_inference: bool = True
    allow_voice_upward_inference: bool = True

    # Whether a conclusion is WRITTEN, as distinct from whether it is DRAWN.
    #
    # These carry the deployment switches into the one object propagation already reads,
    # rather than having `propagation.py` read settings itself. That matters twice over:
    # the ~30 tests that construct a config directly stay meaningful without a settings
    # fixture, and there stops being a second place that can disagree with the first about
    # whether inference is on — which is exactly how node state came to be written for a
    # deployment whose inference switch was off.
    enforce_inference: bool = True
    enforce_blocking: bool = True

    # Compute the same conclusions a second time with the per-edge validation gates
    # waived, for the record only. See `preview` in `propagation.py`.
    preview_unvalidated_edges: bool = True

    source_multipliers: Mapping[str, float] = field(
        default_factory=lambda: DEFAULT_SOURCE_MULTIPLIERS
    )

    def __post_init__(self) -> None:
        """Refuse a configuration that cannot mean anything.

        This class is frozen but was unvalidated, so bounds existed only in the pydantic
        layer — which every direct construction bypasses, including all of `tests/` and
        every sweep cell that builds a config in-process. `PropagationConfig(upward_decay=5.0)`
        constructed happily and produced inference strengths that grew with distance.

        Raise rather than clamp. A clamped factor still appears in the manifest at the
        value that was asked for, so the record of what ran would be false — and the whole
        point of the manifest is that it is not.
        """

        def _bounded(
            name: str, low: float, high: float, *, low_open: bool = False
        ) -> None:
            value = getattr(self, name)
            ok = (low < value if low_open else low <= value) and value <= high
            if not ok:
                bound = f"({low}, {high}]" if low_open else f"[{low}, {high}]"
                raise ValueError(f"{name}={value!r} is outside {bound}")

        for name in (
            "strong_success_threshold",
            "strong_failure_threshold",
            "minimum_propagation_confidence",
            "downward_block_confidence",
            "minimum_inferred_weight",
            "maximum_inferred_weight",
        ):
            _bounded(name, 0.0, 1.0)
        _bounded("upward_decay", 0.0, 1.0, low_open=True)

        for name in (
            "maximum_propagation_depth",
            "maximum_inference_depth",
            "maximum_blocking_depth",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name}={value!r} must be >= 0")
        for name in ("minimum_failures_to_block", "minimum_corroborations"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name}={getattr(self, name)!r} must be >= 1")

        # Cross-field rules pydantic cannot express, each of which produces a config that
        # is individually in range and jointly meaningless.
        if self.minimum_inferred_weight > self.maximum_inferred_weight:
            raise ValueError(
                f"minimum_inferred_weight={self.minimum_inferred_weight} exceeds "
                f"maximum_inferred_weight={self.maximum_inferred_weight}: no inference can pass"
            )
        if self.strong_failure_threshold > self.strong_success_threshold:
            raise ValueError(
                f"strong_failure_threshold={self.strong_failure_threshold} exceeds "
                f"strong_success_threshold={self.strong_success_threshold}: one score would "
                "be both a strong success and a strong failure"
            )

        if self.allowed_inference_modalities is not None:
            unknown = {normalise_modality(m) for m in self.allowed_inference_modalities}
            unknown -= KNOWN_INFERENCE_MODALITIES
            if unknown:
                raise ValueError(
                    f"unknown inference modalities {sorted(unknown)}; "
                    f"known: {sorted(KNOWN_INFERENCE_MODALITIES)}"
                )
        if self.accepted_validation_statuses is not None and "refuted" in {
            s.strip().lower() for s in self.accepted_validation_statuses
        }:
            raise ValueError(
                "'refuted' cannot be an accepted validation status: it is the one verdict "
                "that means the edge was tested and found wrong"
            )

    # --- resolved views ---------------------------------------------------------------
    @property
    def inference_depth(self) -> int:
        """How many prerequisite hops an inference may travel."""
        if self.maximum_inference_depth is None:
            return self.maximum_propagation_depth
        return self.maximum_inference_depth

    @property
    def blocking_depth(self) -> int:
        """How many hops a block may travel. Separate from `inference_depth` by design."""
        if self.maximum_blocking_depth is None:
            return self.maximum_propagation_depth
        return self.maximum_blocking_depth

    @property
    def resolved_modalities(self) -> frozenset[str]:
        """The modality allowlist actually in force.

        The explicit allowlist wins outright when given. Otherwise it is derived from the
        three legacy booleans, which is what makes this change invisible to a deployment
        that has not adopted the new setting.
        """
        if self.allowed_inference_modalities is not None:
            return frozenset(
                normalise_modality(m) for m in self.allowed_inference_modalities
            )

        allowed: set[str] = set()
        if self.allow_mcq_single_hit_upward_inference:
            allowed.add("mcq")
        if self.allow_code_upward_inference:
            allowed.add("code")
        if self.allow_voice_upward_inference:
            # The legacy flag governed voice and open together; deriving both preserves
            # that exactly. Only an explicit allowlist can separate them.
            allowed.update({"voice", "open"})
        return frozenset(allowed)

    def source_multiplier(self, modality: str) -> float:
        """Attenuation for an inference drawn from this modality. Unknown -> 1.0.

        Failing open here is safe because `upward_inference_allowed` has already refused
        an unknown modality outright; this only ever scales something already permitted.
        """
        return float(self.source_multipliers.get(normalise_modality(modality), 1.0))

    def upward_inference_allowed(self, modality: str) -> bool:
        """Whether a direct hit in this modality may imply prerequisite mastery.

        Fails closed on an unknown modality. Whether ONE hit is enough is now a separate
        question, asked by `minimum_corroborations` — this decides only whether the
        modality may contribute at all.
        """
        return normalise_modality(modality) in self.resolved_modalities

    def independence_key(self, signal: InferredNodeSignal) -> str:
        """What this signal counts AS, for the corroboration requirement.

        Two signals sharing a key are one observation. See `CorroborationIndependence`
        for why the default keys on the source node.
        """
        if self.corroboration_independence is CorroborationIndependence.EVIDENCE:
            return f"evidence:{signal.source_evidence_id}"
        if self.corroboration_independence is CorroborationIndependence.ITEM:
            return f"item:{signal.source_item_id or signal.source_evidence_id}"
        if (
            self.corroboration_independence
            is CorroborationIndependence.SOURCE_NODE_MODALITY
        ):
            return f"node+modality:{signal.source_node}:{normalise_modality(signal.modality)}"
        return f"node:{signal.source_node}"

    def as_manifest(self) -> dict:
        """Every factor, at the value actually in force. INV-P8 reads this.

        Includes the resolved properties as well as the raw fields, because an operator
        reading a manifest wants to know what RAN — and `maximum_inference_depth: null`
        does not tell them that inference travelled four hops.
        """
        record: dict = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if isinstance(value, Mapping):
                value = {str(k): v for k, v in sorted(value.items())}
            elif isinstance(value, frozenset):
                value = sorted(value)
            elif isinstance(value, StrEnum):
                value = str(value)
            elif isinstance(value, tuple):
                value = list(value)
            record[f.name] = value

        record["resolved"] = {
            "inference_depth": self.inference_depth,
            "blocking_depth": self.blocking_depth,
            "modalities": sorted(self.resolved_modalities),
        }
        return record


def propagation_config_from_settings(
    *, minimum_failures_to_block: int | None = None
) -> PropagationConfig:
    """The only place a PropagationConfig is built from configuration.

    `minimum_failures_to_block` lets the resolved bank policy tighten the deployment's
    value. It may only tighten: `resolve_policy` takes the max of the two, so a bank that
    demands three consistent failures gets three even on a deployment configured for two,
    and a bank that asks for one on a deployment configured for two still gets two.
    """
    return PropagationConfig(
        strong_success_threshold=settings.graph_strong_success_threshold,
        strong_failure_threshold=settings.graph_strong_failure_threshold,
        minimum_propagation_confidence=settings.graph_minimum_propagation_confidence,
        upward_decay=settings.graph_upward_decay,
        minimum_inferred_weight=settings.graph_minimum_inferred_weight,
        maximum_inferred_weight=settings.graph_maximum_inferred_weight,
        downward_block_confidence=settings.graph_downward_block_confidence,
        minimum_failures_to_block=(
            settings.graph_minimum_failures_to_block
            if minimum_failures_to_block is None
            else max(
                minimum_failures_to_block, settings.graph_minimum_failures_to_block
            )
        ),
        maximum_propagation_depth=settings.graph_maximum_propagation_depth,
        maximum_inference_depth=settings.graph_maximum_inference_depth,
        maximum_blocking_depth=settings.graph_maximum_blocking_depth,
        minimum_corroborations=settings.graph_minimum_corroborations,
        corroboration_independence=CorroborationIndependence(
            settings.graph_corroboration_independence.strip().lower()
        ),
        allowed_inference_modalities=settings.inference_modalities(),
        accepted_validation_statuses=settings.accepted_validation_statuses(),
        allow_mcq_single_hit_upward_inference=settings.graph_allow_mcq_single_hit_inference,
        allow_code_upward_inference=settings.graph_allow_code_upward_inference,
        allow_voice_upward_inference=settings.graph_allow_voice_upward_inference,
        preview_unvalidated_edges=edge_preview_enabled(),
        # The deployment switches, carried in the config rather than read again downstream.
        # `propagation.py` decides whether to WRITE a conclusion from these; `graph_delta`
        # no longer decides separately whether to READ it, which is how the two came to
        # disagree inside one report.
        enforce_inference=upward_inference_enabled(),
        enforce_blocking=descendant_blocking_enabled(),
    )


def graph_enabled() -> bool:
    """The master kill switch. Every graph entry point checks it first."""
    return settings.competency_graph_enabled


def shadow_mode_enabled() -> bool:
    """Whether the audit mirrors are computed and persisted.

    This was declared, documented as live, shown in the operator UI, and read by nothing —
    the exact defect `graph_enabled` above was introduced to fix, reintroduced. The mirror
    sets were populated unconditionally, so an operator turning shadow mode off saw no
    change.

    Narrowed to one real meaning: it governs the MIRROR, not enforcement. It deliberately
    does not force `enforce_inference` off, because it defaults True — an operator setting
    GRAPH_UPWARD_INFERENCE_ENABLED=true would then see nothing happen until they found and
    cleared a second flag, which is the trap this module exists to prevent.
    """
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
