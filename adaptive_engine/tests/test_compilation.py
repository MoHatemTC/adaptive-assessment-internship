"""Compilation is the one validation path: everything broken is refused with a code."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adaptive_engine import (
    AssessmentPolicy,
    InvalidDefinition,
    Measurement,
    Question,
    compile_assessment,
)
from adaptive_engine.tests.conftest import definition_with, mcq


def test_a_valid_assessment_compiles(definition):
    compiled = compile_assessment(definition)
    assert compiled.assessment_id == "python-backend"
    assert compiled.version == "v1"
    assert compiled.competency_ids == ("python", "databases")
    assert set(compiled.questions_by_id) == {q.question_id for q in definition.questions}
    assert compiled.definition is definition, "the definition is held once, not copied"


def test_indexes_cover_every_competency(definition):
    compiled = compile_assessment(definition)
    for competency_id in compiled.competency_ids:
        assert compiled.question_ids_by_competency[competency_id]


def test_duplicate_competency_ids_are_rejected():
    with pytest.raises(InvalidDefinition) as caught:
        compile_assessment(definition_with(competencies=["python", "python"]))
    assert caught.value.code == "invalid_definition"
    assert "duplicate competency" in str(caught.value)


def test_duplicate_question_ids_are_rejected():
    questions = [mcq("q-1", "python"), mcq("q-1", "python")]
    with pytest.raises(InvalidDefinition, match="duplicate question"):
        compile_assessment(definition_with(competencies=["python"], questions=questions))


def test_a_question_measuring_an_unknown_competency_is_rejected():
    questions = [mcq("q-1", "python"), mcq("q-2", "no-such-competency")]
    with pytest.raises(InvalidDefinition, match="unknown competency"):
        compile_assessment(definition_with(competencies=["python"], questions=questions))


def test_a_competency_no_question_measures_is_rejected():
    questions = [mcq("q-1", "python")]
    with pytest.raises(InvalidDefinition, match="never be assessed"):
        compile_assessment(
            definition_with(competencies=["python", "databases"], questions=questions)
        )


def test_a_graph_edge_to_an_unknown_competency_is_rejected():
    with pytest.raises(InvalidDefinition, match="unknown"):
        compile_assessment(definition_with(edges=[("python", "no-such-competency")]))


def test_a_self_loop_is_rejected():
    with pytest.raises(InvalidDefinition, match="self-loop"):
        compile_assessment(definition_with(edges=[("python", "python")]))


def test_a_duplicate_edge_is_rejected():
    with pytest.raises(InvalidDefinition, match="duplicate graph edge"):
        compile_assessment(
            definition_with(edges=[("python", "databases"), ("python", "databases")])
        )


def test_a_prerequisite_cycle_is_rejected():
    with pytest.raises(InvalidDefinition, match="cycle"):
        compile_assessment(
            definition_with(edges=[("python", "databases"), ("databases", "python")])
        )


def test_prerequisite_closure_is_transitive():
    compiled = compile_assessment(
        definition_with(
            competencies=["a", "b", "c"],
            questions=[mcq("qa", "a"), mcq("qb", "b"), mcq("qc", "c")],
            edges=[("a", "b"), ("b", "c")],
        )
    )
    assert compiled.ancestors_by_competency["c"] == {"a", "b"}
    assert compiled.ancestors_by_competency["a"] == frozenset()


def test_an_mcq_question_without_an_answer_key_is_rejected_at_the_model():
    with pytest.raises(ValidationError, match="answer_index"):
        Question(
            question_id="q-1",
            prompt="?",
            options=["a", "b"],
            measures=[Measurement(competency_id="python")],
        )


def test_out_of_range_adaptive_parameters_are_rejected_at_the_model():
    with pytest.raises(ValidationError):
        mcq("q-1", "python", discrimination=5.0)


def test_a_policy_with_the_cap_below_the_floor_is_rejected():
    with pytest.raises(ValidationError, match="min_questions_per_competency"):
        AssessmentPolicy(min_questions_per_competency=6, max_questions_per_competency=3)
