"""The candidate boundary: nothing that grades ever crosses it."""

from __future__ import annotations

import json

from adaptive_engine import compile_assessment, start_assessment
from adaptive_engine.tests.conftest import bank_for, code_item, definition_with


def test_presented_questions_contain_no_answer_keys(definition):
    compiled = compile_assessment(definition)
    decision = start_assessment(compiled, seed=7)
    item = decision.question.item
    dumped = item.model_dump()
    assert "answer_index" not in dumped
    assert "reference_solution" not in dumped
    assert item.options, "the candidate still gets what they need to answer"


def test_nothing_in_a_serialized_decision_leaks_grading_data():
    items = [
        *bank_for("python", 4),
        code_item("code-1", [("python", 0.8)]),
    ]
    items[-1]["code"]["reference_solution"] = "def secret(): ..."
    compiled = compile_assessment(definition_with(items=items))
    decision = start_assessment(compiled, seed=7)
    over_the_wire = json.dumps(decision.model_dump(mode="json"))
    assert "answer_index" not in over_the_wire
    assert "reference_solution" not in over_the_wire
    assert "secret" not in over_the_wire
