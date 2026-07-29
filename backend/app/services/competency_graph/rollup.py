from __future__ import annotations

from collections import deque
from typing import Any

from app.services.orchestrator.competency import rollup_outcomes as main_rollup_outcomes

from .graph import CompetencyGraphService
from .models import CompetencyStatus
from .propagation import PropagationConfig


def _prerequisite_edge_strength_index(graph: CompetencyGraphService) -> dict[tuple[str, str], float]:
    index: dict[tuple[str, str], float] = {}
    for e in graph.graph.edges:
        if e.relation == "PREREQUISITE":
            index[(e.from_id, e.to_id)] = float(e.strength)
    return index


def infer_upward_inferred_nodes(
    *,
    graph: CompetencyGraphService,
    target_node: str,
    direct_effective: float,
    confidence: float,
    score: float,
    config: PropagationConfig,
) -> dict[str, tuple[int, float]]:
    """Infer prerequisite ancestors with (distance, inferred_weight).

    Returns only nodes that pass the minimum inferred weight threshold.
    """
    strength_index = _prerequisite_edge_strength_index(graph)

    # BFS over prerequisite parents while accumulating edge-strength products.
    # We keep the best (largest product strength) per node within max_depth.
    best_strength_product: dict[str, float] = {target_node: 1.0}
    best_distance: dict[str, int] = {target_node: 0}

    queue: deque[tuple[str, int, float]] = deque([(target_node, 0, 1.0)])

    while queue:
        cur, depth, strength_product = queue.popleft()
        if depth >= config.maximum_propagation_depth:
            continue
        for parent in graph.prerequisites_parents(cur):
            edge_strength = strength_index.get((parent, cur))
            if edge_strength is None:
                continue
            next_strength = strength_product * float(edge_strength)
            next_depth = depth + 1
            if parent not in best_strength_product or next_strength > best_strength_product[parent]:
                best_strength_product[parent] = next_strength
                best_distance[parent] = next_depth
                queue.append((parent, next_depth, next_strength))

    inferred: dict[str, tuple[int, float]] = {}
    for ancestor, strength_product in best_strength_product.items():
        if ancestor == target_node:
            continue
        distance = best_distance.get(ancestor, 0)
        inferred_weight = direct_effective * float(strength_product) * (config.upward_decay ** distance)
        inferred_weight = min(inferred_weight, config.maximum_inferred_weight)
        if inferred_weight < config.minimum_inferred_weight:
            continue
        inferred[ancestor] = (distance, inferred_weight)
    return inferred


def graph_inferred_rollup_raw_outcomes(
    raw_outcomes: list[dict[str, Any]],
    *,
    graph: CompetencyGraphService,
    config: PropagationConfig,
) -> list[dict[str, Any]]:
    """Create inferred raw outcomes for prerequisite nodes.

    These raw outcomes are compatible with `app.services.orchestrator.competency.rollup_outcomes`.
    """
    inferred_raws: list[dict[str, Any]] = []

    for raw in raw_outcomes:
        variable = str(raw.get("variable") or "")
        score = float(raw.get("score", 0.0))
        weight = float(raw.get("weight", 0.0))
        confidence = float(raw.get("confidence", 0.0))
        if not variable or weight <= 0.0 or confidence <= 0.0:
            continue

        strong_success = (
            score >= config.strong_success_threshold
            and confidence >= config.minimum_propagation_confidence
        )
        if not strong_success:
            continue

        direct_effective = weight * confidence
        inferred = infer_upward_inferred_nodes(
            graph=graph,
            target_node=variable,
            direct_effective=direct_effective,
            confidence=confidence,
            score=score,
            config=config,
        )

        for anc, (_distance, inferred_weight) in inferred.items():
            inferred_raws.append(
                {
                    "variable": anc,
                    "score": score,
                    "weight": inferred_weight,
                    "confidence": confidence,
                    "source_item_id": raw.get("source_item_id", ""),
                    "modality": raw.get("modality", ""),
                }
            )

    return inferred_raws


def graph_rollup_outcomes(
    raw_outcomes: list[dict[str, Any]],
    *,
    session_variables: set[str],
    graph: CompetencyGraphService,
    config: PropagationConfig,
) -> list:
    """Roll up graph-inferred node outcomes into main competencies."""
    inferred_raws = graph_inferred_rollup_raw_outcomes(raw_outcomes, graph=graph, config=config)
    merged = [*raw_outcomes, *inferred_raws]
    return main_rollup_outcomes(merged, session_variables)

