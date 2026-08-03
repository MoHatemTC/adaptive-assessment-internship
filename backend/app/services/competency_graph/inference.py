"""What the graph concludes about a node it did not directly test.

An inferred signal is DELIBERATELY NOT a graded outcome. It has no `score` and no
`weight`, so it cannot be handed to the CAT update even by accident — `GradedOutcome`
would reject the keyword arguments.

That is the shape of the rule the review calls A1: in this version the graph shapes
SELECTION and REPORTING and never the posterior. Only a directly observed response
multiplies a likelihood.

WHY THE BOUNDARY IS STRUCTURAL AND NOT A FLAG

Propagated evidence is a deduction FROM a response that is already in the likelihood.
Multiplying it in again counts one response twice — measured at 1.80x weight inflation on
the specification's own worked example, saturating the `min(1, ·)` cap on denser graphs.
The damage is not mainly to the ability estimate; it is to the standard error, which is
what the assessment stops on. A flag would leave four uncalibrated constants wired into a
path that terminates at SE. Removing the path removes the failure mode.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .config import PropagationConfig
from .graph import CompetencyGraphService


@dataclass(frozen=True)
class InferredNodeSignal:
    """One ancestor the graph believes is supported by a direct success elsewhere.

    Consumed by selection (deprioritise what we already believe) and by reporting (say
    which nodes were inferred, from what, and how far away). Never by measurement.
    """

    node: str
    source_node: str
    distance: int
    strength: float
    source_evidence_id: str
    modality: str


def infer_ancestors(
    graph: CompetencyGraphService,
    *,
    target_node: str,
    direct_effective: float,
    modality: str,
    source_evidence_id: str,
    config: PropagationConfig,
    ignore_edge_validation: bool = False,
) -> list[InferredNodeSignal]:
    """Prerequisite ancestors supported by a strong success on `target_node`.

    Breadth-first over parents, keeping the strongest strength product per ancestor.
    Strength decays with distance and is bounded from both sides: below the floor the
    signal is too weak to act on, above the ceiling it would rival direct evidence.

    Honours `allow_upward_inference` per edge. An edge shipped inert must be inert here —
    that is the whole basis on which unvalidated edges are allowed into the graph at all.

    `ignore_edge_validation` waives THAT gate and nothing else, for the reporting-only
    preview: the modality rule, the decay, the weight bounds and the depth limit all still
    apply. Waiving only the validation status is what makes the preview a forecast of
    enabling an edge rather than a different calculation that happens to look similar —
    turn an edge on and enforcement reproduces the preview exactly.
    """
    if not config.upward_inference_allowed(modality):
        return []

    multiplier = config.source_multiplier(modality)
    best: dict[str, tuple[int, float]] = {}
    queue: deque[tuple[str, int, float]] = deque([(target_node, 0, 1.0)])

    while queue:
        node, depth, strength_so_far = queue.popleft()
        if depth >= config.maximum_propagation_depth:
            continue
        for edge in graph.prerequisite_edges_into(node):
            if not (edge.allow_upward_inference or ignore_edge_validation):
                continue
            parent = edge.from_id
            strength = strength_so_far * float(edge.strength)
            known = best.get(parent)
            if known is not None and known[1] >= strength:
                continue
            best[parent] = (depth + 1, strength)
            queue.append((parent, depth + 1, strength))

    best.pop(target_node, None)

    signals: list[InferredNodeSignal] = []
    for node, (distance, path_strength) in sorted(best.items()):
        weight = (
            direct_effective
            * path_strength
            * multiplier
            * (config.upward_decay**distance)
        )
        weight = min(weight, config.maximum_inferred_weight)
        if weight < config.minimum_inferred_weight:
            continue
        signals.append(
            InferredNodeSignal(
                node=node,
                source_node=target_node,
                distance=distance,
                strength=round(weight, 6),
                source_evidence_id=source_evidence_id,
                modality=modality,
            )
        )
    return signals
