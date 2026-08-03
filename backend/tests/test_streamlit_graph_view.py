"""Regression tests for the dependency DAG shown in the Streamlit tester."""

from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[2]
for path in (ROOT / "backend", ROOT / "streamlit"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import graph_view  # noqa: E402


def test_focused_dag_is_valid_self_contained_svg() -> None:
    # Pinned to DA: the focus nodes below are DA sub-competencies.
    service = graph_view.graph_service("DA")

    svg = graph_view.svg_for_subgraph(
        service,
        focus_nodes={"DA.5"},
        mastered={"DA.1"},
        not_mastered={"DA.5"},
        blocked={"DA.4"},
        contradicted={"DA.3"},
    )

    root = ElementTree.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.attrib["aria-label"] == "Competency dependency graph"
    assert "DA.5" in svg
    assert "DA.1" in svg
    assert "DA.4" in svg
    assert "Graph legend" in svg
    assert "not mastered" in svg
    assert "#b42318" in svg
    assert "<script" not in svg
    assert "http://" not in svg.replace("http://www.w3.org/2000/svg", "")
    assert "https://" not in svg


def test_the_unvalidated_edge_forecast_is_drawn_as_a_forecast() -> None:
    """Dashed, its own fill, and never overriding a state the session measured.

    The forecast exists because every prerequisite edge in the AI Engineer graph ships
    inert, so the live inferred and blocked counts are structurally zero. It is only safe
    to show at all if a reader cannot mistake it for a result.
    """
    service = graph_view.graph_service("DA")

    svg = graph_view.svg_for_subgraph(
        service,
        focus_nodes=set(),
        mastered={"DA.1"},
        preview_inferred={"DA.2", "DA.1"},
        preview_blocked={"DA.3"},
    )

    assert "would be inferred (unvalidated edge)" in svg
    assert "#f2f7f3" in svg  # would-be-inferred fill
    assert "#fffaf0" in svg  # would-be-blocked fill
    # DA.1 was measured. A forecast may not repaint it.
    measured = svg.split('<title>DA.1:')[1].split("</g>")[0]
    assert "#d1e7dd" in measured
    assert "stroke-dasharray" not in measured


def test_a_dag_without_a_forecast_is_unchanged() -> None:
    service = graph_view.graph_service("DA")

    plain = graph_view.svg_for_subgraph(service, focus_nodes=set(), mastered={"DA.1"})
    empty_forecast = graph_view.svg_for_subgraph(
        service,
        focus_nodes=set(),
        mastered={"DA.1"},
        preview_inferred=set(),
        preview_blocked=set(),
    )

    assert plain == empty_forecast


def test_empty_focus_renders_the_complete_dag() -> None:
    service = graph_view.graph_service("DA")

    svg = graph_view.svg_for_subgraph(service, focus_nodes=set())

    for node_id in service.graph.nodes:
        assert f">{node_id}</text>" in svg
    assert svg.count("<rect ") >= len(service.graph.nodes) + 1
