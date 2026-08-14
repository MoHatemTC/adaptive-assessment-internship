"""Runtime graph behavior: classify direct evidence, derive what it implies.

The graph's structure lives in the immutable definition; what happened at each node
lives in ``AdaptiveState.graph_evidence`` — one authoritative record per node, holding
only what the posterior cannot reproduce: how often direct evidence was strong enough to
classify as a clear success or a clear failure.

Everything else is DERIVED at decision time from that record — which nodes are failing,
which competencies are blocked. Nothing is stored twice, so nothing can disagree.

Graph inference never manufactures evidence: a strong failure on a prerequisite steers
selection away from its dependents, but only a directly observed response ever moves a
competency's posterior. Blocking is a selection preference, not a verdict — a blocked
competency can still be measured, and measuring it is exactly how a wrong block is
disproved.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from adaptive_engine.compilation import CompiledAssessment
from adaptive_engine.evidence import Evidence
from adaptive_engine.state import GraphNodeEvidence

# Classification thresholds, ported from the previous engine's prerequisite rules: a
# response only classifies when the grader stands behind it, and only clear outcomes
# count. A score in the middle is real evidence for the posterior but says nothing
# categorical about the node.
STRONG_SUCCESS_SCORE = 0.8
STRONG_FAILURE_SCORE = 0.2
CLASSIFICATION_CONFIDENCE_FLOOR = 0.8

# One clear failure can be a slip; two is a pattern worth steering around.
FAILURES_TO_BLOCK = 2


def record_graph_evidence(
    existing: Mapping[str, GraphNodeEvidence], evidence: Iterable[Evidence]
) -> dict[str, GraphNodeEvidence]:
    """Fold newly graded evidence into the per-node record. Returns a new dict."""
    updated = dict(existing)
    for piece in evidence:
        if piece.confidence < CLASSIFICATION_CONFIDENCE_FLOOR:
            continue
        node = updated.get(piece.competency_id, GraphNodeEvidence())
        if piece.score >= STRONG_SUCCESS_SCORE:
            node = GraphNodeEvidence(
                strong_successes=node.strong_successes + 1,
                strong_failures=node.strong_failures,
            )
        elif piece.score <= STRONG_FAILURE_SCORE:
            node = GraphNodeEvidence(
                strong_successes=node.strong_successes,
                strong_failures=node.strong_failures + 1,
            )
        else:
            continue
        updated[piece.competency_id] = node
    return updated


def failing_competencies(
    graph_evidence: Mapping[str, GraphNodeEvidence],
) -> frozenset[str]:
    """Nodes whose direct evidence shows a clear pattern of failure.

    Requires ``FAILURES_TO_BLOCK`` strong failures and more failures than successes, so
    a node that recovered stops failing without any separate reopening bookkeeping —
    the derivation self-corrects because there is only one record to derive from.
    """
    return frozenset(
        node_id
        for node_id, node in graph_evidence.items()
        if node.strong_failures >= FAILURES_TO_BLOCK
        and node.strong_failures > node.strong_successes
    )


def blocked_competencies(
    compiled: CompiledAssessment,
    graph_evidence: Mapping[str, GraphNodeEvidence],
) -> frozenset[str]:
    """Competencies with a failing prerequisite anywhere in their ancestry.

    Blocking DEPRIORITIZES a competency in selection; it never excludes it and never
    affects convergence. If nothing unblocked remains, a blocked competency is simply
    measured next — the engine prefers investigating prerequisites first, it does not
    refuse to look.
    """
    failing = failing_competencies(graph_evidence)
    if not failing:
        return frozenset()
    return frozenset(
        competency_id
        for competency_id, ancestors in compiled.ancestors_by_competency.items()
        if ancestors & failing
    )
