"""Why every competency is in the state it is in.

The review's "reporting first": build this before anything the graph decides is enforced.
It has zero runtime effect and it is the cheapest way to surface a bad prerequisite edge —
an assessor reading "inferred from C6.16, two hops, strength 0.31" can say "that inference
is wrong" long before any metric accumulates enough sessions to say so.

Populated whenever a graph loads, regardless of which enforcement flags are set. A report
that only appears once a feature is enabled cannot be used to decide whether to enable it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .graph import CompetencyGraphService
from .models import CompetencyStatus
from .state import CompetencyGraphState

# Which statuses count as a direct measurement for reporting purposes. Inferred mastery is
# deliberately absent: the whole point of the split is that a deduction is not an
# observation.
_DIRECT = {CompetencyStatus.DIRECT_MASTERED, CompetencyStatus.DIRECT_NOT_MASTERED}


@dataclass(frozen=True)
class NodeReport:
    """One sub-competency, and how it reached its state."""

    competency_id: str
    title: str
    status: str
    mastery: float
    status_confidence: float
    direct_observations: int
    inferred_observations: int
    direct_evidence_ids: list[str]
    inferred_evidence_ids: list[str]
    blocked_by: list[str]
    contradictions: list[dict]
    affected_mains: list[str]
    critical: bool

    def as_dict(self) -> dict:
        return {
            "competency_id": self.competency_id,
            "title": self.title,
            "status": self.status,
            "mastery": round(self.mastery, 4),
            "status_confidence": round(self.status_confidence, 4),
            "direct_observations": self.direct_observations,
            "inferred_observations": self.inferred_observations,
            "direct_evidence_ids": self.direct_evidence_ids,
            "inferred_evidence_ids": self.inferred_evidence_ids,
            "blocked_by": self.blocked_by,
            "contradictions": self.contradictions,
            "affected_mains": self.affected_mains,
            "critical": self.critical,
        }


@dataclass(frozen=True)
class MainGraphReport:
    """What the graph knows about one main competency."""

    competency_id: str
    nodes_directly_measured: int
    nodes_inferred: int
    nodes_blocked: int
    unresolved_contradictions: int
    critical_nodes_resolved: bool
    coverage_satisfied: bool
    unmeasured_nodes: list[str] = field(default_factory=list)
    # What the unvalidated edges would have concluded, and how much of it this session
    # disproved. Reporting only; see `AssessmentState.graph_preview_inferred_nodes`.
    nodes_preview_inferred: int = 0
    nodes_preview_blocked: int = 0
    preview_refuted: int = 0

    def as_dict(self) -> dict:
        return {
            "competency_id": self.competency_id,
            # Named for what they are. `observations` on the CAT side counts exactly one
            # thing — main-level outcomes folded into a posterior — and these node counts
            # must never be mistaken for it.
            "nodes_directly_measured": self.nodes_directly_measured,
            "nodes_inferred": self.nodes_inferred,
            "nodes_blocked": self.nodes_blocked,
            "unresolved_contradictions": self.unresolved_contradictions,
            "critical_nodes_resolved": self.critical_nodes_resolved,
            "coverage_satisfied": self.coverage_satisfied,
            "unmeasured_nodes": self.unmeasured_nodes,
            "nodes_preview_inferred": self.nodes_preview_inferred,
            "nodes_preview_blocked": self.nodes_preview_blocked,
            "preview_refuted": self.preview_refuted,
        }


def build_node_reports(
    graph: CompetencyGraphService, graph_state: CompetencyGraphState
) -> list[NodeReport]:
    """Every node the session touched, in id order. Untouched nodes are omitted."""
    reports: list[NodeReport] = []
    for node_id, node_state in sorted(graph_state.nodes.items()):
        # A node the session never touched is not news. A node with a PARTIAL score is:
        # its status is still UNKNOWN — neither strong success nor strong failure — but it
        # was directly measured, which is what the coverage gate counts and what an
        # assessor needs to see.
        touched = (
            node_state.status is not CompetencyStatus.UNKNOWN
            or node_state.direct_observations
            or node_state.inferred_observations
            or node_state.contradictions
        )
        if not touched:
            continue
        node = graph.graph.nodes.get(node_id)
        reports.append(
            NodeReport(
                competency_id=node_id,
                title=node.title if node else node_id,
                status=str(node_state.status),
                mastery=node_state.mastery,
                status_confidence=node_state.status_confidence,
                direct_observations=node_state.direct_observations,
                inferred_observations=node_state.inferred_observations,
                direct_evidence_ids=sorted(node_state.direct_evidence_ids),
                inferred_evidence_ids=sorted(node_state.inferred_evidence_ids),
                blocked_by=sorted(node_state.blocked_by),
                contradictions=list(node_state.contradictions),
                affected_mains=list(graph.mains_for_node(node_id)),
                critical=bool(node.critical) if node else False,
            )
        )
    return reports


def build_main_report(
    graph: CompetencyGraphService,
    graph_state: CompetencyGraphState,
    main_id: str,
    *,
    unmeasured_nodes: list[str],
    preview_inferred: dict[str, dict] | None = None,
    preview_blocked: set[str] | None = None,
) -> MainGraphReport:
    """The graph's summary for one main competency.

    `preview_*` live on the session rather than on node state — deliberately, because a
    forecast that wrote itself into node state would be indistinguishable from a
    conclusion the moment anyone read the state back.
    """
    from .coverage import sub_nodes_for_main

    subs = sub_nodes_for_main(graph, main_id, critical_only=False)
    critical = set(graph.critical_nodes_for_main(main_id))

    directly_measured = {
        nid
        for nid in subs
        if graph_state.nodes.get(nid) and graph_state.nodes[nid].status in _DIRECT
    }
    inferred = {
        nid
        for nid in subs
        if graph_state.nodes.get(nid)
        and graph_state.nodes[nid].status is CompetencyStatus.INFERRED_MASTERED
    }
    blocked = {
        nid
        for nid in subs
        if graph_state.nodes.get(nid)
        and graph_state.nodes[nid].status is CompetencyStatus.BLOCKED
    }
    contradictions = sum(
        len(graph_state.nodes[nid].contradictions)
        for nid in subs
        if nid in graph_state.nodes
    )

    forecast = set(preview_inferred or {})
    not_mastered = {
        nid
        for nid in subs
        if graph_state.nodes.get(nid)
        and graph_state.nodes[nid].status is CompetencyStatus.DIRECT_NOT_MASTERED
    }

    return MainGraphReport(
        competency_id=main_id,
        nodes_directly_measured=len(directly_measured),
        nodes_inferred=len(inferred),
        nodes_blocked=len(blocked),
        unresolved_contradictions=contradictions,
        # Critical nodes must be measured DIRECTLY. An inference standing in for one is
        # precisely the substitution the coverage gate exists to prevent.
        critical_nodes_resolved=critical.issubset(directly_measured)
        if critical
        else True,
        coverage_satisfied=not unmeasured_nodes,
        unmeasured_nodes=sorted(unmeasured_nodes),
        nodes_preview_inferred=len(forecast & set(subs)),
        nodes_preview_blocked=len((preview_blocked or set()) & set(subs)),
        preview_refuted=len(forecast & not_mastered),
    )
