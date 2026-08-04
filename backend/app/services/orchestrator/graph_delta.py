"""Everything one response did to the graph, held until all of it succeeded.

The transaction boundary the review's C14 asks for. A response can produce several
evidence events; applying them one at a time straight into session state means a failure
partway through leaves the session in a state no sequence of responses could have
produced — and, because the evidence id was marked processed first, one that cannot be
retried.

Also the place the two projections are computed. The algorithm is the same for both:
`enforced_*` is what eligibility, coverage and queueing read; `shadow_*` is the audit
mirror. What differs is which flags let a consumer act on it, never what was computed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.orchestration import AssessmentState
from app.services.competency_graph import config as graph_config
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.ledger import EvidenceLedger
from app.services.competency_graph.propagation import GraphUpdateResult
from app.services.competency_graph.state import CompetencyGraphState


@dataclass
class GraphDelta:
    """Session graph state, plus what this response changed about it."""

    node_states: dict[str, dict] = field(default_factory=dict)
    processed_evidence_ids: list[str] = field(default_factory=list)

    measured: set[str] = field(default_factory=set)
    mastered: set[str] = field(default_factory=set)
    not_mastered: set[str] = field(default_factory=set)
    inferred_mastered: set[str] = field(default_factory=set)
    # The audit mirror for inference, matching the one blocking already had. Enforcement
    # is a deployment decision; the record of what WOULD have been inferred is not, and
    # collapsing the two left `graph_upward_inference_enabled` enforced nowhere.
    shadow_inferred_mastered: set[str] = field(default_factory=set)
    # Enforced blocking is flag-gated; the audit set records what WOULD be blocked so a
    # reviewer can see the consequence before anyone enables it.
    blocked: set[str] = field(default_factory=set)
    shadow_blocked: set[str] = field(default_factory=set)
    contradicted: set[str] = field(default_factory=set)
    contradictions: list[dict] = field(default_factory=list)

    # The unvalidated-edge forecast: node -> where the belief came from. Recorded so a
    # session says what the edges WOULD have concluded; read by reporting and by the edge
    # validation script, and by nothing that decides anything.
    preview_inferred: dict[str, dict] = field(default_factory=dict)
    preview_blocked: set[str] = field(default_factory=set)

    # Mains whose ELIGIBILITY changed — a blocked or reopened node makes a queued pick
    # stale without any estimate having moved (review C17).
    selection_affected_mains: set[str] = field(default_factory=set)

    @classmethod
    def restore(cls, state: AssessmentState) -> "GraphDelta":
        """The delta a response starts from: whatever the session already knew."""
        return cls(
            node_states=dict(state.graph_node_states),
            processed_evidence_ids=list(state.graph_processed_evidence_ids),
            measured=set(state.graph_direct_measured_nodes),
            mastered=set(state.graph_direct_mastered_nodes),
            not_mastered=set(state.graph_direct_not_mastered_nodes),
            inferred_mastered=set(state.graph_inferred_mastered_nodes),
            shadow_inferred_mastered=set(state.graph_shadow_inferred_mastered_nodes),
            blocked=set(state.graph_blocked_nodes),
            shadow_blocked=set(state.graph_shadow_blocked_nodes),
            contradicted=set(state.graph_contradicted_nodes),
            contradictions=list(state.graph_contradictions),
            preview_inferred=dict(state.graph_preview_inferred_nodes),
            preview_blocked=set(state.graph_preview_blocked_nodes),
        )

    def absorb(
        self,
        *,
        graph: CompetencyGraphService,
        graph_state: CompetencyGraphState,
        ledger: EvidenceLedger,
        results: list[GraphUpdateResult],
        session_variables: set[str],
    ) -> None:
        """Fold a completed batch in. Called only after every event applied cleanly."""
        self.node_states = graph_state.to_dict()
        self.processed_evidence_ids = ledger.processed_ids()

        blocking_enforced = graph_config.descendant_blocking_enabled()
        # Symmetrical with blocking, and new. `graph_upward_inference_enabled` was declared
        # in settings, documented as the switch for upward inference, and read by nothing:
        # inference was gated only by the per-edge flag, so an operator turning the feature
        # off saw no change. Enforced here rather than baked into the edge set, so the
        # mirror below still records what the graph would have concluded.
        inference_enforced = graph_config.upward_inference_enabled()

        for result in results:
            if result.unscorable:
                # No node changed, so no queue slot is stale and no main is affected. An
                # audio failure is not evidence about the candidate.
                continue

            for event in result.direct_updates:
                if event.target_node in graph.graph.nodes:
                    self.measured.add(event.target_node)

            node_status = {
                nid: graph_state.nodes[nid].status
                for nid in result.changed_nodes
                if nid in graph_state.nodes
            }
            for nid, status in node_status.items():
                if status.value == "direct_mastered":
                    self.mastered.add(nid)
                    self.not_mastered.discard(nid)
                elif status.value == "direct_not_mastered":
                    self.not_mastered.add(nid)
                    self.mastered.discard(nid)

            # Inference is recorded separately from direct mastery, whatever the flags
            # say. Merging the two is what let a deduction satisfy the coverage gate.
            self.shadow_inferred_mastered.update(s.node for s in result.inferred_signals)
            self.shadow_inferred_mastered -= self.mastered | self.not_mastered
            if inference_enforced:
                self.inferred_mastered.update(s.node for s in result.inferred_signals)
            self.inferred_mastered -= self.mastered | self.not_mastered

            self.shadow_blocked.update(result.blocked_nodes)
            if blocking_enforced:
                self.blocked.update(result.blocked_nodes)
            # Direct evidence reopens a block, and a node demonstrated directly is not
            # blocked by anything. Without this the block is permanent and contradiction
            # recovery — which needs the node served again — can never fire.
            self.blocked -= result.reopened_nodes | self.mastered
            self.shadow_blocked -= result.reopened_nodes | self.mastered

            self.contradicted.update(result.contradicted_nodes)
            self.contradicted -= result.reopened_nodes
            for nid in result.contradicted_nodes:
                node = graph_state.nodes.get(nid)
                if node is not None and node.contradictions:
                    latest = node.contradictions[-1]
                    if latest not in self.contradictions:
                        self.contradictions.append(latest)

            # The unvalidated-edge forecast. Kept in its own fields, never merged into the
            # sets above: the moment a preview is unioned into `inferred_mastered` it
            # starts satisfying coverage, which is the exact substitution the direct /
            # inferred split exists to prevent.
            for signal in result.preview_inferred_signals:
                known = self.preview_inferred.get(signal.node)
                if known is not None and float(known.get("strength", 0.0)) >= signal.strength:
                    continue
                self.preview_inferred[signal.node] = {
                    "source_node": signal.source_node,
                    "distance": signal.distance,
                    "strength": signal.strength,
                    "modality": signal.modality,
                    "evidence_id": signal.source_evidence_id,
                }
            # NOT reduced by what was later mastered. Enforcement would have reopened
            # those nodes; the record keeps them, because "this edge would have hidden a
            # competency the candidate demonstrably has" is the single most useful thing a
            # session can say about an unvalidated edge — and hard blocking is what makes
            # it unsayable in production.
            self.preview_blocked |= set(result.preview_blocked_nodes)

            if graph_config.filtering_enabled() or graph_config.utility_enabled():
                self.selection_affected_mains |= result.selection_affected_mains

        self.selection_affected_mains &= session_variables

    def preview_refutations(self) -> list[dict]:
        """Preview beliefs the session went on to measure and disprove.

        The only in-band evidence that an authored edge is wrong, in both directions:

            wrong_inference   the graph would have called a node mastered, and — had the
                              edge been live — stopped asking about it. It was asked, and
                              it failed.
            false_block       the graph would have declared a node unreachable. It was
                              reached.

        Ordering does not matter. The direct verdict and the forecast are both cumulative,
        and a session normally measures a prerequisite root long before the dependent
        skill that would have implied it, so a check that only looked forward from the
        inference would miss most of what it exists to catch.
        """
        refutations = [
            {"kind": "wrong_inference", "node": node, **self.preview_inferred[node]}
            for node in sorted(self.preview_inferred.keys() & self.not_mastered)
        ]
        refutations.extend(
            {"kind": "false_block", "node": node}
            for node in sorted(self.preview_blocked & self.mastered)
        )
        return refutations

    def as_state_update(self) -> dict:
        """The persisted projection of this delta."""
        return {
            "graph_node_states": self.node_states,
            "graph_processed_evidence_ids": self.processed_evidence_ids,
            "graph_direct_measured_nodes": sorted(self.measured),
            "graph_direct_mastered_nodes": sorted(self.mastered),
            "graph_direct_not_mastered_nodes": sorted(self.not_mastered),
            "graph_inferred_mastered_nodes": sorted(self.inferred_mastered),
            "graph_blocked_nodes": sorted(self.blocked),
            "graph_contradicted_nodes": sorted(self.contradicted),
            "graph_contradictions": self.contradictions,
            # The audit mirror. Same computation; recorded, never enforced.
            "graph_shadow_direct_mastered_nodes": sorted(self.mastered),
            "graph_shadow_inferred_mastered_nodes": sorted(self.shadow_inferred_mastered),
            "graph_shadow_direct_not_mastered_nodes": sorted(self.not_mastered),
            "graph_shadow_blocked_nodes": sorted(self.shadow_blocked),
            "graph_shadow_contradicted_nodes": sorted(self.contradicted),
            "graph_last_affected_mains": sorted(self.selection_affected_mains),
            # The unvalidated-edge forecast, and the direct evidence that contradicts it.
            "graph_preview_inferred_nodes": dict(sorted(self.preview_inferred.items())),
            "graph_preview_blocked_nodes": sorted(self.preview_blocked),
            "graph_preview_refuted_nodes": self.preview_refutations(),
        }
