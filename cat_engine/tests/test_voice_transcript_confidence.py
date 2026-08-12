"""Live packages must not invent ASR confidence scores."""

from __future__ import annotations

from cat_engine.engine.schemas.voice import VoiceEvaluation, VoiceResponsePackage, VoiceTurn
from cat_engine.engine.services.voice.evidence import evidence_strength
from cat_engine.engine.services.voice_live.gemini_live import LiveInterviewResult


def test_live_package_leaves_transcript_confidence_unknown_when_unset() -> None:
    result = LiveInterviewResult(
        item_id="open_py_001",
        transcript="Lists are mutable; tuples are not.",
        turns=[
            {
                "turn_id": "c1",
                "role": "candidate",
                "text": "Lists are mutable; tuples are not.",
                "transcript_confidence": None,
            }
        ],
        total_speech_seconds=12.0,
    )
    package = result.as_package()
    assert package.mean_transcript_confidence is None
    assert package.turns[0].transcript_confidence is None


def test_unknown_transcript_confidence_does_not_crush_evidence_strength() -> None:
    package = VoiceResponsePackage(
        item_id="open_py_001",
        turns=[
            VoiceTurn(
                turn_id="c1",
                role="candidate",
                text="A shared iterator is why the grouper idiom chunks consecutively.",
            )
        ],
        total_speech_seconds=20.0,
        mean_transcript_confidence=None,
        word_count=12,
        live_text="A shared iterator is why the grouper idiom chunks consecutively.",
        final_text="A shared iterator is why the grouper idiom chunks consecutively.",
    )
    evaluation = VoiceEvaluation(
        item_id="open_py_001",
        rubric_id="inline",
        evaluation_confidence=0.9,
    )
    assert evidence_strength(package, evaluation) == 1.0
