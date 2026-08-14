"""The competency graph travels in the definition and drives the engine's own gating."""

from __future__ import annotations

from adaptive_engine import (
    QuestionResponse,
    advance_assessment,
    compile_assessment,
    start_assessment,
)
from adaptive_engine.tests.conftest import definition_with, mcq_item


def sub_competency_graph() -> dict:
    return {
        "version": "1.0",
        "nodes": [
            {"competency_id": "python", "title": "Python", "node_type": "main"},
            {
                "competency_id": "python.syntax",
                "title": "Syntax",
                "node_type": "sub_competency",
                "critical": True,
                "main_competencies": ["python"],
            },
            {
                "competency_id": "python.testing",
                "title": "Testing",
                "node_type": "sub_competency",
                "main_competencies": ["python"],
            },
        ],
        "edges": [
            {
                "from": "python.syntax",
                "to": "python.testing",
                "relation": "PREREQUISITE",
                "strength": 0.9,
            }
        ],
    }


def sub_competency_items() -> list[dict]:
    return [
        mcq_item(f"syntax-{i}", "python.syntax", b=float(i) - 2.0) for i in range(6)
    ] + [
        mcq_item(f"testing-{i}", "python.testing", b=float(i) - 2.0) for i in range(6)
    ]


def test_direct_evidence_is_recorded_against_graph_nodes():
    compiled = compile_assessment(
        definition_with(items=sub_competency_items(), graph=sub_competency_graph())
    )
    assert compiled.targets == ("python",)
    decision = start_assessment(compiled, seed=7)
    answered_node = decision.question.item.sub_competency or decision.question.item.competency
    decision = advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(
            question_id=decision.question.item.item_id, answer=0
        ),
    )
    measured = decision.state.engine_state.graph_direct_measured_nodes
    assert measured, "a graded response must mark its node directly measured"
    assert answered_node in measured or any(m.startswith("python.") for m in measured)


def test_the_report_carries_graph_provenance(monkeypatch):
    from cat_engine.engine.config.settings import settings

    monkeypatch.setattr(settings, "orchestrator_max_items", 4)
    compiled = compile_assessment(
        definition_with(items=sub_competency_items(), graph=sub_competency_graph())
    )
    decision = start_assessment(compiled, seed=7)
    while decision.status == "question":
        decision = advance_assessment(
            compiled,
            state=decision.state,
            response=QuestionResponse(
                question_id=decision.question.item.item_id, answer=0
            ),
        )
    assert decision.report.graph, "the graph block reports what the run concluded"


def test_an_assessment_without_a_graph_never_borrows_one(definition):
    """A definition without a graph must not fall back to some other bank's graph."""
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    decision = advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(
            question_id=decision.question.item.item_id, answer=0
        ),
    )
    assert decision.state.engine_state.graph_direct_measured_nodes == []
