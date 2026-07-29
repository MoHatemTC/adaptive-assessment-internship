from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .evidence import EvidenceEvent
from .ledger import EvidenceLedger
from .models import CompetencyStatus
from .state import CompetencyGraphState
from .graph import CompetencyGraphService


@dataclass(frozen=True)
class PropagationConfig:
    """Propagation thresholds and policies.

    Phase A only needs the scaffolding; later phases tune the defaults and behavior.
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


@dataclass(frozen=True)
class GraphUpdateResult:
    """Result of applying one root evidence event.

    Phase A scaffolds the model; Phase B+ will fill the logic.
    """

    direct_updates: tuple[EvidenceEvent, ...] = ()
    inferred_updates: tuple[EvidenceEvent, ...] = ()
    blocked_nodes: frozenset[str] = frozenset()
    reopened_nodes: frozenset[str] = frozenset()
    contradicted_nodes: frozenset[str] = frozenset()
    changed_nodes: frozenset[str] = frozenset()
    affected_mains: frozenset[str] = frozenset()


def _path_strength_product(
    graph: CompetencyGraphService, *, start: str, end: str, max_depth: int
) -> tuple[int, float] | None:
    """Compute (distance, strength_product) along the strongest prerequisite path.

    This is phase scaffolding: it returns the best strength product among all paths up to
    max_depth. In later phases this can be replaced by a precomputed all-pairs index.
    """
    best: dict[str, float] = {start: 1.0}
    best_dist: dict[str, int] = {start: 0}
    # DFS stack: (node, strength_product, depth)
    stack: list[tuple[str, float, int]] = [(start, 1.0, 0)]
    while stack:
        cur, strength_so_far, depth = stack.pop()
        if depth >= max_depth:
            continue
        # Upward inference walks from an advanced node to its prerequisite parents.
        for parent in graph.prerequisites_parents(cur):
            edge = next(
                (
                    e
                    for e in graph.graph.edges
                    if e.relation == "PREREQUISITE"
                    and e.from_id == parent
                    and e.to_id == cur
                ),
                None,
            )
            if edge is None:
                continue
            if not edge.allow_upward_inference:
                continue
            next_strength = strength_so_far * float(edge.strength)
            if parent not in best or next_strength > best[parent]:
                best[parent] = next_strength
                best_dist[parent] = depth + 1
            stack.append((parent, next_strength, depth + 1))

    if end not in best:
        return None
    return best_dist[end], best[end]


def _upward_inference_allowed(modality: str, config: PropagationConfig) -> bool:
    """Whether one direct hit in this modality may imply prerequisite mastery."""
    normalized = (modality or "").strip().lower()
    if normalized == "mcq":
        return config.allow_mcq_single_hit_upward_inference
    if normalized == "code":
        return config.allow_code_upward_inference
    if normalized in {"open", "voice", "audio"}:
        return config.allow_voice_upward_inference
    return False


def _blockable_descendants(
    graph: CompetencyGraphService,
    start: str,
    *,
    max_depth: int,
) -> set[str]:
    """Descendants reachable only through edges that permit downward blocking."""
    blocked: set[str] = set()
    stack: list[tuple[str, int]] = [(start, 0)]
    while stack:
        current, depth = stack.pop()
        if depth >= max_depth:
            continue
        for edge in graph.graph.prerequisite_edges:
            if (
                edge.from_id != current
                or not edge.allow_downward_blocking
            ):
                continue
            if edge.to_id not in blocked:
                blocked.add(edge.to_id)
                stack.append((edge.to_id, depth + 1))
    return blocked


def shadow_apply_direct_event(
    graph: CompetencyGraphService,
    graph_state: CompetencyGraphState,
    event: EvidenceEvent,
    *,
    ledger: EvidenceLedger,
    config: PropagationConfig,
) -> GraphUpdateResult:
    """Apply one evidence event in shadow mode.

    Shadow mode is *analysis-only*: it never affects CAT posterior math.
    """
    ledger.assert_new(event.evidence_id)
    graph_state.ensure_nodes({event.target_node})

    node_state = graph_state.nodes[event.target_node]
    weight = float(event.weight)
    confidence = float(event.confidence)
    score = float(event.score)

    if weight <= 0.0 or confidence <= 0.0:
        # Unscorable/infrastructure failed: no updates or propagation.
        ledger.commit(event.evidence_id)
        return GraphUpdateResult(
            direct_updates=(event,),
            inferred_updates=(),
            blocked_nodes=frozenset(),
            reopened_nodes=frozenset(),
            contradicted_nodes=frozenset(),
            changed_nodes=frozenset({event.target_node}),
            affected_mains=frozenset(graph.mains_for_node(event.target_node)),
        )

    direct_effective = weight * confidence

    strong_success = (
        score >= config.strong_success_threshold and confidence >= config.minimum_propagation_confidence
    )
    strong_failure = (
        score <= config.strong_failure_threshold and confidence >= config.downward_block_confidence
    )

    direct_updates: list[EvidenceEvent] = [event]
    inferred_updates: list[EvidenceEvent] = []
    blocked_nodes: set[str] = set()

    if strong_success:
        node_state.status = CompetencyStatus.DIRECT_MASTERED
        node_state.direct_observations += 1
        node_state.mastery = max(node_state.mastery, float(score))
        node_state.uncertainty = min(node_state.uncertainty, 1.0 - float(confidence))

        if _upward_inference_allowed(event.modality, config):
            # Upward inference through strict prerequisite ancestors.
            for anc in graph.ancestors(event.target_node, include_self=False):
                path = _path_strength_product(
                    graph,
                    start=event.target_node,
                    end=anc,
                    max_depth=config.maximum_propagation_depth,
                )
                if path is None:
                    continue
                distance, path_strength = path
                inferred_weight = (
                    direct_effective
                    * float(path_strength)
                    * (config.upward_decay ** distance)
                )
                inferred_weight = min(inferred_weight, config.maximum_inferred_weight)
                if inferred_weight < config.minimum_inferred_weight:
                    continue
                inferred_id = f"{event.evidence_id}::inferred::{anc}"
                inferred_updates.append(
                    EvidenceEvent(
                        evidence_id=inferred_id,
                        session_id=event.session_id,
                        item_id=event.item_id,
                        modality=event.modality,
                        target_node=anc,
                        score=event.score,
                        weight=float(inferred_weight),
                        confidence=event.confidence,
                        source=event.source,
                        evidence_kind="inferred_upward",
                        source_node=event.target_node,
                        propagation_distance=distance,
                        directly_tested=False,
                        rubric_criterion_id=event.rubric_criterion_id,
                        evaluator_version=event.evaluator_version,
                        metadata={"shadow": True},
                    )
                )
                graph_state.ensure_nodes({anc})
                graph_state.nodes[anc].status = CompetencyStatus.INFERRED_MASTERED
                graph_state.nodes[anc].inferred_observations += 1

    elif strong_failure:
        node_state.status = CompetencyStatus.DIRECT_NOT_MASTERED
        node_state.direct_observations += 1
        node_state.mastery = min(node_state.mastery, float(score))
        node_state.uncertainty = min(node_state.uncertainty, 1.0 - float(confidence))

        # Descendant blocking: mark dependent descendants as BLOCKED.
        for desc in _blockable_descendants(
            graph,
            event.target_node,
            max_depth=config.maximum_propagation_depth,
        ):
            blocked_nodes.add(desc)
            graph_state.ensure_nodes({desc})
            graph_state.nodes[desc].status = CompetencyStatus.BLOCKED
            graph_state.nodes[desc].blocked_by.add(event.target_node)

    # Commit ledger after computing.
    ledger.commit(event.evidence_id)

    changed_nodes = {event.target_node, *blocked_nodes, *(e.target_node for e in inferred_updates)}
    affected_mains: set[str] = set()
    for nid in changed_nodes:
        for m in graph.mains_for_node(nid):
            affected_mains.add(m)

    return GraphUpdateResult(
        direct_updates=tuple(direct_updates),
        inferred_updates=tuple(inferred_updates),
        blocked_nodes=frozenset(blocked_nodes),
        reopened_nodes=frozenset(),
        contradicted_nodes=frozenset(),
        changed_nodes=frozenset(changed_nodes),
        affected_mains=frozenset(affected_mains),
    )


def shadow_apply_events(
    graph: CompetencyGraphService,
    events: list[EvidenceEvent],
    *,
    config: PropagationConfig,
) -> list[GraphUpdateResult]:
    """Apply many evidence events and return update results for logging."""
    graph_state = CompetencyGraphState()
    ledger = EvidenceLedger()
    updates: list[GraphUpdateResult] = []
    # Ensure all nodes exist for cleaner state transitions later.
    graph_state.ensure_nodes(set(graph.graph.nodes.keys()))
    for e in events:
        try:
            updates.append(
                shadow_apply_direct_event(
                    graph,
                    graph_state,
                    e,
                    ledger=ledger,
                    config=config,
                )
            )
        except Exception:
            # Shadow mode should never break CAT; loggers in caller can report if desired.
            raise
    return updates


