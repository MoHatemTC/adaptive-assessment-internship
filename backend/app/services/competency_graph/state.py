from __future__ import annotations

from dataclasses import dataclass, field

from .models import CompetencyStatus


def _support_from_dict(raw: dict) -> dict[str, dict]:
    """Read `inferred_support`, reconstructing it for sessions persisted before it existed.

    A session in flight across the deploy that introduced corroboration has
    `inferred_evidence_ids` and no support map. Those ids record THAT an inference
    happened, never from which source node — so the independence key cannot be recovered
    and the true count is unknowable.

    Reconstruct them as a single collective observation rather than one each. That is the
    conservative direction: it can only delay an inference to an ancestor, never license
    one that the configured K would have refused. Marked `legacy` so a manifest reader can
    see the count is a floor rather than a measurement.
    """
    support = raw.get("inferred_support")
    if isinstance(support, dict) and support:
        return {str(k): dict(v) for k, v in support.items()}

    legacy_ids = sorted(raw.get("inferred_evidence_ids") or [])
    if not legacy_ids:
        return {}
    return {
        "legacy:pre-corroboration": {
            "source_node": "",
            "source_item_id": "",
            "evidence_id": legacy_ids[0],
            "modality": "",
            "distance": None,
            "strength": 0.0,
            "edge_path": [],
            "at": raw.get("last_inferred_update_at"),
            "legacy": True,
            "legacy_evidence_ids": legacy_ids,
        }
    }


@dataclass
class CompetencyNodeState:
    competency_id: str

    mastery: float = 0.0
    uncertainty: float = 1.0

    direct_observations: int = 0
    inferred_observations: int = 0
    # How many DIRECT observations of this node were strong failures. Blocking reads it,
    # because a block decided from one observation propagates that observation's full
    # error rate into the block: at a per-observation error of 5-10% — which is what the
    # measured 4.8-5.1% false-blocking rate implies — one failure blocks wrongly 5-10% of
    # the time, and two consistent failures square that to 0.25-1.0%.
    strong_failure_observations: int = 0

    status: CompetencyStatus = CompetencyStatus.UNKNOWN
    status_confidence: float = 0.0

    direct_evidence_ids: set[str] = field(default_factory=set)
    inferred_evidence_ids: set[str] = field(default_factory=set)

    # WHAT SUPPORTS AN INFERENCE, keyed by whatever the configured independence rule says
    # makes two observations two. One entry per independent observation; the value is the
    # strongest provenance record seen for that key.
    #
    # This is deliberately one structure serving two jobs, because they are the same fact.
    # `len(inferred_support)` is the corroboration count the K requirement reads, and each
    # value carries the distance, source node and edge path that a depth- or edge-
    # stratified safety analysis needs. Storing them apart would let the count and the
    # evidence for it drift.
    #
    # Record shape: {source_node, source_item_id, evidence_id, modality, distance,
    #                strength, edge_path: list[str], at}
    inferred_support: dict[str, dict] = field(default_factory=dict)

    blocked_by: set[str] = field(default_factory=set)
    contradictions: list[dict] = field(default_factory=list)

    last_direct_update_at: str | None = None
    last_inferred_update_at: str | None = None

    def record_inferred_support(self, key: str, record: dict) -> None:
        """Add or strengthen one independent observation supporting this node.

        Keeps the STRONGEST record per key rather than the first or the last, so a second
        look at the same source through a better path improves the provenance without
        inflating the count — which is the invariant the whole corroboration rule rests on.
        """
        existing = self.inferred_support.get(key)
        if existing is None or float(record.get("strength", 0.0)) > float(
            existing.get("strength", 0.0)
        ):
            self.inferred_support[key] = record
        self.inferred_observations = len(self.inferred_support)

    def to_dict(self) -> dict:
        return {
            "mastery": self.mastery,
            "uncertainty": self.uncertainty,
            "direct_observations": self.direct_observations,
            "inferred_observations": self.inferred_observations,
            "strong_failure_observations": self.strong_failure_observations,
            "status": str(self.status),
            "status_confidence": self.status_confidence,
            "direct_evidence_ids": sorted(self.direct_evidence_ids),
            "inferred_evidence_ids": sorted(self.inferred_evidence_ids),
            "inferred_support": {
                k: dict(v) for k, v in sorted(self.inferred_support.items())
            },
            "blocked_by": sorted(self.blocked_by),
            "contradictions": list(self.contradictions),
            "last_direct_update_at": self.last_direct_update_at,
            "last_inferred_update_at": self.last_inferred_update_at,
        }

    @classmethod
    def from_dict(cls, competency_id: str, raw: dict) -> CompetencyNodeState:
        return cls(
            competency_id=competency_id,
            mastery=float(raw.get("mastery", 0.0)),
            uncertainty=float(raw.get("uncertainty", 1.0)),
            direct_observations=int(raw.get("direct_observations", 0)),
            inferred_observations=int(raw.get("inferred_observations", 0)),
            strong_failure_observations=int(raw.get("strong_failure_observations", 0)),
            status=CompetencyStatus(raw.get("status", CompetencyStatus.UNKNOWN)),
            status_confidence=float(raw.get("status_confidence", 0.0)),
            direct_evidence_ids=set(raw.get("direct_evidence_ids") or []),
            inferred_evidence_ids=set(raw.get("inferred_evidence_ids") or []),
            inferred_support=_support_from_dict(raw),
            blocked_by=set(raw.get("blocked_by") or []),
            contradictions=list(raw.get("contradictions") or []),
            last_direct_update_at=raw.get("last_direct_update_at"),
            last_inferred_update_at=raw.get("last_inferred_update_at"),
        )


@dataclass
class CompetencyGraphState:
    """Graph state for one assessment session.

    Persisted with the session. It used to be built inside the propagation entry point and
    discarded on return, so node status, evidence provenance and contradiction history
    were computed fresh every response and then thrown away — which is why the report
    could only ever show flat sets of node ids and never how a node reached its state.
    """

    # Node runtime state keyed by node id.
    nodes: dict[str, CompetencyNodeState] = field(default_factory=dict)

    def ensure_nodes(self, node_ids: set[str]) -> None:
        for nid in node_ids:
            if nid not in self.nodes:
                self.nodes[nid] = CompetencyNodeState(competency_id=nid)

    def to_dict(self) -> dict[str, dict]:
        return {nid: node.to_dict() for nid, node in sorted(self.nodes.items())}

    @classmethod
    def from_dict(cls, raw: dict[str, dict] | None) -> CompetencyGraphState:
        state = cls()
        for nid, node in (raw or {}).items():
            state.nodes[nid] = CompetencyNodeState.from_dict(nid, node)
        return state

    def nodes_with_status(self, *statuses: CompetencyStatus) -> set[str]:
        wanted = set(statuses)
        return {nid for nid, node in self.nodes.items() if node.status in wanted}
