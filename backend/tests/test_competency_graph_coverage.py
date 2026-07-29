from __future__ import annotations

from app.services.competency_graph import load_default_competency_graph
from app.services.competency_graph.coverage import (
    coverage_allows_convergence,
    sub_nodes_for_main,
    unmeasured_required_nodes,
)
from app.services.competency_graph.graph import CompetencyGraphService


def test_py_requires_all_ten_sub_nodes_by_default() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    required = sub_nodes_for_main(graph, "PY", critical_only=False)
    assert required == {
        "PY.1",
        "PY.2",
        "PY.3",
        "PY.4",
        "PY.5",
        "PY.6",
        "PY.7",
        "PY.8",
        "PY.9",
        "PY.10",
    }


def test_critical_only_limits_coverage_to_py1_and_py2() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    required = sub_nodes_for_main(graph, "PY", critical_only=True)
    assert required == {"PY.1", "PY.2"}


def test_inferred_mastery_does_not_satisfy_coverage() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    # Even if every node were listed as "inferred", coverage uses direct sets only.
    assert not coverage_allows_convergence(
        graph,
        "PY",
        mastered=set(),
        not_mastered=set(),
        critical_only=False,
    )
    missing = unmeasured_required_nodes(
        graph,
        "PY",
        mastered={"PY.1"},  # direct success on one node only
        not_mastered=set(),
        critical_only=False,
    )
    assert "PY.1" not in missing
    assert "PY.3" in missing


def test_success_or_failure_both_count_as_measured() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    assert coverage_allows_convergence(
        graph,
        "PY",
        mastered={"PY.1", "PY.2", "PY.4", "PY.5", "PY.6", "PY.7", "PY.8", "PY.9", "PY.10"},
        not_mastered={"PY.3"},
        critical_only=False,
    )


def test_partial_scorable_evidence_counts_as_direct_measurement() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    required = sub_nodes_for_main(graph, "PY")
    assert coverage_allows_convergence(
        graph,
        "PY",
        measured=set(required),
        mastered=set(),
        not_mastered=set(),
        critical_only=False,
    )
