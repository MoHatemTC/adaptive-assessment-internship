"""Core unit tests for the voice / open-ended CAT package."""

from __future__ import annotations

import asyncio

from app.schemas.voice import (
    CriterionEvidence,
    VoiceEvaluation,
    VoiceResponsePackage,
    VoiceTurn,
)
from app.services.orchestrator.bank import JsonUnifiedBank
from app.services.orchestrator.calibration import OPEN_RUBRIC_FLOOR, open_cat_parameters
from app.services.voice import evidence, evaluator, quotes
from app.services.voice.grader import grade_voice
from app.services.voice.rubrics import load_rubric
from app.services.voice.session import VoiceAssessmentRunner


def test_open_calibration_floor_and_a_bounds():
    params = open_cat_parameters(0.5, 2.0)
    assert params["c"] == OPEN_RUBRIC_FLOOR
    assert params["a"] == 2.0
    assert -4.0 <= params["b"] <= 4.0


def test_bank_loads_open_items_only():
    bank = JsonUnifiedBank()
    items = bank.all_items()
    assert len(items) >= 100
    assert all(i.modality == "open" for i in items)
    assert all(1.8 <= i.cat.a <= 2.3 for i in items)
    assert all(abs(i.cat.c - OPEN_RUBRIC_FLOOR) < 1e-9 for i in items)
    assert bank.variables()


def test_quote_exact_and_missing():
    turn = "Mutable defaults share state across calls."
    tier, ratio = quotes.match_quote("defaults share state", turn)
    assert tier in {"exact", "normalized", "subsequence", "fuzzy"}
    assert ratio > 0
    bad_tier, _ = quotes.match_quote("completely unrelated quantum foam", turn)
    assert bad_tier is None


def test_unscorable_package_zero_weight():
    item = JsonUnifiedBank().all_items()[0]
    package = VoiceResponsePackage(
        item_id=item.item_id,
        outcome_status="unscorable",
        reason_code="TOO_SHORT",
        turns=[],
        total_speech_seconds=0.5,
    )
    graded = asyncio.run(evaluator.evaluate(item, package, use_llm=False))
    response = grade_voice(graded)
    assert response.modality == "open"
    assert all(o["weight"] == 0.0 for o in response.outcomes)


def test_heuristic_evaluate_then_grade_updates():
    bank = JsonUnifiedBank()
    runner = VoiceAssessmentRunner(bank)
    comps = bank.variables()[:1]
    assert comps
    state = runner.begin(comps, intake={comps[0]: 3}, confidence={comps[0]: False})
    state = asyncio.run(runner.fill_queue(state, use_llm=False, seed=1))
    state = runner.ensure_presenting(state)
    nxt = runner.next_item(state)
    assert nxt is not None
    item, _ = nxt
    text = (
        "The default list is evaluated once at definition time and shared across calls. "
        "Use None as a sentinel and allocate a fresh list inside the function. "
        "I would also add a unit test that calls twice and checks independence."
    )
    new_state, graded_voice, graded = asyncio.run(
        runner.submit_text(state, item, text, use_llm=False, seed=2)
    )
    assert graded_voice.evaluation.criterion_evidence
    assert graded.modality == "open"
    assert new_state.items_administered == 1
    vs = new_state.variables[comps[0]]
    assert vs.observations >= 1


def test_evidence_strength_respects_confidence_floor():
    package = VoiceResponsePackage(
        item_id="i",
        turns=[VoiceTurn(turn_id="t0", role="candidate", text="short")],
        mean_transcript_confidence=0.2,
        total_speech_seconds=10.0,
        word_count=20,
        final_text="short answer text here for testing",
    )
    evaluation = VoiceEvaluation(
        item_id="i",
        rubric_id="r",
        criterion_evidence=[
            CriterionEvidence(
                criterion_id="c1",
                competency_id="C1.1",
                raw_score=4,
                maximum_score=8,
                confidence=0.9,
                quote="short answer",
                quote_turn_id="t0",
            )
        ],
    )
    rubric = {
        "criteria": [
            {
                "criterion_id": "c1",
                "competency_id": "C1.1",
                "weight": 1.0,
                "maximum_score": 8.0,
            }
        ]
    }
    out = evidence.project_competency_evidence(package, evaluation, rubric)
    assert out
    assert out[0].evidence_strength < 1.0


def test_load_rubric_for_first_item():
    item = JsonUnifiedBank().all_items()[0]
    rubric_id = item.payload["rubric_id"]
    rubric = load_rubric(rubric_id)
    assert rubric["rubric_id"] == rubric_id
    assert rubric["criteria"]
