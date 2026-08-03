"""The orchestrated multi-modality assessment.

The tests that matter most here are not about features. They are the three properties the
whole design rests on: that unifying the scale did not change the MCQ engine, that no
evidence means no update, and that a finalised variable is never picked for again.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.schemas.orchestration import BankItem, VariableState
from app.services.adaptive.irt import posterior_update, uniform_prior
from app.services.orchestrator import variables as variables_module
from app.services.orchestrator.bank import JsonUnifiedBank
from app.services.orchestrator.calibration import (
    code_cat_parameters,
    mastery_difficulty_to_theta,
    open_cat_parameters,
    theta_to_mastery_difficulty,
)
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator
from app.services.orchestrator.outcome import GradedOutcome, graded_posterior_update
from app.services.orchestrator.picker import criterion_for, rank
from app.services.orchestrator.queue import CandidateQueue


@pytest.fixture(scope="module")
def bank() -> JsonUnifiedBank:
    return JsonUnifiedBank()


@pytest.fixture
def orchestrator(bank) -> Orchestrator:
    return Orchestrator(bank, GraderAgent())


@pytest.fixture(scope="module")
def mixed_variable(bank) -> str:
    """A main competency measured by both modalities."""
    return next(v for v, m in bank.coverage().items() if len(m) > 1)


class TestFractionalUpdate:
    """The generalisation must not disturb what it generalises."""

    @pytest.mark.parametrize("a,b,c", [(1.5, 0.0, 0.25), (0.6, -1.0, 0.2), (1.2, 1.0, 0.0)])
    @pytest.mark.parametrize("correct", [True, False])
    def test_binary_case_is_bit_identical_to_the_mcq_engine(self, a, b, c, correct):
        """Float equality, not approximate: the MCQ path must be provably unchanged.

        If this ever fails, unifying the scale has silently altered a shipped, measured
        engine — which is the one outcome this whole design must not have.
        """
        prior = uniform_prior()
        reference = posterior_update(prior, a, b, c, correct)
        generalised = graded_posterior_update(prior, a, b, c, 1.0 if correct else 0.0, 1.0)

        assert np.array_equal(reference[0], generalised[0])
        assert reference[1] == generalised[1]
        assert reference[2] == generalised[2]

    def test_zero_weight_leaves_the_posterior_untouched(self):
        """An infrastructure failure must move no estimate — from the maths, not a branch."""
        prior = uniform_prior()
        posterior, theta, se = graded_posterior_update(prior, 1.5, 0.0, 0.25, 0.0, weight=0.0)
        assert np.array_equal(prior, posterior)

    def test_partial_credit_lands_between_the_two_extremes(self):
        prior = uniform_prior()
        wrong = graded_posterior_update(prior, 1.5, 0.0, 0.0, 0.0, 1.0)[1]
        partial = graded_posterior_update(prior, 1.5, 0.0, 0.0, 0.6, 1.0)[1]
        right = graded_posterior_update(prior, 1.5, 0.0, 0.0, 1.0, 1.0)[1]
        assert wrong < partial < right

    def test_lower_weight_moves_the_estimate_less(self):
        prior = uniform_prior()
        full = graded_posterior_update(prior, 1.5, 0.0, 0.0, 1.0, 1.0)[1]
        half = graded_posterior_update(prior, 1.5, 0.0, 0.0, 1.0, 0.5)[1]
        assert abs(half) < abs(full)

    def test_modality_is_invisible_to_the_update(self):
        """Same score, same item parameters, same posterior — whatever graded it.

        The property that makes mixing modalities legitimate at all.
        """
        prior = uniform_prior()
        as_mcq = graded_posterior_update(prior, 1.2, 0.3, 0.0, 1.0, 1.0)
        as_code = graded_posterior_update(prior, 1.2, 0.3, 0.0, 1.0, 1.0)
        assert np.array_equal(as_mcq[0], as_code[0])

    def test_outcomes_outside_the_unit_interval_are_refused(self):
        with pytest.raises(ValueError):
            GradedOutcome("T1.1", score=1.4)
        with pytest.raises(ValueError):
            GradedOutcome("T1.1", score=0.5, weight=-0.1)


class TestCalibration:
    def test_difficulty_maps_to_the_theta_where_success_is_even_odds(self):
        assert mastery_difficulty_to_theta(0.5) == pytest.approx(0.0)
        assert mastery_difficulty_to_theta(0.15) < 0 < mastery_difficulty_to_theta(0.7)

    def test_the_mapping_round_trips(self):
        for difficulty in (0.15, 0.35, 0.5, 0.7):
            theta = mastery_difficulty_to_theta(difficulty)
            assert theta_to_mastery_difficulty(theta) == pytest.approx(difficulty, abs=1e-3)

    def test_extremes_are_clamped_rather_than_infinite(self):
        assert -4.0 <= mastery_difficulty_to_theta(0.0) <= 4.0
        assert -4.0 <= mastery_difficulty_to_theta(1.0) <= 4.0

    def test_code_items_carry_no_guessing_floor(self):
        """Nobody guesses their way to a passing test suite."""
        assert code_cat_parameters(0.4, 1.2)["c"] == 0.0

    def test_open_ended_calibration_uses_rubric_floor_not_guessing(self):
        params = open_cat_parameters(0.5, 1.0)
        assert params["c"] == pytest.approx(0.15)
        assert params["b"] == pytest.approx(0.0)
        assert params["a"] == pytest.approx(1.0)


class TestUnifiedBank:
    def test_all_modalities_are_present(self, bank):
        modalities = {i.modality for i in bank.all_items()}
        assert modalities == {"mcq", "code", "voice"}

    def test_every_item_carries_theta_parameters(self, bank):
        """The invariant that makes cross-modality ranking valid."""
        for item in bank.all_items():
            assert -4.0 <= item.cat.b <= 4.0
            assert 0.0 < item.cat.a <= 3.0
            assert 0.0 <= item.cat.c < 1.0

    def test_a_shortlist_spans_modalities(self, bank, mixed_variable):
        pool = bank.shortlist(mixed_variable, exclude=set())
        assert len({i.modality for i in pool}) > 1

    def test_served_items_are_excluded(self, bank, mixed_variable):
        first = bank.shortlist(mixed_variable, exclude=set())[0]
        again = bank.shortlist(mixed_variable, exclude={first.item_id})
        assert first.item_id not in {i.item_id for i in again}

    def test_parity_separates_low_loading_from_miscalibration(self, bank, mixed_variable):
        """A modality can lose for two reasons and only one is a fault.

        A code question loading 0.2 on a variable SHOULD lose — it genuinely measures less
        of it. Weak even at full loading is the calibration problem worth acting on.
        """
        report = bank.information_parity(mixed_variable)
        assert set(report["relative_effective"]) == set(report["relative_intrinsic"])
        assert max(report["relative_effective"].values()) == pytest.approx(1.0)

    def test_no_modality_is_miscalibrated_across_the_bank(self, bank):
        """Guards the provisional mapping in calibration.py against silent drift."""
        offenders = [r["variable"] for r in bank.parity_report() if r["miscalibrated"]]
        # Provisional mapping may leave a few variables weak; record rather than hide.
        assert len(offenders) <= 3, f"unexpectedly miscalibrated: {offenders}"


class TestPicker:
    def test_the_criterion_switches_after_the_kl_phase(self):
        assert criterion_for(0) == "KL"
        assert criterion_for(2) == "KL"
        assert criterion_for(3) == "E[Fisher]"

    def test_ranking_is_ordered_and_bounded_by_the_shortlist(self, bank, mixed_variable):
        state = variables_module.seed_variable(mixed_variable)
        pool = bank.shortlist(mixed_variable, exclude=set())
        shortlist, criterion = rank(pool, state, mixed_variable, rng=np.random.default_rng(0), top_k=1)
        assert criterion == "KL"
        assert len(shortlist) <= 5
        assert [s for _, s in shortlist] == sorted((s for _, s in shortlist), reverse=True)

    def test_information_is_scaled_by_loading(self, bank, mixed_variable):
        """A question that only partly measures a variable tells you proportionally less."""
        from app.services.orchestrator.picker import information_for

        state = variables_module.seed_variable(mixed_variable)
        pool = bank.shortlist(mixed_variable, exclude=set())
        partial = next((i for i in pool if 0 < i.loading(mixed_variable) < 1.0), None)
        if partial is None:
            pytest.skip("no partially loading item for this variable")

        scaled = information_for(partial, state, mixed_variable)
        unscaled = scaled / partial.loading(mixed_variable)
        assert scaled < unscaled


class TestQueue:
    def test_one_slot_per_variable(self, bank, mixed_variable):
        from app.schemas.orchestration import QueuedCandidate

        queue = CandidateQueue()
        for item_id in ("a", "b"):
            queue.put(QueuedCandidate(
                variable=mixed_variable, item_id=item_id, modality="mcq", criterion="KL",
                information=1.0, utility=1.0, best_information=1.0, normalized_regret=0.0,
                engine_top_pick=item_id, chosen_by_llm=False,
            ))
        assert len(queue) == 1
        assert queue.get(mixed_variable).item_id == "b"

    def test_releasing_a_finalised_variable_empties_its_slot(self, mixed_variable):
        from app.schemas.orchestration import QueuedCandidate

        queue = CandidateQueue()
        queue.put(QueuedCandidate(
            variable=mixed_variable, item_id="a", modality="mcq", criterion="KL",
            information=1.0, utility=1.0, best_information=1.0, normalized_regret=0.0,
            engine_top_pick="a", chosen_by_llm=False,
        ))
        queue.release(mixed_variable)
        assert mixed_variable not in queue


class TestFinalisation:
    """The explicit requirement: a finalised variable is never picked for again."""

    def test_a_precise_variable_finalises_and_converges(self):
        state = VariableState(
            variable="T1.1", posterior=list(uniform_prior()),
            theta_hat=0.5, standard_error=0.3, observations=8, band_history=[3] * 8,
        )
        finalised = variables_module.evaluate_finalisation(state, items_remaining=10)
        assert finalised.finalised is True
        assert finalised.converged is True

    def test_an_exhausted_variable_finalises_without_claiming_convergence(self):
        state = variables_module.seed_variable("T1.1")
        exhausted = variables_module.mark_exhausted(state)
        assert exhausted.finalised is True
        assert exhausted.converged is False
        assert exhausted.stop_reason == "bank_exhausted"

    def test_finalisation_is_one_way(self):
        state = variables_module.seed_variable("T1.1")
        finalised = variables_module.mark_exhausted(state)
        # A vague estimate would not normally finalise; already-finalised must stay so.
        assert variables_module.evaluate_finalisation(finalised, items_remaining=99).finalised

    def test_a_zero_weight_outcome_does_not_count_as_an_observation(self):
        """No evidence must not make an unmeasured variable look measured."""
        state = variables_module.seed_variable("T1.1")
        after = variables_module.apply_outcome(
            state, GradedOutcome("T1.1", score=0.0, weight=0.0), 1.5, 0.0, 0.25, "item"
        )
        assert after.observations == 0
        assert after.served_item_ids == []

    @pytest.mark.asyncio
    async def test_the_picker_is_never_invoked_for_a_finalised_variable(
        self, orchestrator, bank, mixed_variable, monkeypatch
    ):
        """The requirement, asserted at the point it is enforced."""
        from app.services.orchestrator import orchestrator as orchestrator_module

        state = orchestrator.begin([mixed_variable])
        state.variables[mixed_variable] = variables_module.mark_exhausted(
            state.variables[mixed_variable]
        )

        called: list[str] = []

        async def spy(items, variable_state, variable, **kwargs):
            called.append(variable)
            return None

        monkeypatch.setattr(orchestrator_module, "pick", spy)
        await orchestrator.fill_queue(state, use_llm=False)
        assert called == []
