from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import (
    CompetencyEdge,
    CompetencyGraph,
    CompetencyNode,
)
from .policy import GraphPolicy

VALID_RELATIONS: set[str] = {
    "PREREQUISITE",
    "CONTRIBUTES_TO",
    "EQUIVALENT_TO",
    "CONTEXT_VARIANT_OF",
    "RELATED_TO",
    "SUPPORTS",
}


class CompetencyGraphValidationError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise CompetencyGraphValidationError(message)


def _parse_node(raw: dict[str, Any]) -> CompetencyNode:
    node_type = raw.get("node_type", "sub_competency")
    if node_type not in ("main", "sub_competency"):
        raise CompetencyGraphValidationError(
            f"invalid node_type={node_type!r} for {raw.get('competency_id')!r}"
        )
    main_competencies = raw.get("main_competencies") or []
    if isinstance(main_competencies, str):
        main_competencies = [main_competencies]
    return CompetencyNode(
        competency_id=str(raw["competency_id"]),
        title=str(raw.get("title") or raw.get("competency_id")),
        node_type=node_type,  # type: ignore[arg-type]
        critical=bool(raw.get("critical", False)),
        context_specific=bool(raw.get("context_specific", False)),
        main_competencies=tuple(str(x) for x in main_competencies),
        metadata=dict(raw.get("metadata") or {}),
    )


def _parse_edge(raw: dict[str, Any]) -> CompetencyEdge:
    relation = str(raw["relation"])
    _require(relation in VALID_RELATIONS, f"invalid relation={relation!r}")
    return CompetencyEdge(
        from_id=str(raw["from"]),
        to_id=str(raw["to"]),
        relation=relation,  # type: ignore[arg-type]
        strength=float(raw.get("strength", 1.0)),
        weight=float(raw.get("weight", 1.0)),
        allow_upward_inference=bool(raw.get("allow_upward_inference", True)),
        allow_downward_blocking=bool(raw.get("allow_downward_blocking", True)),
        metadata=dict(raw.get("metadata") or {}),
    )


def _detect_prerequisite_cycles(graph: CompetencyGraph) -> None:
    # Cycle detection via DFS on PREREQUISITE edges only.
    adj: dict[str, list[str]] = {}
    for node_id in graph.nodes:
        adj[node_id] = []
    for edge in graph.edges:
        if edge.relation != "PREREQUISITE":
            continue
        adj[edge.from_id].append(edge.to_id)

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(n: str) -> None:
        if n in visiting:
            raise CompetencyGraphValidationError(
                f"prerequisite cycle detected at {n!r}"
            )
        if n in visited:
            return
        visiting.add(n)
        for m in adj.get(n, []):
            dfs(m)
        visiting.remove(n)
        visited.add(n)

    for node_id in graph.nodes:
        if node_id not in visited:
            dfs(node_id)


def validate_graph(graph: CompetencyGraph) -> None:
    # Unique node IDs: dict already enforces, but validate from raw structures too.
    _require(
        len(graph.nodes) == len(set(graph.nodes.keys())), "duplicate node IDs detected"
    )
    _require(all(n.competency_id for n in graph.nodes.values()), "empty competency_id")

    for edge in graph.edges:
        _require(
            edge.from_id in graph.nodes,
            f"edge.from {edge.from_id!r} missing from nodes",
        )
        _require(
            edge.to_id in graph.nodes, f"edge.to {edge.to_id!r} missing from nodes"
        )
        _require(
            edge.relation in VALID_RELATIONS, f"edge relation {edge.relation!r} invalid"
        )
        if edge.relation == "PREREQUISITE":
            # Strength and allow flags must be sane.
            _require(
                edge.strength > 0.0,
                f"PREREQUISITE strength must be >0 for {edge.from_id}->{edge.to_id}",
            )

    _detect_prerequisite_cycles(graph)


def parse_and_validate_graph(raw: dict[str, Any], *, source: str) -> CompetencyGraph:
    """Build a validated graph from the raw JSON structure.

    Split out from `load_and_validate_graph` so a graph can be validated BEFORE it is
    written anywhere. The bank registry accepts graphs over HTTP, and "write it to disk,
    then try to load it, then delete it if that failed" is a worse answer than parsing the
    structure it already has — it leaves a half-registered bank behind on the one path
    where something went wrong.

    `source` names the graph in error messages. A file path when there is one, the bank id
    when the graph arrived in a request body.
    """
    schema_version = str(raw.get("version") or raw.get("schema_version") or "1.0")

    raw_nodes = raw.get("nodes") or []
    if isinstance(raw_nodes, dict):
        raw_nodes = raw_nodes.values()
    nodes: dict[str, CompetencyNode] = {
        str(n["competency_id"]): _parse_node(n) for n in raw_nodes
    }

    raw_edges = raw.get("edges") or []
    if isinstance(raw_edges, dict):
        raw_edges = raw_edges.values()
    edges = tuple(_parse_edge(e) for e in raw_edges)

    # The bank's own propagation policy. Absent means "no opinion", which is what every
    # graph authored before the block existed means — so adding the block is additive and
    # an old graph file keeps behaving exactly as it did.
    try:
        policy = GraphPolicy.from_dict(raw.get("policy"))
    except ValueError as exc:
        raise CompetencyGraphValidationError(
            f"{source}: invalid policy block — {exc}"
        ) from exc

    graph = CompetencyGraph(
        schema_version=schema_version, nodes=nodes, edges=edges, policy=policy
    )
    validate_graph(graph)
    return graph


def load_and_validate_graph(path: str | Path) -> CompetencyGraph:
    p = Path(path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    return parse_and_validate_graph(raw, source=p.name)
