from __future__ import annotations

from dataclasses import dataclass, field

from .models import CompetencyStatus


@dataclass
class CompetencyNodeState:
    competency_id: str

    mastery: float = 0.0
    uncertainty: float = 1.0

    direct_observations: int = 0
    inferred_observations: int = 0

    status: CompetencyStatus = CompetencyStatus.UNKNOWN
    status_confidence: float = 0.0

    direct_evidence_ids: set[str] = field(default_factory=set)
    inferred_evidence_ids: set[str] = field(default_factory=set)

    blocked_by: set[str] = field(default_factory=set)
    contradictions: list[dict] = field(default_factory=list)

    last_direct_update_at: str | None = None
    last_inferred_update_at: str | None = None


    def to_dict(self) -> dict:
        return {
            "mastery": self.mastery,
            "uncertainty": self.uncertainty,
            "direct_observations": self.direct_observations,
            "inferred_observations": self.inferred_observations,
            "status": str(self.status),
            "status_confidence": self.status_confidence,
            "direct_evidence_ids": sorted(self.direct_evidence_ids),
            "inferred_evidence_ids": sorted(self.inferred_evidence_ids),
            "blocked_by": sorted(self.blocked_by),
            "contradictions": list(self.contradictions),
            "last_direct_update_at": self.last_direct_update_at,
            "last_inferred_update_at": self.last_inferred_update_at,
        }

    @classmethod
    def from_dict(cls, competency_id: str, raw: dict) -> "CompetencyNodeState":
        return cls(
            competency_id=competency_id,
            mastery=float(raw.get("mastery", 0.0)),
            uncertainty=float(raw.get("uncertainty", 1.0)),
            direct_observations=int(raw.get("direct_observations", 0)),
            inferred_observations=int(raw.get("inferred_observations", 0)),
            status=CompetencyStatus(raw.get("status", CompetencyStatus.UNKNOWN)),
            status_confidence=float(raw.get("status_confidence", 0.0)),
            direct_evidence_ids=set(raw.get("direct_evidence_ids") or []),
            inferred_evidence_ids=set(raw.get("inferred_evidence_ids") or []),
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
    def from_dict(cls, raw: dict[str, dict] | None) -> "CompetencyGraphState":
        state = cls()
        for nid, node in (raw or {}).items():
            state.nodes[nid] = CompetencyNodeState.from_dict(nid, node)
        return state

    def nodes_with_status(self, *statuses: CompetencyStatus) -> set[str]:
        wanted = set(statuses)
        return {nid for nid, node in self.nodes.items() if node.status in wanted}

