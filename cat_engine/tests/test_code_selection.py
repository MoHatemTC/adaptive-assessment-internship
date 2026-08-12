"""Selection fidelity: what the engine offers, and what the model may do with it."""

from __future__ import annotations

import pytest

from cat_engine.engine.services.code_adaptive import scoring
from cat_engine.engine.services.code_adaptive.bank import JsonQuestionRepository
from cat_engine.engine.services.code_adaptive.competency import LearnerModel
from cat_engine.engine.services.code_adaptive.selection import (
    choose,
    competency_weight,
    evaluate_stop,
    filter_candidates,
    rank_candidates,
)
from cat_engine.engine.services.code_adaptive.weights import WeightProfile


@pytest.fixture(scope="module")
def bank() -> list[dict]:
    return [q.model_dump() for q in JsonQuestionRepository().all_questions()]


@pytest.fixture(scope="module")
def target(bank) -> str:
    """The competency with the widest coverage, so ranking has something to choose between."""
    counts: dict[str, int] = {}
    for q in bank:
        for c in q["competencies"]:
            counts[c["competency_id"]] = counts.get(c["competency_id"], 0) + 1
    return max(counts, key=counts.get)


class TestFiltering:
    def test_answered_questions_are_never_offered_again(self, bank, target):
        model = LearnerModel()
        first = filter_candidates(bank, set(), model, target)[0]
        again = filter_candidates(bank, {first["question_id"]}, model, target)
        assert first["question_id"] not in {q["question_id"] for q in again}

    def test_targeting_restricts_the_pool_to_questions_that_assess_it(self, bank, target):
        model = LearnerModel()
        for question in filter_candidates(bank, set(), model, target):
            assert competency_weight(question, target) > 0

    def test_an_unassessed_prerequisite_does_not_block(self, bank, target):
        """On a short test most competencies are unmeasured; blocking on them strands the bank."""
        model = LearnerModel()
        assert filter_candidates(bank, set(), model, target)


class TestRanking:
    def test_every_candidate_is_ranked_and_ordered(self, bank, target):
        model = LearnerModel()
        candidates = filter_candidates(bank, set(), model, target)
        ranked = rank_candidates(candidates, model, target)
        assert len(ranked) == len(candidates)
        assert [c.utility for c in ranked] == sorted((c.utility for c in ranked), reverse=True)

    def test_the_criterion_switches_with_evidence(self, bank, target):
        """KL while the estimate is vague, expected Fisher once it is worth localising."""
        model = LearnerModel()
        candidates = filter_candidates(bank, set(), model, target)

        early = rank_candidates(candidates, model, target)
        assert early[0].signals["criterion"] == "KL"

        state = model.get(target)
        state.alpha, state.beta, state.evidence_count = 5.2, 2.4, 4
        later = rank_candidates(candidates, model, target)
        assert later[0].signals["criterion"] == "E[Fisher]"

    def test_information_is_normalised_so_the_phase_switch_does_not_reweight_it(self, bank, target):
        """KL sums a divergence, Fisher averages a variance — they share no scale.

        Blending either raw against the fixed uncertainty and coverage weights would change
        how much information counts at the moment the criterion switches, altering
        selection for a reason unrelated to any candidate.
        """
        model = LearnerModel()
        candidates = filter_candidates(bank, set(), model, target)
        for state_args in [(1.0, 1.0, 0), (5.2, 2.4, 4)]:
            state = model.get(target)
            state.alpha, state.beta, state.evidence_count = state_args
            ranked = rank_candidates(candidates, model, target)
            values = [c.signals["information_relative"] for c in ranked]
            assert max(values) == pytest.approx(1.0)
            assert all(0.0 <= v <= 1.0 for v in values)


class TestChoosing:
    def test_without_the_model_the_engine_takes_its_own_top_pick(self, bank, target):
        model = LearnerModel()
        ranked = rank_candidates(filter_candidates(bank, set(), model, target), model, target)
        decision = choose(ranked, model, use_llm=False, target_competency=target)
        assert decision.rank == 1
        assert decision.chosen_by_llm is False
        assert decision.normalized_regret == pytest.approx(0.0)

    def test_the_shortlist_bounds_the_worst_case(self, bank, target):
        """Every question the model can reach is one the engine already ranked near-optimal."""
        model = LearnerModel()
        ranked = rank_candidates(filter_candidates(bank, set(), model, target), model, target)
        decision = choose(ranked, model, use_llm=False, target_competency=target)
        assert len(decision.shortlist_ids) <= 5
        assert decision.question["question_id"] in decision.shortlist_ids

    def test_an_empty_pool_returns_nothing_rather_than_raising(self, bank):
        model = LearnerModel()
        assert choose([], model, use_llm=False) is None


class TestStopping:
    def test_the_model_cannot_stop_an_assessment(self, bank, target):
        """evaluate_stop reads only the learner model and the clock. There is no seam here."""
        model = LearnerModel()
        assert evaluate_stop(model, 0, 0.0, 10, target).should_stop is False

    def test_the_question_cap_always_wins(self, bank, target):
        model = LearnerModel()
        stop = evaluate_stop(model, 99, 0.0, 10, target)
        assert stop.should_stop is True
        assert stop.reason == "max_questions"
        assert stop.converged is False

    def test_an_exhausted_bank_stops_without_claiming_convergence(self, bank, target):
        model = LearnerModel()
        stop = evaluate_stop(model, 3, 0.0, 0, target)
        assert stop.should_stop is True
        assert stop.converged is False

    def test_precision_stop_reports_convergence(self, bank, target):
        model = LearnerModel()
        state = model.get(target)
        state.alpha, state.beta, state.evidence_count = 40.0, 15.0, 9
        stop = evaluate_stop(model, 9, 0.0, 5, target)
        assert stop.should_stop is True
        assert stop.converged is True


class TestWeightProfile:
    def test_effective_shares_drop_sources_that_cannot_score_a_criterion(self, bank):
        """tests cannot judge algorithm choice, so a weight there is dead and renormalised.

        Reading the declared table said the model held 30.0% of the score; it held 34.7%.
        """
        profile = WeightProfile.from_preset("B", scoring.SOURCE_WEIGHTS)
        weights = scoring.criterion_weights(bank[0])
        effective = profile.overall_shares(weights)
        assert effective["tests"] > effective["llm"] > effective["static"]
        assert sum(effective.values()) == pytest.approx(1.0, abs=0.01)

    def test_the_fingerprint_tracks_the_weights_and_not_the_label(self, bank):
        preset = WeightProfile.from_preset("B", scoring.SOURCE_WEIGHTS)
        same = WeightProfile.from_preset("B", scoring.SOURCE_WEIGHTS)
        tuned = preset.with_llm_shares({"code_quality": 0.1})
        assert preset.fingerprint() == same.fingerprint()
        assert preset.fingerprint() != tuned.fingerprint()

    def test_zero_share_everywhere_means_no_model_call_is_needed(self, bank):
        profile = WeightProfile.from_preset("B", scoring.SOURCE_WEIGHTS).with_llm_shares(
            {c: 0.0 for c in ["functional_correctness", "edge_case_handling",
                              "algorithm_choice", "code_quality"]}
        )
        assert profile.uses_llm() is False
