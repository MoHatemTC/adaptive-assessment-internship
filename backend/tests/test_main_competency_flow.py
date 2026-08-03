"""Main-competency rollup and queue behaviour."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.orchestrator.bank import JsonUnifiedBank
from app.services.orchestrator.competency import (
    affected_mains,
    main_competency,
    rollup_outcomes,
)
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator
from app.services.orchestrator.outcome import GradedOutcome


@pytest.fixture(scope="module")
def bank() -> JsonUnifiedBank:
    return JsonUnifiedBank()


@pytest.fixture
def orchestrator(bank) -> Orchestrator:
    return Orchestrator(bank, GraderAgent())


def test_main_competency_prefix():
    assert main_competency("T1.3") == "T1"
    assert main_competency("T2") == "T2"


def test_rollup_combines_sub_competencies_under_one_main():
    raw = [
        GradedOutcome("T1.1", score=1.0, weight=0.8).__dict__,
        GradedOutcome("T1.2", score=0.0, weight=0.6).__dict__,
    ]
    rolled = rollup_outcomes(raw, {"T1"})
    assert len(rolled) == 1
    assert rolled[0].variable == "T1"
    assert rolled[0].score == pytest.approx(0.8 / 1.4)


class TestPresentingQueue:
    @pytest.mark.asyncio
    async def test_presenting_competency_is_absent_from_queue(self, orchestrator, bank):
        tracks = [t["code"] for t in bank.tracks()]
        if len(tracks) < 1:
            pytest.skip("bank has no main competencies")
        # Prefer two mains when available; otherwise exercise the single-track bank.
        chosen = tracks[:2] if len(tracks) >= 2 else tracks
        state = orchestrator.begin(chosen)
        state = await orchestrator.fill_queue(state, use_llm=False, rng=np.random.default_rng(0))
        state = orchestrator.ensure_presenting(state)
        assert state.presenting is not None
        assert state.presenting.variable not in state.queue

    @pytest.mark.asyncio
    async def test_multi_competency_answer_clears_both_queue_slots(self, bank):
        from app.services.code_adaptive.bank import JsonQuestionRepository
        from app.services.code_adaptive.session import CodeAdaptiveSession

        orchestrator = Orchestrator(
            bank, GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
        )
        cross = next(
            (
                i for i in bank.all_items()
                if len({main_competency(m.variable) for m in i.measures}) > 1
            ),
            None,
        )
        if cross is None:
            pytest.skip("no item in the bank measures more than one main competency")
        mains = sorted({main_competency(m.variable) for m in cross.measures})[:2]
        state = orchestrator.begin(mains)
        state = await orchestrator.fill_queue(state, use_llm=False, rng=np.random.default_rng(1))
        state = orchestrator.ensure_presenting(state)
        item, _ = orchestrator.next_item(state)
        assert item is not None
        if item.modality == "mcq":
            response = int(item.payload["answer_index"])
        else:
            response = "def f():\n    pass\n"
        state, _ = orchestrator.record_response(state, item, response)
        touched = affected_mains(item, set(mains))
        assert not (touched & set(state.queue))
