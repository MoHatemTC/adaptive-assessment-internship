from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# What a grader outcome means when it says nothing. One default, in one place: the two
# call sites used to disagree — one read a missing confidence as 1.0 and propagated at
# full strength, the other read it as 0.0 and skipped the node entirely. Same dictionary,
# opposite treatment.
DEFAULT_CONFIDENCE = 1.0


def evidence_id_for(
    *,
    session_id: str,
    item_id: str,
    attempt_no: int,
    modality: str,
    target_node: str,
    rubric_criterion_id: str | None = None,
) -> str:
    """The idempotency key for one piece of direct evidence.

    `attempt_no` distinguishes re-administrations of the same item. Re-administration
    cannot happen today — the bank shortlist excludes served items — so this is
    future-proofing rather than a live fix, and saying so beats implying otherwise.

    `rubric_criterion_id` exists because two criteria on one item can score the same
    competency in the same modality; without it their ids collide and the second is
    silently dropped as a duplicate.
    """
    criterion = rubric_criterion_id or "-"
    return f"{session_id}:{item_id}:{attempt_no}:{modality}:{target_node}:{criterion}"


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

