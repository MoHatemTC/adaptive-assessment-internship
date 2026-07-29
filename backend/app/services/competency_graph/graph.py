from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .models import CompetencyGraph


@dataclass(frozen=True)
class GraphIndexes:
    parents_by_node: dict[str, tuple[str, ...]]
    children_by_node: dict[str, tuple[str, ...]]
    mains_by_node: dict[str, tuple[str, ...]]
    critical_nodes_by_main: dict[str, tuple[str, ...]]


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
        mains_by_node: dict[str, tuple[str, ...]] = {}
        critical_nodes_by_main: dict[str, list[str]] = defaultdict(list)

        for nid, node in graph.nodes.items():
            mains_by_node[nid] = node.main_competencies
            if node.critical:
                for m in node.main_competencies:
                    critical_nodes_by_main[m].append(nid)

        for edge in graph.edges:
            if edge.relation != "PREREQUISITE":
                continue
            # PREREQUISITE: edge.from is prerequisite; edge.to is dependent.
            parents[edge.to_id].append(edge.from_id)
            children[edge.from_id].append(edge.to_id)

        return GraphIndexes(
            parents_by_node={k: _as_tuple(v) for k, v in parents.items()},
            children_by_node={k: _as_tuple(v) for k, v in children.items()},
            mains_by_node=mains_by_node,
            critical_nodes_by_main={
                k: tuple(v) for k, v in critical_nodes_by_main.items()
            },
        )

    def prerequisites_parents(self, node_id: str) -> tuple[str, ...]:
        return self.indexes.parents_by_node.get(node_id, ())

    def prerequisites_children(self, node_id: str) -> tuple[str, ...]:
        return self.indexes.children_by_node.get(node_id, ())

    def ancestors(self, node_id: str, *, include_self: bool = False) -> set[str]:
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            if cur != node_id or include_self:
                seen.add(cur)
            for p in self.prerequisites_parents(cur):
                stack.append(p)
        return seen

    def descendants(self, node_id: str, *, include_self: bool = False) -> set[str]:
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            if cur != node_id or include_self:
                seen.add(cur)
            for c in self.prerequisites_children(cur):
                stack.append(c)
        return seen

    def mains_for_node(self, node_id: str) -> tuple[str, ...]:
        return self.indexes.mains_by_node.get(node_id, ())

    def critical_nodes_for_main(self, main_id: str) -> tuple[str, ...]:
        return self.indexes.critical_nodes_by_main.get(main_id, ())


# Re-export name used by plan
CompetencyGraph = CompetencyGraph

