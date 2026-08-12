"""Coverage helpers for graph-gated convergence.

A main competency may only *converge* after its required sub-competencies have received
direct, scorable evidence. The score can be success, failure, or partial credit; inferred
mastery does not count. Budget stops remain allowed and never claim convergence.
"""

from __future__ import annotations

from .graph import CompetencyGraphService


def sub_nodes_for_main(
    graph: CompetencyGraphService,
    main_id: str,
    *,
    critical_only: bool = False,
) -> frozenset[str]:
    """Sub-competency nodes that belong to ``main_id``."""
    required: set[str] = set()
    for nid, node in graph.graph.nodes.items():
        if node.node_type != "sub_competency":
            continue
        mains = set(node.main_competencies)
        if not mains and "." in nid:
            mains = {nid.split(".", 1)[0]}
        if main_id not in mains:
            continue
        if critical_only and not node.critical:
            continue
        required.add(nid)
    return frozenset(required)


def directly_measured_nodes(
    *,
    measured: set[str] | frozenset[str] | list[str] | tuple[str, ...] = (),
    mastered: set[str] | frozenset[str] | list[str],
    not_mastered: set[str] | frozenset[str] | list[str],
) -> frozenset[str]:
    """Direct scorable evidence, with status sets retained for old saved sessions."""
    return frozenset(measured) | frozenset(mastered) | frozenset(not_mastered)


def unmeasured_required_nodes(
    graph: CompetencyGraphService,
    main_id: str,
    *,
    measured: set[str] | frozenset[str] | list[str] | tuple[str, ...] = (),
    mastered: set[str] | frozenset[str] | list[str],
    not_mastered: set[str] | frozenset[str] | list[str],
    critical_only: bool = False,
) -> frozenset[str]:
    required = sub_nodes_for_main(graph, main_id, critical_only=critical_only)
    direct = directly_measured_nodes(
        measured=measured,
        mastered=mastered,
        not_mastered=not_mastered,
    )
    return frozenset(required - direct)


def coverage_allows_convergence(
    graph: CompetencyGraphService,
    main_id: str,
    *,
    measured: set[str] | frozenset[str] | list[str] | tuple[str, ...] = (),
    mastered: set[str] | frozenset[str] | list[str],
    not_mastered: set[str] | frozenset[str] | list[str],
    critical_only: bool = False,
) -> bool:
    """True when every required sub-node has direct success or failure evidence."""
    return not unmeasured_required_nodes(
        graph,
        main_id,
        measured=measured,
        mastered=mastered,
        not_mastered=not_mastered,
        critical_only=critical_only,
    )
