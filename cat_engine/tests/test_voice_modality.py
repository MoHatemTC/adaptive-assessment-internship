"""`voice` is a modality of its own, graded exactly like `open`.

The distinction is not cosmetic: a report that cannot tell a 35-minute spoken interview
from a typed essay is claiming something it does not know. What must NOT differ is the
grading — same evaluator, same rubric criteria, same competency projection.
"""

from __future__ import annotations

import pytest

from cat_engine.engine.schemas.orchestration import (
    DEFAULT_SECONDS_BY_MODALITY,
    BankItem,
    GradedResponse,
)
from cat_engine.engine.schemas.voice import GradedVoiceResponse
from cat_engine.engine.services.orchestrator.grader import GraderAgent
from cat_engine.engine.services.voice import evaluator as voice_evaluator
from cat_engine.engine.services.voice.grader import grade_voice

ANSWER = (
    "Mutable objects can be changed in place, immutable ones cannot. A list used as a "
    "default argument is created once at definition time, so every call that relies on "
    "the default shares it and it accumulates state. The fix is the None sentinel."
)


@pytest.fixture(scope="module")
def voice_item(aie_bank) -> BankItem:
    return next(i for i in aie_bank.all_items() if i.modality == "voice")


def _graded(item: BankItem, text: str = ANSWER) -> GradedVoiceResponse:
    rubric = voice_evaluator._rubric_from_payload(item)
    package = voice_evaluator.package_from_text(item.item_id, text)
    return GradedVoiceResponse(
        package=package,
        evaluation=voice_evaluator._heuristic_from_rubric(
            item.item_id, rubric, package, flags=["TEST"]
        ),
        rubric=rubric,
    )


def test_a_voice_item_validates_and_exposes_its_payload(voice_item) -> None:
    assert voice_item.modality == "voice"
    assert voice_item.payload is voice_item.voice
    assert voice_item.payload.get("question")


def test_the_grader_routes_voice_to_the_open_path(voice_item) -> None:
    graded = GraderAgent().grade(voice_item, _graded(voice_item))

    assert isinstance(graded, GradedResponse)
    assert graded.modality == "voice"
    assert graded.outcomes
    assert {o["modality"] for o in graded.outcomes} == {"voice"}


def test_voice_and_open_grade_identically(voice_item) -> None:
    """Only the label differs. If the scores ever diverge, one path has drifted."""
    graded = _graded(voice_item)

    as_voice = grade_voice(graded, modality="voice")
    as_open = grade_voice(graded, modality="open")

    assert [o["score"] for o in as_voice.outcomes] == [o["score"] for o in as_open.outcomes]
    assert [o["weight"] for o in as_voice.outcomes] == [o["weight"] for o in as_open.outcomes]
    assert as_voice.modality == "voice"
    assert as_open.modality == "open"


def test_a_voice_item_will_not_grade_raw_text(voice_item) -> None:
    with pytest.raises(TypeError, match="GradedVoiceResponse"):
        GraderAgent().grade(voice_item, "I spoke my answer")


def test_the_rubric_reaches_the_grader_under_either_key_convention(voice_item) -> None:
    """AI Engineer voice items author the checklist and exemplar under other names.

    Without the aliases the grader still returns a grade — a quietly worse one, with no
    answer checklist and no worked exemplar in its prompt.
    """
    rubric = voice_evaluator._rubric_from_payload(voice_item)

    assert rubric["expected_answer_points"], "evaluation_criteria did not reach the rubric"
    assert rubric["reference_answer"], "sample_strong_answer did not reach the rubric"
    assert [c["criterion_id"] for c in rubric["criteria"]] == [
        "technical_accuracy",
        "completeness",
        "reasoning_and_justification",
        "clarity_and_communication",
    ]


class TestExpectedSeconds:
    """Selection divides information by this, so where it comes from matters."""

    def test_the_envelope_wins(self, voice_item) -> None:
        item = voice_item.model_copy(update={"estimated_time_seconds": 123.0})
        assert item.expected_seconds == 123.0

    def test_the_payload_is_the_fallback(self, aie_bank) -> None:
        item = next(i for i in aie_bank.all_items() if i.modality == "code")
        stripped = item.model_copy(update={"estimated_time_seconds": None})
        assert stripped.expected_seconds == float(item.payload["estimated_time_seconds"])

    def test_a_modality_default_covers_an_item_that_states_no_time(self, aie_bank) -> None:
        item = next(i for i in aie_bank.all_items() if i.modality == "mcq")
        assert item.estimated_time_seconds is None
        assert "estimated_time_seconds" not in item.payload
        assert item.expected_seconds == DEFAULT_SECONDS_BY_MODALITY["mcq"]

    def test_no_item_in_any_bank_reports_a_free_question(self, any_profile) -> None:
        from cat_engine.engine.services.orchestrator import registry

        for item in registry.get_bank(any_profile.bank_id).all_items():
            assert item.expected_seconds > 0.0, item.item_id
