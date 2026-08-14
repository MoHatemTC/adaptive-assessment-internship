"""Compilation reuses the engine's validators and refuses anything broken."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from adaptive_engine import (
    InvalidDefinition,
    MeasurementPolicy,
    compile_assessment,
    content_hash_of,
)
from adaptive_engine.tests.conftest import definition_with, mcq_item, two_main_graph


def test_a_valid_assessment_compiles(definition):
    compiled = compile_assessment(definition)
    assert compiled.assessment_id == "python-backend"
    assert compiled.version == "v1"
    assert compiled.targets == ("databases", "python")
    assert compiled.definition is definition, "the definition is held once, not copied"
    assert compiled.graph_service is None


def test_a_graph_compiles_with_its_policy_resolved():
    compiled = compile_assessment(definition_with(graph=two_main_graph()))
    assert compiled.graph_service is not None
    assert compiled.resolved_policy is not None


def test_an_item_the_engine_schema_rejects_is_rejected():
    bad = mcq_item("q-1", "python")
    bad["cat"]["a"] = 99.0  # discrimination far outside the calibrated range

    # Constructing the typed definition already refuses it, with a field-level error...
    with pytest.raises(ValidationError, match=r"cat\.a"):
        definition_with(items=[bad])
    # ...and a raw payload sent straight to compile gets the typed error code instead.
    with pytest.raises(InvalidDefinition) as caught:
        compile_assessment(
            {"assessment_id": "python-backend", "version": "v1", "items": [bad]}
        )
    assert caught.value.code == "invalid_definition"


def test_an_mcq_item_without_an_answer_key_is_rejected():
    bad = mcq_item("q-1", "python")
    del bad["mcq"]["answer_index"]
    with pytest.raises(InvalidDefinition, match="q-1"):
        compile_assessment(definition_with(items=[bad]))


def test_duplicate_item_ids_are_rejected():
    items = [mcq_item("q-1", "python"), mcq_item("q-1", "python")]
    with pytest.raises(InvalidDefinition, match="duplicate item id"):
        compile_assessment(definition_with(items=items))


def test_an_invalid_graph_is_rejected():
    graph = two_main_graph()
    graph["edges"][0]["to"] = "no-such-node"
    with pytest.raises(InvalidDefinition, match="graph"):
        compile_assessment(definition_with(graph=graph))


def test_a_cyclic_prerequisite_graph_is_rejected():
    graph = two_main_graph()
    graph["edges"].append(
        {"from": "databases", "to": "python", "relation": "PREREQUISITE", "strength": 0.9}
    )
    with pytest.raises(InvalidDefinition, match="graph"):
        compile_assessment(definition_with(graph=graph))


def test_a_target_no_item_measures_is_rejected():
    with pytest.raises(InvalidDefinition, match="kubernetes"):
        compile_assessment(definition_with(targets=["python", "kubernetes"]))


def test_the_content_hash_pins_the_answer_keys():
    original = definition_with()
    edited_items = [item.model_dump() for item in original.items]
    edited_items[0]["mcq"] = {**edited_items[0]["mcq"], "answer_index": 2}
    edited = definition_with(items=edited_items)
    assert content_hash_of(original) != content_hash_of(edited)
    assert content_hash_of(original) == content_hash_of(definition_with())


def test_policy_overrides_map_onto_engine_setting_names():
    policy = MeasurementPolicy(
        se_target=0.6,
        max_questions_total=30,
        time_limit_minutes=45,
        graph_enabled=False,
    )
    assert policy.overrides() == {
        "cat_se_target": 0.6,
        "orchestrator_max_items": 30,
        "orchestrator_time_limit_minutes": 45,
        "competency_graph_enabled": False,
    }
