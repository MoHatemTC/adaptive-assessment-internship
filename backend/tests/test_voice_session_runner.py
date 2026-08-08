"""VoiceAssessmentRunner integration without network or model calls."""

from __future__ import annotations

from typing import cast

import numpy as np
import pytest

from app.schemas.orchestration import GradedResponse
from app.services.voice.session import VoiceAssessmentRunner


@pytest.mark.asyncio
async def test_runner_submits_a_transcript_and_advances_the_assessment(aie_bank) -> None:
    runner = VoiceAssessmentRunner(bank=aie_bank, bank_id="AIE", seed=17)
    state = runner.begin(["C1"], intake={"C1": 3})
    voice_item = next(
        item
        for item in aie_bank.shortlist("C1", exclude=set())
        if item.modality == "voice"
    )
    candidate = next(iter((await runner.fill_queue(state, use_llm=False, seed=4)).queue.values()))
    state = state.model_copy(
        update={
            "queue": {
                "C1": candidate.model_copy(
                    update={
                        "item_id": voice_item.item_id,
                        "modality": voice_item.modality,
                        "estimated_seconds": voice_item.expected_seconds,
                    }
                )
            }
        }
    )
    state = runner.ensure_presenting(state)

    updated, graded_voice, graded = await runner.submit_text(
        state,
        voice_item,
        (
            "I would define the interface, validate all model output, log decisions, "
            "and use a deterministic fallback when the provider is unavailable."
        ),
        use_llm=False,
    )

    assert graded_voice.package.item_id == voice_item.item_id
    graded_response = cast(GradedResponse, graded)
    assert graded_response.item_id == voice_item.item_id
    assert voice_item.item_id in updated.served_item_ids
    assert updated.items_administered == 1
    assert updated.variables["C1"].observations >= 1


@pytest.mark.asyncio
async def test_runner_reuses_its_rng_unless_a_call_seed_is_explicit(aie_bank, monkeypatch) -> None:
    runner = VoiceAssessmentRunner(bank=aie_bank, bank_id="AIE", seed=23)
    state = runner.begin(["C1"])
    seen: list[np.random.Generator] = []

    async def fake_fill(state, *, use_llm, rng):
        seen.append(rng)
        return state

    monkeypatch.setattr(runner.orchestrator, "fill_queue", fake_fill)

    await runner.fill_queue(state, use_llm=False)
    await runner.fill_queue(state, use_llm=False)
    await runner.fill_queue(state, use_llm=False, seed=99)

    assert seen[0] is seen[1] is runner._rng
    assert seen[2] is not runner._rng


def test_runner_time_limit_uses_the_session_start(monkeypatch, aie_bank) -> None:
    from app.services.voice import session as session_module

    now = 10_000.0
    monkeypatch.setattr(session_module.time, "time", lambda: now)
    runner = VoiceAssessmentRunner(bank=aie_bank, bank_id="AIE")
    state = runner.begin(["C1"])
    monkeypatch.setattr(
        session_module.time,
        "time",
        lambda: now + 60.0 * session_module.voice_settings.voice_session_time_limit_minutes,
    )

    assert runner.time_remaining_minutes() == 0.0
    assert runner.should_stop(state) == (True, "time_limit")
