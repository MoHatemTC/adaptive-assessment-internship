from __future__ import annotations

from app.services.competency_graph import load_default_competency_graph
from app.services.competency_graph.coverage import (
    coverage_allows_convergence,
    sub_nodes_for_main,
    unmeasured_required_nodes,
)
from app.services.competency_graph.graph import CompetencyGraphService


def test_da_requires_all_six_sub_nodes_by_default() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    required = sub_nodes_for_main(graph, "DA", critical_only=False)
    assert required == {
        "DA.1",
        "DA.2",
        "DA.3",
        "DA.4",
        "DA.5",
        "DA.6",
    }


def test_critical_only_limits_coverage_to_da1_and_da2() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    required = sub_nodes_for_main(graph, "DA", critical_only=True)
    assert required == {"DA.1", "DA.2"}


def test_inferred_mastery_does_not_satisfy_coverage() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    # Even if every node were listed as "inferred", coverage uses direct sets only.
    assert not coverage_allows_convergence(
        graph,
        "DA",
        mastered=set(),
        not_mastered=set(),
        critical_only=False,
    )
    missing = unmeasured_required_nodes(
        graph,
        "DA",
        mastered={"DA.1"},  # direct success on one node only
        not_mastered=set(),
        critical_only=False,
    )
    assert "DA.1" not in missing
    assert "DA.3" in missing


def test_success_or_failure_both_count_as_measured() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    assert coverage_allows_convergence(
        graph,
        "DA",
        mastered={"DA.1", "DA.2", "DA.4", "DA.5", "DA.6"},
        not_mastered={"DA.3"},
        critical_only=False,
    )


def test_partial_scorable_evidence_counts_as_direct_measurement() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    required = sub_nodes_for_main(graph, "DA")
    assert coverage_allows_convergence(
        graph,
        "DA",
        measured=set(required),
        mastered=set(),
        not_mastered=set(),
        critical_only=False,
    )
