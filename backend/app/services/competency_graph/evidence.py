from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceEvent:
    """One uniquely identifiable evidence event for the graph layer.

    The deterministic propagation layer uses this as the root identity so that:
    - one response criterion becomes one evidence root
    - inferred descendants share provenance via evidence_id variants
    - duplicate evidence is rejected by EvidenceLedger
    """

    evidence_id: str
    session_id: str
    item_id: str
    modality: str

    target_node: str
    score: float
    weight: float
    confidence: float

    source: str
    evidence_kind: str = "direct"

    # Provenance
    source_node: str | None = None
    propagation_distance: int = 0
    directly_tested: bool = True

    rubric_criterion_id: str | None = None
    evaluator_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

