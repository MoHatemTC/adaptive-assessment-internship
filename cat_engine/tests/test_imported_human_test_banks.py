"""Regression coverage for the two deployable human-test bank imports."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np

from cat_engine.engine.schemas.code_adaptive import Question
from cat_engine.engine.services.adaptive.irt import fisher_information
from cat_engine.engine.services.competency_graph.coverage import sub_nodes_for_main
from cat_engine.engine.services.orchestrator import registry
from cat_engine.engine.services.orchestrator.grader import GraderAgent
from cat_engine.engine.services.voice.evaluator import _rubric_from_payload

EXPECTED = {
    "AIE-JR-V3": {
        "items": 60,
        "mains": {"C1"},
        "modalities": {"mcq": 39, "code": 12, "voice": 9},
    },
    "JAI-600": {
        "items": 600,
        "mains": {"C1", "C2", "C3", "C4", "C5", "C6"},
        "modalities": {"mcq": 300, "code": 150, "voice": 150},
        "quarantined_code": 64,
    },
}
DATA = Path(__file__).resolve().parents[1] / "engine" / "data"
GENERATED_FILES = {
    "AIE-JR-V3": DATA / "question_bank_AIE_JR_v3.json",
    "JAI-600": DATA / "question_bank_JAI_2026_600.json",
}


def test_new_banks_are_additive() -> None:
    assert {"DA", "PY", "AIE", *EXPECTED}.issubset(registry.REGISTRY)


def test_imported_bank_counts_and_ids() -> None:
    for bank_id, expected in EXPECTED.items():
        bank = registry.get_bank(bank_id)
        items = bank.all_items()
        assert len(items) == expected["items"]
        assert len({item.item_id for item in items}) == len(items)
        assert set(bank.variables()) == expected["mains"]
        assert Counter(item.modality for item in items) == expected["modalities"]


def test_every_imported_item_has_usable_cat_and_duration() -> None:
    for bank_id in EXPECTED:
        for item in registry.get_bank(bank_id).all_items():
            assert item.cat.a > 0
            assert -4 <= item.cat.b <= 4
            assert 0 <= item.cat.c < 1
            assert item.expected_seconds > 0


def test_every_synthesized_cat_triple_matches_its_recorded_prior_bank_donor() -> None:
    for bank_id, path in GENERATED_FILES.items():
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["import_metadata"]["calibration_status"] == (
            "SYNTHESIZED_FROM_PRIOR_BANK_PARAMETERS_NOT_ITEM_CALIBRATED"
        )
        triples = set()
        for entry in raw["items"]:
            provenance = entry["cat_synthesis"]
            donor_bank_id = provenance["donor_bank_id"]
            assert donor_bank_id in {"DA", "PY", "AIE"}
            donor = registry.get_bank(donor_bank_id).get(provenance["donor_item_id"])
            assert donor is not None
            assert entry["cat"] == donor.cat.model_dump()
            triples.add(tuple(entry["cat"].values()))
        assert len(triples) > 20, f"{bank_id} collapsed to near-constant CAT parameters"


def test_synthesized_difficulty_strata_remain_ordered() -> None:
    for path in GENERATED_FILES.values():
        raw = json.loads(path.read_text(encoding="utf-8"))
        by_modality: dict[str, dict[str, list[float]]] = {}
        for item in raw["items"]:
            modality = (
                "voice"
                if item["modality"] in {"open", "voice"}
                else item["modality"]
            )
            label = item[item["modality"]]["authored_difficulty_label"]
            by_modality.setdefault(modality, {}).setdefault(label, []).append(
                item["cat"]["b"]
            )
        for strata in by_modality.values():
            medians = {
                label: float(np.median(values)) for label, values in strata.items()
            }
            if {"easy", "medium", "hard"}.issubset(medians):
                assert medians["easy"] < medians["medium"] < medians["hard"]


def test_synthesized_banks_reach_precision_within_twelve_items() -> None:
    for bank_id in EXPECTED:
        bank = registry.get_bank(bank_id)
        for main in bank.variables():
            pool = bank.shortlist(main, exclude=set())
            for theta in (-2.0, -1.0, 0.0, 1.0, 2.0):
                information = sorted(
                    (
                        fisher_information(theta, item.cat.a, item.cat.b, item.cat.c)
                        for item in pool
                    ),
                    reverse=True,
                )
                best_se = 1.0 / np.sqrt(sum(information[:12]))
                assert best_se <= 0.55, (
                    f"{bank_id}/{main}@{theta}: SE {best_se:.4f}"
                )


def test_every_imported_code_payload_satisfies_the_executable_schema() -> None:
    for bank_id in EXPECTED:
        items = registry.get_bank(bank_id).all_items()
        for item in (candidate for candidate in items if candidate.modality == "code"):
            question = Question.model_validate(GraderAgent.as_code_question(item))
            assert question.tests
            assert any(t.get("visibility") == "public" for t in item.payload["tests"])
            if item.status == "active":
                assert any(
                    t.get("visibility") == "hidden" for t in item.payload["tests"]
                )


def test_public_only_code_items_are_retained_but_never_selectable() -> None:
    bank = registry.get_bank("JAI-600")
    quarantined = [
        item
        for item in bank.all_items()
        if item.status == "inactive_missing_hidden_tests"
    ]
    assert len(quarantined) == EXPECTED["JAI-600"]["quarantined_code"]
    for item in quarantined:
        assert item not in bank.shortlist(item.measures[0].variable, exclude=set())


def test_every_imported_voice_payload_builds_a_scorable_rubric() -> None:
    for bank_id in EXPECTED:
        items = registry.get_bank(bank_id).all_items()
        for item in (candidate for candidate in items if candidate.modality == "voice"):
            rubric = _rubric_from_payload(item)
            assert rubric["criteria"], item.item_id
            assert all(c["maximum_score"] > 0 for c in rubric["criteria"])


def test_imported_graphs_are_coverage_only_and_match_the_banks() -> None:
    for bank_id in EXPECTED:
        bank = registry.get_bank(bank_id)
        graph = registry.get_graph_service(bank_id)
        assert graph is not None
        assert not [e for e in graph.graph.edges if e.relation == "PREREQUISITE"]
        measured = {m.variable for item in bank.all_items() for m in item.measures}
        for main in bank.variables():
            required = sub_nodes_for_main(graph, main, critical_only=False)
            assert required
            assert set(required).issubset(measured)
