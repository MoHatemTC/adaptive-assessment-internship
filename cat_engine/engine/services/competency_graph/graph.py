from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable
from dataclasses import dataclass

from .models import CompetencyEdge, CompetencyGraph


@dataclass(frozen=True)
class GraphIndexes:
    """Everything derived from the edge list, computed once.

    Derived structure belongs here and not on `CompetencyGraph`. It used to carry a
    `prerequisite_edges` tuple that the loader never populated, so every consumer of it
    silently saw an empty graph — and because it lived on the data model, any code that
    constructed a `CompetencyGraph` directly reintroduced the same emptiness. An index
    built by the one place that builds indexes cannot be forgotten.
    """

    parents_by_node: dict[str, tuple[str, ...]]
    children_by_node: dict[str, tuple[str, ...]]
    mains_by_node: dict[str, tuple[str, ...]]
    critical_nodes_by_main: dict[str, tuple[str, ...]]
    # PREREQUISITE edges, by endpoint and by pair. The pair index replaces a linear scan
    # of every edge that ran once per parent per step of the propagation walk.
    prerequisite_edges: tuple[CompetencyEdge, ...]
    prerequisite_edges_by_from: dict[str, tuple[CompetencyEdge, ...]]
    prerequisite_edges_by_to: dict[str, tuple[CompetencyEdge, ...]]
    edge_by_pair: dict[tuple[str, str], CompetencyEdge]
    # Children reachable through edges that PERMIT blocking. Separate from
    # `children_by_node` because an edge may model a real dependency while being
    # untrusted to enforce one — which is exactly how an unvalidated edge ships.
    blockable_children_by_node: dict[str, tuple[str, ...]]


def _as_tuple(xs: Iterable[str]) -> tuple[str, ...]:
    return tuple(xs)


class CompetencyGraphService:
    """Immutable graph with precomputed traversal helpers."""

    def __init__(self, graph: CompetencyGraph) -> None:
        self.graph = graph
        self.indexes = self._build_indexes(graph)

    @staticmethod
    def _build_indexes(graph: CompetencyGraph) -> GraphIndexes:
        parents: dict[str, list[str]] = defaultdict(list)
        children: dict[str, list[str]] = defaultdict(list)
        blockable: dict[str, list[str]] = defaultdict(list)
        mains_by_node: dict[str, tuple[str, ...]] = {}
        critical_nodes_by_main: dict[str, list[str]] = defaultdict(list)

        for nid, node in graph.nodes.items():
            mains_by_node[nid] = node.main_competencies
            if node.critical:
                for m in node.main_competencies:
                    critical_nodes_by_main[m].append(nid)

        prerequisite_edges: list[CompetencyEdge] = []
        by_from: dict[str, list[CompetencyEdge]] = defaultdict(list)
        by_to: dict[str, list[CompetencyEdge]] = defaultdict(list)
        edge_by_pair: dict[tuple[str, str], CompetencyEdge] = {}

        for edge in graph.edges:
            if edge.relation != "PREREQUISITE":
                continue
            # PREREQUISITE: edge.from is prerequisite; edge.to is dependent.
            parents[edge.to_id].append(edge.from_id)
            children[edge.from_id].append(edge.to_id)
            if edge.allow_downward_blocking:
                blockable[edge.from_id].append(edge.to_id)
            prerequisite_edges.append(edge)
            by_from[edge.from_id].append(edge)
            by_to[edge.to_id].append(edge)
            # Duplicate pairs keep the strongest: a weaker parallel edge cannot weaken
            # an inference the stronger one already licenses.
            existing = edge_by_pair.get((edge.from_id, edge.to_id))
            if existing is None or edge.strength > existing.strength:
                edge_by_pair[(edge.from_id, edge.to_id)] = edge

        return GraphIndexes(
            parents_by_node={k: _as_tuple(v) for k, v in parents.items()},
            children_by_node={k: _as_tuple(v) for k, v in children.items()},
            mains_by_node=mains_by_node,
            critical_nodes_by_main={
                k: tuple(v) for k, v in critical_nodes_by_main.items()
            },
            prerequisite_edges=tuple(prerequisite_edges),
            prerequisite_edges_by_from={k: tuple(v) for k, v in by_from.items()},
            prerequisite_edges_by_to={k: tuple(v) for k, v in by_to.items()},
            edge_by_pair=edge_by_pair,
            blockable_children_by_node={k: _as_tuple(v) for k, v in blockable.items()},
        )

    # --- adjacency ---------------------------------------------------------
    def prerequisites_parents(self, node_id: str) -> tuple[str, ...]:
        return self.indexes.parents_by_node.get(node_id, ())

    def prerequisites_children(self, node_id: str) -> tuple[str, ...]:
        return self.indexes.children_by_node.get(node_id, ())

    def blockable_children(self, node_id: str) -> tuple[str, ...]:
        """Children this node is permitted to block, per `allow_downward_blocking`."""
        return self.indexes.blockable_children_by_node.get(node_id, ())

    def prerequisite_edge(self, parent: str, child: str) -> CompetencyEdge | None:
        return self.indexes.edge_by_pair.get((parent, child))

    def prerequisite_edges_into(self, node_id: str) -> tuple[CompetencyEdge, ...]:
        return self.indexes.prerequisite_edges_by_to.get(node_id, ())

    # --- traversal ---------------------------------------------------------
    def ancestors(self, node_id: str, *, include_self: bool = False) -> set[str]:
        return self._reachable(node_id, self.prerequisites_parents, include_self)

    def descendants(self, node_id: str, *, include_self: bool = False) -> set[str]:
        return self._reachable(node_id, self.prerequisites_children, include_self)

    def blockable_descendants(
        self,
        node_id: str,
        *,
        max_depth: int | None = None,
        ignore_edge_validation: bool = False,
    ) -> set[str]:
        """Descendants reachable only through edges that permit downward blocking.

        Breadth-first, so `max_depth` bounds the SHORTEST path to each node. Depth-first
        would record whichever path it happened to walk first and then refuse to re-expand
        the node from a shorter one, truncating the frontier for no stated reason.

        `ignore_edge_validation` walks every prerequisite edge instead, for the
        reporting-only preview of what an unvalidated edge set would block. The depth
        bound still applies, so the preview is what enabling those edges would do.
        """
        step = (
            self.prerequisites_children
            if ignore_edge_validation
            else self.blockable_children
        )
        found: set[str] = set()
        seen_depth: dict[str, int] = {node_id: 0}
        queue: deque[tuple[str, int]] = deque([(node_id, 0)])
        while queue:
            current, depth = queue.popleft()
            if max_depth is not None and depth >= max_depth:
                continue
            for child in step(current):
                if child in seen_depth and seen_depth[child] <= depth + 1:
                    continue
                seen_depth[child] = depth + 1
                found.add(child)
                queue.append((child, depth + 1))
        found.discard(node_id)
        return found

    def _reachable(self, node_id: str, step, include_self: bool) -> set[str]:
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            if cur != node_id or include_self:
                seen.add(cur)
            for nxt in step(cur):
                if nxt not in seen:
                    stack.append(nxt)
        return seen

    # --- mains -------------------------------------------------------------
    def mains_for_node(self, node_id: str) -> tuple[str, ...]:
        return self.indexes.mains_by_node.get(node_id, ())

    def critical_nodes_for_main(self, main_id: str) -> tuple[str, ...]:
        return self.indexes.critical_nodes_by_main.get(main_id, ())

    def mains_for_nodes(self, node_ids: Iterable[str]) -> set[str]:
        return {main for nid in node_ids for main in self.mains_for_node(nid)}
