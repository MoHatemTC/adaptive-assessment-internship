"""Apply one evidence event to the graph.

ONE ALGORITHM, TWO PROJECTIONS

"Shadow" means the RESULT is recorded but not enforced. It never meant a different
algorithm, and when it did the two disagreed: the enforced path inferred from a
multiple-choice hit that shadow mode explicitly refused, and blocked descendants through
edges shadow mode would not have blocked through. There is one function; the caller
decides which consumers read its output.

WHAT THIS NEVER DOES

It does not touch the CAT posterior. Inferred consequences come back as
`InferredNodeSignal`, which carries no score and no weight and therefore cannot be turned
into a graded outcome. See `inference.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .config import PropagationConfig
from .evidence import EvidenceEvent
from .graph import CompetencyGraphService
from .inference import InferredNodeSignal, infer_ancestors
from .ledger import EvidenceLedger
from .models import CompetencyStatus
from .prerequisite_rules import is_scorable, is_strong_failure, is_strong_success
from .state import CompetencyGraphState

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GraphUpdateResult:
    """What one evidence event did to the graph."""

    direct_updates: tuple[EvidenceEvent, ...] = ()
    inferred_signals: tuple[InferredNodeSignal, ...] = ()
    blocked_nodes: frozenset[str] = frozenset()
    reopened_nodes: frozenset[str] = frozenset()
    contradicted_nodes: frozenset[str] = frozenset()

    # WHAT THE GRAPH CONCLUDED, as distinct from what it wrote.
    #
    # `inferred_signals` is every ancestor the traversal reached — one per hop, before any
    # corroboration or enforcement test. It is the raw traversal output and it always was.
    #
    # `corroborated_nodes` is the subset whose support reached K on this event, computed
    # whether or not the deployment enforces inference. `blocked_nodes` above is now
    # enforcement-gated, so `shadow_blocked_nodes` carries the same conclusion for a
    # deployment that is only watching. These two are what an audit mirror reads, and
    # keeping them separate from the enforced sets is what stops a report claiming an
    # inference the deployment did not make.
    corroborated_nodes: frozenset[str] = frozenset()
    pending_corroboration_nodes: frozenset[str] = frozenset()
    # The subset of `corroborated_nodes` actually written to node status. Empty whenever
    # the deployment does not enforce inference — which is the only difference between the
    # two, and the one a consumer must not have to re-derive.
    inferred_mastered_nodes: frozenset[str] = frozenset()
    shadow_blocked_nodes: frozenset[str] = frozenset()

    # SCORE-affecting nodes only. Drives main-competency rollup, so a node whose status
    # changed without its score moving must not appear here (review C17): a blocked
    # descendant is a reason to re-pick, not a reason to recompute an estimate.
    changed_nodes: frozenset[str] = frozenset()
    affected_mains: frozenset[str] = frozenset()

    # Nodes whose ELIGIBILITY changed — blocked, reopened, contradicted. Drives queue
    # invalidation only.
    status_changed_nodes: frozenset[str] = frozenset()
    selection_affected_mains: frozenset[str] = frozenset()

    # REPORTING ONLY. What the same event would have concluded had the unvalidated
    # PREREQUISITE edges been trusted. No node state was written for these, no main is
    # affected by them, and no consumer may act on them — see `_preview` below.
    preview_inferred_signals: tuple[InferredNodeSignal, ...] = ()
    preview_blocked_nodes: frozenset[str] = frozenset()

    unscorable: bool = False
    audit: dict = field(default_factory=dict)

    @property
    def inferred_updates(self) -> tuple[InferredNodeSignal, ...]:
        """Retained name for readers that predate the signal/outcome split."""
        return self.inferred_signals


def apply_direct_evidence(
    graph: CompetencyGraphService,
    graph_state: CompetencyGraphState,
    event: EvidenceEvent,
    *,
    ledger: EvidenceLedger,
    config: PropagationConfig,
    now: str | None = None,
    item_minimum_confidence: float | None = None,
) -> GraphUpdateResult:
    """Apply one direct evidence event. Idempotent through `ledger`.

    `now` is passed in rather than read from the clock so a replay produces the same
    record it produced the first time.
    """
    ledger.assert_new(event.evidence_id)
    graph_state.ensure_nodes({event.target_node})

    node_state = graph_state.nodes[event.target_node]
    prior_status = node_state.status
    prior_blockers = frozenset(node_state.blocked_by)

    weight = float(event.weight)
    confidence = float(event.confidence)
    score = float(event.score)

    if not is_scorable(weight, confidence):
        # An unscorable response is not a wrong answer. It changes nothing, and it must
        # not report a changed node: doing so invalidates queue slots and triggers a
        # rollup for a main where no score moved.
        ledger.commit(event.evidence_id)
        return GraphUpdateResult(
            direct_updates=(event,),
            unscorable=True,
            audit={"event": event.evidence_id, "outcome": "unscorable"},
        )

    direct_effective = weight * confidence
    strong_success = is_strong_success(
        score, confidence, config=config, item_minimum_confidence=item_minimum_confidence
    )
    strong_failure = is_strong_failure(
        score, confidence, config=config, item_minimum_confidence=item_minimum_confidence
    )

    inferred: list[InferredNodeSignal] = []
    blocked: set[str] = set()
    reopened: set[str] = set()
    # Ancestors whose support reached K on this event, and those still short of it. Both
    # are computed whatever the deployment switch says; `enforce_inference` decides only
    # whether the corroborated ones are written into node status.
    corroborated: set[str] = set()
    pending: set[str] = set()
    inferred_mastered: set[str] = set()
    shadow_blocked: set[str] = set()

    node_state.direct_observations += 1
    node_state.status_confidence = confidence
    node_state.last_direct_update_at = now
    node_state.direct_evidence_ids.add(event.evidence_id)

    if strong_success:
        node_state.status = CompetencyStatus.DIRECT_MASTERED
        node_state.mastery = max(node_state.mastery, score)
        node_state.uncertainty = min(node_state.uncertainty, 1.0 - confidence)

        # Direct evidence overrides a block. A node reached this state because a
        # prerequisite failed; passing it directly says the block was wrong, and the node
        # must become eligible again or the contradiction can never be resolved.
        if prior_status is CompetencyStatus.BLOCKED:
            reopened.add(event.target_node)
            node_state.blocked_by.clear()

        inferred = infer_ancestors(
            graph,
            target_node=event.target_node,
            direct_effective=direct_effective,
            modality=event.modality,
            source_evidence_id=event.evidence_id,
            source_item_id=event.item_id,
            config=config,
        )
        for signal in inferred:
            graph_state.ensure_nodes({signal.node})
            ancestor = graph_state.nodes[signal.node]

            # RECORDING SUPPORT IS NOT DRAWING A CONCLUSION. The provenance goes in
            # unconditionally — before the status guard, before the corroboration count,
            # before enforcement — because it is the evidence on which all three are
            # later judged. Recording it only when it changed something is how the audit
            # mirror ends up empty for exactly the deployments that need to read it.
            ancestor.record_inferred_support(
                config.independence_key(signal),
                {
                    "source_node": signal.source_node,
                    "source_item_id": signal.source_item_id,
                    "evidence_id": signal.source_evidence_id,
                    "modality": signal.modality,
                    "distance": signal.distance,
                    "strength": signal.strength,
                    "edge_path": list(signal.edge_path),
                    "at": now,
                },
            )
            ancestor.inferred_evidence_ids.add(signal.source_evidence_id)
            ancestor.last_inferred_update_at = now

            # Inferred mastery never overwrites a verdict the graph already holds for a
            # stronger reason. BLOCKED and CONTRADICTED belong in this guard alongside the
            # two direct verdicts: a blocked node is one whose prerequisite failed, and
            # silently promoting it to INFERRED_MASTERED both loses the block and hides
            # the disagreement that produced it — the opposite of the explicit reopen path
            # a direct success takes above.
            if ancestor.status in (
                CompetencyStatus.DIRECT_MASTERED,
                CompetencyStatus.DIRECT_NOT_MASTERED,
                CompetencyStatus.BLOCKED,
                CompetencyStatus.CONTRADICTED,
            ):
                continue

            if len(ancestor.inferred_support) < config.minimum_corroborations:
                pending.add(signal.node)
                continue
            corroborated.add(signal.node)
            if config.enforce_inference:
                ancestor.status = CompetencyStatus.INFERRED_MASTERED
                inferred_mastered.add(signal.node)

    elif strong_failure:
        node_state.status = CompetencyStatus.DIRECT_NOT_MASTERED
        node_state.mastery = min(node_state.mastery, score)
        node_state.uncertainty = min(node_state.uncertainty, 1.0 - confidence)
        node_state.strong_failure_observations += 1

        # HOW MANY FAILURES BUY A BLOCK. One observation propagates its own error rate
        # straight into the block, and the block is the strongest claim the graph makes:
        # it denies a candidate the chance to demonstrate a skill. Measured on a graph
        # whose edges were correct by construction, blocking from a single failure put the
        # false-blocking rate at 4.8-5.1% against a 2% gate — which is the per-observation
        # error rate, not an edge-quality problem. Requiring k consistent failures squares
        # it: at q = 0.05-0.10, k = 2 lands at 0.25-1.0%, inside the gate, without
        # touching a single edge.
        #
        # Asymmetric with inference on purpose, and in the same direction as the
        # confidence floors: inferring mastery a candidate lacks costs one unnecessary
        # question, blocking a skill they have costs them the assessment.
        if node_state.strong_failure_observations < config.minimum_failures_to_block:
            blocked_pending = graph.blockable_descendants(
                event.target_node, max_depth=config.blocking_depth
            )
            if blocked_pending:
                logger.debug(
                    "%s failed once — holding %d descendant block(s) until a second failure",
                    event.target_node,
                    len(blocked_pending),
                )
            descendants: set[str] = set()
        else:
            descendants = graph.blockable_descendants(
                event.target_node, max_depth=config.blocking_depth
            )

        for descendant in descendants:
            graph_state.ensure_nodes({descendant})
            state = graph_state.nodes[descendant]
            # A node already demonstrated directly is not blocked by a later failure
            # upstream: it is evidence AGAINST the edge, not a reason to hide the node.
            if state.status is CompetencyStatus.DIRECT_MASTERED:
                continue
            # Concluded either way; written only under enforcement. Same rule as
            # inference above, and for the same reason: the mirror has to keep recording
            # what a deployment WOULD block, or there is no evidence on which to decide
            # whether to let it.
            shadow_blocked.add(descendant)
            if not config.enforce_blocking:
                continue
            blocked.add(descendant)
            state.status = CompetencyStatus.BLOCKED
            state.blocked_by.add(event.target_node)

    # The same two conclusions, drawn again with the per-edge validation gates waived and
    # written NOWHERE. A bank whose edges all ship inert otherwise reports zero inferred
    # and zero blocked nodes for every session it will ever run, which is exactly what a
    # broken inference engine reports — and leaves no evidence on which to judge an edge
    # short of enabling it on live candidates.
    preview_inferred: tuple[InferredNodeSignal, ...] = ()
    preview_blocked: frozenset[str] = frozenset()
    if config.preview_unvalidated_edges:
        if strong_success:
            preview_inferred = tuple(
                infer_ancestors(
                    graph,
                    target_node=event.target_node,
                    direct_effective=direct_effective,
                    modality=event.modality,
                    source_evidence_id=event.evidence_id,
                    source_item_id=event.item_id,
                    config=config,
                    ignore_edge_validation=True,
                )
            )
        elif strong_failure:
            preview_blocked = frozenset(
                descendant
                for descendant in graph.blockable_descendants(
                    event.target_node,
                    max_depth=config.blocking_depth,
                    ignore_edge_validation=True,
                )
                # Mirrors the enforced rule: a node already demonstrated directly is
                # evidence against the edge, not something the edge gets to hide.
                if graph_state.nodes.get(descendant) is None
                or graph_state.nodes[descendant].status
                is not CompetencyStatus.DIRECT_MASTERED
            )

    ledger.commit(event.evidence_id)

    from .conflicts import detect_contradictions

    contradictions = detect_contradictions(
        prior_status=prior_status,
        prior_blocked_by=prior_blockers,
        event=event,
        strong_success=strong_success,
        strong_failure=strong_failure,
        graph_state=graph_state,
        now=now,
    )
    contradicted = frozenset(c.node for c in contradictions)
    for contradiction in contradictions:
        graph_state.ensure_nodes({contradiction.node})
        graph_state.nodes[contradiction.node].contradictions.append(contradiction.as_dict())

    changed = frozenset({event.target_node})
    status_changed = frozenset(blocked | reopened | contradicted) - changed

    return GraphUpdateResult(
        direct_updates=(event,),
        inferred_signals=tuple(inferred),
        blocked_nodes=frozenset(blocked),
        corroborated_nodes=frozenset(corroborated),
        pending_corroboration_nodes=frozenset(pending),
        inferred_mastered_nodes=frozenset(inferred_mastered),
        shadow_blocked_nodes=frozenset(shadow_blocked),
        reopened_nodes=frozenset(reopened),
        contradicted_nodes=contradicted,
        changed_nodes=changed,
        affected_mains=frozenset(graph.mains_for_nodes(changed)),
        status_changed_nodes=status_changed,
        selection_affected_mains=frozenset(graph.mains_for_nodes(status_changed)),
        preview_inferred_signals=preview_inferred,
        preview_blocked_nodes=preview_blocked,
        audit={
            "event": event.evidence_id,
            "target": event.target_node,
            "status": str(graph_state.nodes[event.target_node].status),
            "inferred": [s.node for s in inferred],
            "corroborated": sorted(corroborated),
            "pending_corroboration": sorted(pending),
            "blocked": sorted(blocked),
            "shadow_blocked": sorted(shadow_blocked),
            "reopened": sorted(reopened),
            "contradicted": sorted(contradicted),
            "preview_inferred": [s.node for s in preview_inferred],
            "preview_blocked": sorted(preview_blocked),
        },
    )


def apply_events(
    graph: CompetencyGraphService,
    events: list[EvidenceEvent],
    *,
    config: PropagationConfig,
    graph_state: CompetencyGraphState | None = None,
    ledger: EvidenceLedger | None = None,
    now: str | None = None,
) -> tuple[list[GraphUpdateResult], CompetencyGraphState, EvidenceLedger]:
    """Apply several events, returning the state and ledger so a caller can persist them.

    The state used to be built and discarded here, which meant node status, evidence
    provenance and contradiction history were computed and then thrown away every
    response, and the ledger deduplicated only within a single item.
    """
    if graph_state is None:
        graph_state = CompetencyGraphState()
        graph_state.ensure_nodes(set(graph.graph.nodes))
    if ledger is None:
        ledger = EvidenceLedger()

    updates = [
        apply_direct_evidence(
            graph, graph_state, event, ledger=ledger, config=config, now=now
        )
        for event in events
    ]
    return updates, graph_state, ledger


# Retained names. The old ones said "shadow", which described how the caller used the
# result rather than what the function did — and invited the divergence this module exists
# to remove.
shadow_apply_direct_event = apply_direct_evidence


def shadow_apply_events(
    graph: CompetencyGraphService,
    events: list[EvidenceEvent],
    *,
    config: PropagationConfig,
) -> list[GraphUpdateResult]:
    updates, _state, _ledger = apply_events(graph, events, config=config)
    return updates
