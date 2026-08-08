"""Main-competency rollup and queue behaviour."""

from __future__ import annotations

import numpy as np
import pytest

from app.schemas.orchestration import QueuedCandidate
from app.services.orchestrator import registry
from app.services.orchestrator.competency import (
    affected_mains,
    combined_weight,
    main_competency,
    rollup_outcomes,
)
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator
from app.services.orchestrator.outcome import GradedOutcome
from tests.conftest import orchestrator_for


@pytest.fixture(scope="module")
def bank():
    return registry.get_bank("DA")


@pytest.fixture
def orchestrator(bank) -> Orchestrator:
    return orchestrator_for("DA")


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


def test_cross_main_evidence_does_not_claim_another_mains_modality():
    """One question can update two posteriors but is administered for only one main."""
    bank = registry.get_bank("AIE")
    orchestrator = orchestrator_for("AIE")
    item = next(
        item
        for item in bank.all_items()
        if item.modality == "mcq"
        and len({main_competency(m.variable) for m in item.measures}) > 1
    )
    mains = sorted({main_competency(m.variable) for m in item.measures})[:2]
    primary, secondary = mains
    state = orchestrator.begin(mains).model_copy(
        update={
            "presenting": QueuedCandidate(
                variable=primary,
                item_id=item.item_id,
                modality=item.modality,
                criterion="KL",
                information=1.0,
                utility=1.0,
                best_information=1.0,
                normalized_regret=0.0,
                engine_top_pick=item.item_id,
                chosen_by_llm=False,
            )
        }
    )

    updated, _ = orchestrator.record_response(
        state, item, int(item.payload["answer_index"])
    )

    assert item.item_id in updated.administered_by_variable[primary]
    assert item.item_id not in updated.administered_by_variable.get(secondary, [])


class TestCombinedWeight:
    """One response carries one response's worth of evidence about a main.

    It used to be `min(1, sum(w_i))`, which made an item measuring two sub-competencies at
    0.70 and 0.65 count for exactly as much as a flawless full-credit multiple-choice
    answer — and past the cap stopped distinguishing anything at all.
    """

    def test_two_partial_measurements_never_outweigh_one_whole_one(self):
        raw = [
            GradedOutcome("T1.1", score=1.0, weight=0.70).__dict__,
            GradedOutcome("T1.2", score=1.0, weight=0.65).__dict__,
        ]
        rolled = rollup_outcomes(raw, {"T1"})
        single_node = rollup_outcomes(
            [GradedOutcome("T1.1", score=1.0, weight=1.0).__dict__], {"T1"}
        )

        assert rolled[0].weight == pytest.approx(0.895)
        assert rolled[0].weight < single_node[0].weight

    def test_measuring_more_still_counts_for_more(self):
        """Monotone: a second sub-competency adds evidence, it does not merely cap."""
        one = combined_weight([0.70])
        two = combined_weight([0.70, 0.65])
        three = combined_weight([0.70, 0.65, 0.40])

        assert one < two < three < 1.0

    def test_a_single_outcome_is_unchanged(self):
        """Every multiple-choice item measures one node, and must update exactly as before."""
        for weight in (0.25, 0.7, 1.0):
            assert combined_weight([weight]) == pytest.approx(weight)

    def test_it_is_bounded_by_one(self):
        assert combined_weight([1.0, 0.5]) == pytest.approx(1.0)
        assert combined_weight([0.9] * 8) <= 1.0

    def test_an_unscorable_outcome_contributes_nothing(self):
        assert combined_weight([0.6, 0.0]) == pytest.approx(0.6)


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

        orchestrator = orchestrator_for(
            "DA", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
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
