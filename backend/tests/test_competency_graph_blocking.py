"""Descendant blocking: the branch nothing has ever exercised.

Strong failure on a prerequisite BLOCKS its dependent descendants — it does not mark them
failed (spec section 17.5). That branch used to be dead: it read a `prerequisite_edges`
field declared on `CompetencyGraph` and populated by nothing, so it always returned the
empty set. The existing propagation tests could not catch it, because the only assertion
on `blocked_nodes` was for a NON-strong score, which passes either way.

Every assertion here is written against the intended semantics. They were introduced as
`xfail(strict=True)` against the pre-fix code and un-marked once the index moved onto
`GraphIndexes`, where the one place that builds indexes cannot forget to populate it.

The DA graph, for reference:

    DA.1 -> DA.3, DA.4, DA.5
    DA.2 -> DA.3, DA.4
    DA.3 -> DA.4, DA.6
    DA.4 -> DA.5, DA.6
"""

from __future__ import annotations

import pytest

from app.services.competency_graph import load_default_competency_graph
from app.services.competency_graph.evidence import EvidenceEvent
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.models import (
    CompetencyEdge,
    CompetencyGraph,
    CompetencyNode,
)
from app.services.competency_graph.config import PropagationConfig
from app.services.competency_graph.propagation import shadow_apply_events


def _failure_event(*, target: str, modality: str = "code") -> EvidenceEvent:
    """A strong failure: score at the floor, confidence above the blocking threshold."""
    return EvidenceEvent(
        evidence_id=f"session:item:{modality}:{target}",
        session_id="session",
        item_id="item",
        modality=modality,
        target_node=target,
        score=0.0,
        weight=1.0,
        confidence=1.0,
        source="item",
    )


def _graph_with_edges(*edges: CompetencyEdge, node_ids: set[str]) -> CompetencyGraphService:
    nodes = {
        nid: CompetencyNode(competency_id=nid, title=nid, node_type="sub_competency")
        for nid in node_ids
    }
    return CompetencyGraphService(
        CompetencyGraph(schema_version="test", nodes=nodes, edges=edges)
    )


def _prereq(from_id: str, to_id: str, **kw) -> CompetencyEdge:
    return CompetencyEdge(from_id=from_id, to_id=to_id, relation="PREREQUISITE", **kw)


def test_strong_failure_blocks_every_dependent_descendant() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    [update] = shadow_apply_events(
        graph, [_failure_event(target="DA.1")], config=PropagationConfig()
    )

    assert update.blocked_nodes == frozenset({"DA.3", "DA.4", "DA.5", "DA.6"})


def test_blocked_descendants_are_not_marked_failed() -> None:
    """Spec section 17.5: 'blocked' and 'not mastered' are different claims.

    Passes today and must keep passing once blocking is real — that is the point.
    """
    graph = CompetencyGraphService(load_default_competency_graph())
    [update] = shadow_apply_events(
        graph, [_failure_event(target="DA.1")], config=PropagationConfig()
    )

    # Only the directly tested node carries a failure verdict.
    assert {e.target_node for e in update.direct_updates} == {"DA.1"}
    assert update.inferred_updates == ()


def test_an_edge_that_forbids_blocking_does_not_block_through_itself() -> None:
    """DA.1 -/-> DA.3, but DA.6 is still blocked because DA.4 reaches it."""
    graph = _graph_with_edges(
        _prereq("DA.1", "DA.3", allow_downward_blocking=False),
        _prereq("DA.1", "DA.4"),
        _prereq("DA.4", "DA.6"),
        node_ids={"DA.1", "DA.3", "DA.4", "DA.6"},
    )

    blocked = graph.blockable_descendants("DA.1", max_depth=4)

    assert "DA.3" not in blocked
    assert blocked == {"DA.4", "DA.6"}


def test_maximum_propagation_depth_bounds_the_blocked_frontier() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())

    at_depth_1 = graph.blockable_descendants("DA.1", max_depth=1)
    at_depth_4 = graph.blockable_descendants("DA.1", max_depth=4)

    assert at_depth_1 == {"DA.3", "DA.4", "DA.5"}
    assert at_depth_4 == {"DA.3", "DA.4", "DA.5", "DA.6"}


def test_depth_is_the_shortest_path_not_the_first_path_found() -> None:
    """A -> B -> D -> F, and A -> C -> E -> D.

    D sits at depth 2 (via B) and at depth 3 (via C -> E). Depth-first pops the C branch
    first, records D at 3, and then refuses to re-expand it from B — so F, whose true
    depth is 3, falls outside a max_depth of 3 and is silently missed.
    """
    graph = _graph_with_edges(
        _prereq("A", "B"),
        _prereq("A", "C"),
        _prereq("C", "E"),
        _prereq("E", "D"),
        _prereq("B", "D"),
        _prereq("D", "F"),
        node_ids={"A", "B", "C", "D", "E", "F"},
    )

    assert graph.blockable_descendants("A", max_depth=3) == {"B", "C", "D", "E", "F"}


def test_a_weak_failure_does_not_block() -> None:
    """Confidence below `downward_block_confidence` must not hard-block. Passes today."""
    graph = CompetencyGraphService(load_default_competency_graph())
    event = EvidenceEvent(
        evidence_id="session:item:code:DA.1",
        session_id="session",
        item_id="item",
        modality="code",
        target_node="DA.1",
        score=0.0,
        weight=1.0,
        confidence=0.5,
        source="item",
    )

    [update] = shadow_apply_events(graph, [event], config=PropagationConfig())

    assert update.blocked_nodes == frozenset()
