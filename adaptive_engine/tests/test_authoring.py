"""Authoring: questions in, a validated graph and definition out — pure, nothing written."""

from __future__ import annotations

import pytest

from adaptive_engine import (
    InvalidDefinition,
    QuestionResponse,
    advance_assessment,
    authoring,
    compile_assessment,
    content_hash_of,
    start_assessment,
)
from adaptive_engine.tests.conftest import mcq_item


def sub_items() -> list[dict]:
    """Two mains, four sub-competencies, with deliberate co-measurement structure:
    every item measuring C1.2 also measures C1.1, so C1.1 -> C1.2 clears the 0.5
    relation threshold and becomes a prerequisite."""
    items = []
    for i in range(4):
        items.append(mcq_item(f"a{i}", "C1.1", b=float(i) - 2.0))
    for i in range(2):
        item = mcq_item(f"b{i}", "C1.2", b=float(i) - 1.0)
        item["measures"].append({"variable": "C1.1", "weight": 0.5})
        items.append(item)
    for i in range(3):
        items.append(mcq_item(f"c{i}", "C2.1", b=float(i) - 1.0))
    for i in range(3):
        items.append(mcq_item(f"d{i}", "C2.2", b=float(i) - 2.0))
    return items


def test_a_graph_is_derived_from_the_question_list_alone():
    graph = authoring.derive_graph("demo", sub_items())
    nodes = {n.competency_id: n for n in graph.nodes}
    assert {"C1", "C2", "C1.1", "C1.2", "C2.1", "C2.2"} <= set(nodes)
    assert nodes["C1"].node_type == "main"
    assert nodes["C1.1"].node_type == "sub_competency"

    relations = {(e.from_id, e.to_id): e for e in graph.edges}
    # Sub-to-main contributions exist and are weighted.
    assert relations[("C1.1", "C1")].relation == "CONTRIBUTES_TO"
    # Co-measurement above the threshold became a prerequisite — shipped inert.
    prerequisite = relations[("C1.2", "C1.1")]
    assert prerequisite.relation == "PREREQUISITE"
    assert prerequisite.allow_upward_inference is False
    assert prerequisite.metadata["validation_status"] == "unvalidated"
    # No cross-main edge: C1 and C2 items never co-measure.
    assert ("C1.1", "C2.1") not in relations and ("C2.1", "C1.1") not in relations


def test_the_thresholds_used_are_stamped_into_the_graph():
    graph = authoring.derive_graph("demo", sub_items(), relation_threshold=0.7, edge_floor=0.1)
    assert graph.derivation is not None
    assert graph.derivation.basis == "item_co_measurement"
    assert graph.derivation.relation_threshold == 0.7
    assert graph.derivation.edge_floor == 0.1


def test_the_threshold_decides_the_relation():
    strict = authoring.derive_graph("demo", sub_items(), relation_threshold=1.1)
    relations = {(e.from_id, e.to_id): e.relation for e in strict.edges}
    assert relations[("C1.2", "C1.1")] == "CONTRIBUTES_TO", (
        "above an unreachable threshold, no co-measurement is a prerequisite"
    )


def test_a_declaration_supplies_what_questions_cannot():
    declaration = [
        {"id": "C1.1", "title": "Core Python", "critical": True},
        {"id": "C1.2", "critical": False},
    ]
    graph = authoring.derive_graph("demo", sub_items(), declaration=declaration)
    nodes = {n.competency_id: n for n in graph.nodes}
    assert nodes["C1.1"].title == "Core Python"
    assert nodes["C1.1"].critical is True
    assert nodes["C1.2"].critical is False


def test_validate_content_accepts_a_sound_pair():
    items = sub_items()
    report = authoring.validate_content("demo", items, authoring.derive_graph("demo", items))
    assert report.accepted
    assert report.items == len(items)
    assert report.mains == ["C1", "C2"]


def test_validate_content_names_what_is_wrong():
    items = sub_items()
    graph = authoring.derive_graph("demo", items)
    extra = mcq_item("x1", "C9.9")  # measures a node the graph has never heard of
    report = authoring.validate_content("demo", [*items, extra], graph)
    assert not report.accepted
    assert any(f.code == "measured_node_absent" for f in report.errors)

    tiny_budget = authoring.validate_content("demo", items, graph, question_budget=2)
    assert any(f.code == "coverage_unreachable" for f in tiny_budget.errors)


def test_build_assessment_returns_a_runnable_definition():
    definition = authoring.build_assessment("demo", "v1", sub_items())
    assert definition.graph is not None and definition.graph.derivation is not None
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=3)
    decision = advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(question_id=decision.question.item.item_id, answer=0),
    )
    assert decision.state.questions_answered == 1


def test_build_assessment_accepts_an_authored_graph_but_validates_it():
    items = sub_items()
    authored = authoring.derive_graph("demo", items)  # stands in for a hand-written one
    definition = authoring.build_assessment("demo", "v1", items, graph=authored)
    assert definition.graph == authored

    missing_nodes = authored.model_copy(
        update={
            "nodes": [n for n in authored.nodes if n.competency_id != "C1.1"],
            "edges": [e for e in authored.edges if "C1.1" not in (e.from_id, e.to_id)],
        }
    )
    with pytest.raises(InvalidDefinition, match="measured_node_absent"):
        authoring.build_assessment("demo", "v1", items, graph=missing_nodes)


def test_a_non_critical_declaration_switches_coverage_to_critical_only():
    declaration = [{"id": "C1.2", "critical": False}]
    definition = authoring.build_assessment("demo", "v1", sub_items(), declaration=declaration)
    assert definition.policy is not None
    assert definition.policy.coverage_critical_only is True

    all_critical = authoring.build_assessment("demo", "v1", sub_items())
    assert all_critical.policy is None


def test_revise_adds_updates_and_retires():
    original = authoring.build_assessment("demo", "v1", sub_items())
    replacement = mcq_item("a0", "C1.1", b=0.5, answer_index=2)
    revised = authoring.revise_assessment(
        original,
        version="v2",
        add_items=[mcq_item("new-1", "C1.1", b=1.5)],
        update_items=[replacement],
        retire_item_ids=["c0"],
    )
    assert revised.version == "v2"
    by_id = {item.item_id: item for item in revised.items}
    assert "new-1" in by_id
    assert by_id["a0"].payload["answer_index"] == 2
    assert by_id["c0"].status == "retired"
    assert content_hash_of(revised) != content_hash_of(original)
    # The original definition is untouched.
    assert {i.item_id: i for i in original.items}["c0"].status == "active"


def test_a_retired_item_is_never_administered():
    original = authoring.build_assessment("demo", "v1", sub_items())
    revised = authoring.revise_assessment(original, version="v2", retire_item_ids=["a0"])
    compiled = compile_assessment(revised)
    assert all(
        item.item_id != "a0"
        for variable in compiled.targets
        for item in compiled.bank.shortlist(variable, exclude=set())
    )


def test_a_revision_must_carry_a_new_version():
    original = authoring.build_assessment("demo", "v1", sub_items())
    with pytest.raises(InvalidDefinition, match="new version"):
        authoring.revise_assessment(original, version="v1", retire_item_ids=["a0"])


def test_revise_rejects_operations_on_unknown_items():
    original = authoring.build_assessment("demo", "v1", sub_items())
    with pytest.raises(InvalidDefinition, match="unknown item"):
        authoring.revise_assessment(original, version="v2", retire_item_ids=["ghost"])
    with pytest.raises(InvalidDefinition, match="unknown item"):
        authoring.revise_assessment(
            original, version="v2", update_items=[mcq_item("ghost", "C1.1")]
        )


def test_keeping_the_graph_refuses_items_it_cannot_place():
    original = authoring.build_assessment("demo", "v1", sub_items())
    with pytest.raises(InvalidDefinition, match="measured_node_absent"):
        authoring.revise_assessment(
            original, version="v2", add_items=[mcq_item("x1", "C9.9")], graph="keep"
        )
    # Rederiving instead makes the new node part of the graph.
    revised = authoring.revise_assessment(
        original, version="v2", add_items=[mcq_item("x1", "C9.9")], graph="rederive"
    )
    assert any(n.competency_id == "C9.9" for n in revised.graph.nodes)
