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


@dataclass
class CompetencyGraphState:
    """Runtime mutable graph state during one assessment session."""

    # Node runtime state keyed by node id.
    nodes: dict[str, CompetencyNodeState] = field(default_factory=dict)

    def ensure_nodes(self, node_ids: set[str]) -> None:
        for nid in node_ids:
            if nid not in self.nodes:
                self.nodes[nid] = CompetencyNodeState(competency_id=nid)

