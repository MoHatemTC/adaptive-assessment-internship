"""What the prerequisite graph does at runtime — and the one thing it must never do."""

from __future__ import annotations

from adaptive_engine import (
    GraphNodeEvidence,
    QuestionResponse,
    advance_assessment,
    compile_assessment,
    start_assessment,
)
from adaptive_engine.graph import blocked_competencies, failing_competencies
from adaptive_engine.selection import select_next_question
from adaptive_engine.tests.conftest import definition_with


def test_two_strong_failures_block_dependents_and_steer_selection():
    definition = definition_with(edges=[("python", "databases")])
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)

    # Both competencies tie at maximum uncertainty, and the id tie-break would pick
    # databases. Two strong failures on its prerequisite override that: the run
    # investigates python first instead of walking into a dependent of a failing node.
    state = decision.state.model_copy(
        update={
            "graph_evidence": {"python": GraphNodeEvidence(strong_failures=2)},
            "current_question_id": None,
        }
    )
    assert failing_competencies(state.graph_evidence) == {"python"}
    assert blocked_competencies(compiled, state.graph_evidence) == {"databases"}
    chosen = select_next_question(compiled, state)
    assert chosen.measures[0].competency_id == "python"

    unblocked = state.model_copy(update={"graph_evidence": {}})
    assert blocked_competencies(compiled, unblocked.graph_evidence) == frozenset()
    assert (
        select_next_question(compiled, unblocked).measures[0].competency_id
        == "databases"
    )


def test_a_recovery_unblocks_without_separate_bookkeeping():
    definition = definition_with(edges=[("python", "databases")])
    compiled = compile_assessment(definition)
    evidence = {"python": GraphNodeEvidence(strong_failures=2, strong_successes=3)}
    assert failing_competencies(evidence) == frozenset()
    assert blocked_competencies(compiled, evidence) == frozenset()


def test_graph_evidence_never_moves_a_posterior_by_itself(definition):
    """Blocking is a selection preference. Only a graded response updates a posterior."""
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    with_blocking = decision.state.model_copy(
        update={"graph_evidence": {"python": GraphNodeEvidence(strong_failures=5)}}
    )
    assert (
        with_blocking.competency_states == decision.state.competency_states
    ), "graph evidence and posteriors are independent state"


def test_wrong_answers_accumulate_strong_failures_through_the_runtime(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    measured = decision.question.question_id.split("-q")[0]
    decision = advance_assessment(
        compiled,
        state=decision.state,
        response=QuestionResponse(question_id=decision.question.question_id, answer=1),
    )
    assert decision.state.graph_evidence[measured].strong_failures == 1
    assert decision.state.graph_evidence[measured].strong_successes == 0
