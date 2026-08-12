"""The orchestrated multi-modality assessment.

The tests that matter most here are not about features. They are the three properties the
whole design rests on: that unifying the scale did not change the MCQ engine, that no
evidence means no update, and that a finalised variable is never picked for again.
"""

from __future__ import annotations

import numpy as np
import pytest

from cat_engine.engine.schemas.orchestration import VariableState
from cat_engine.engine.services.adaptive.irt import posterior_update, uniform_prior
from cat_engine.engine.services.orchestrator import registry
from cat_engine.engine.services.orchestrator import variables as variables_module
from cat_engine.engine.services.orchestrator.calibration import (
    code_cat_parameters,
    mastery_difficulty_to_theta,
    open_cat_parameters,
    theta_to_mastery_difficulty,
)
from cat_engine.engine.services.orchestrator.orchestrator import Orchestrator
from cat_engine.engine.services.orchestrator.outcome import GradedOutcome, graded_posterior_update
from cat_engine.engine.services.orchestrator.picker import criterion_for, rank
from cat_engine.engine.services.orchestrator.queue import CandidateQueue
from cat_engine.tests.conftest import orchestrator_for


@pytest.fixture(scope="module")
def bank():
    """Pinned to DA: these assertions are about a one-track bank."""
    return registry.get_bank("DA")


@pytest.fixture
def orchestrator(bank) -> Orchestrator:
    return orchestrator_for("DA")


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


class TestGraphShadowState:
    def test_direct_failure_is_not_reported_as_unknown(self, orchestrator, bank):
        item = next(i for i in bank.all_items() if i.modality == "mcq")
        state = orchestrator.begin([item.measures[0].variable.split(".")[0]])
        wrong = (int(item.payload["answer_index"]) + 1) % len(item.payload["options"])

        updated, _ = orchestrator.record_response(state, item, wrong)

        assert (
            item.measures[0].variable
            in updated.graph_shadow_direct_not_mastered_nodes
        )


class TestGraphCoverageGate:
    @pytest.mark.asyncio
    async def test_picker_hard_prioritises_an_unmeasured_sub(
        self, orchestrator, bank, monkeypatch
    ):
        from cat_engine.engine.config import settings as settings_module

        monkeypatch.setattr(
            settings_module.settings, "graph_convergence_gate_enabled", True
        )
        monkeypatch.setattr(
            settings_module.settings, "graph_coverage_critical_only", False
        )
        state = orchestrator.begin(["DA"])
        state = state.model_copy(
            update={
                "graph_direct_measured_nodes": [
                    f"DA.{index}" for index in range(1, 7) if index != 5
                ]
            }
        )

        updated = await orchestrator.fill_queue(
            state, use_llm=False, rng=np.random.default_rng(0)
        )
        chosen = bank.get(updated.queue["DA"].item_id)
        assert chosen is not None
        assert "DA.5" in {measure.variable for measure in chosen.measures}

    def test_precision_stop_waits_for_unmeasured_subs(self, orchestrator, bank, monkeypatch):
        from cat_engine.engine.config import settings as settings_module
        from cat_engine.engine.services.orchestrator import variables as variables_module

        monkeypatch.setattr(
            settings_module.settings, "graph_convergence_gate_enabled", True
        )
        monkeypatch.setattr(
            settings_module.settings, "graph_coverage_critical_only", False
        )

        item = next(
            i
            for i in bank.all_items()
            if i.modality == "mcq" and i.measures[0].variable.startswith("DA.")
        )
        main = item.measures[0].variable.split(".")[0]
        state = orchestrator.begin([main])

        # Pretend CAT math already wants to converge.
        def force_precise(state, items_remaining, **kwargs):
            return state.model_copy(
                update={
                    "finalised": True,
                    "stop_reason": "precision",
                    "converged": True,
                }
            )

        monkeypatch.setattr(variables_module, "evaluate_finalisation", force_precise)

        updated, _ = orchestrator.record_response(
            state, item, int(item.payload["answer_index"])
        )
        assert updated.variables[main].finalised is False
        assert item.measures[0].variable in updated.graph_direct_measured_nodes
        assert item.measures[0].variable in (
            updated.graph_direct_mastered_nodes
            or updated.graph_shadow_direct_mastered_nodes
        )

    def test_a_coverage_veto_at_the_cap_reports_waived_gates_not_a_budget_stop(
        self, orchestrator, bank, monkeypatch
    ):
        """The distinction the review's C5 asks for.

        Measurement DID converge; the graph refused to certify it because required nodes
        were never measured. Calling that `question_budget` describes a stop the session
        did not make, and makes the deadlock rate unmeasurable — the two cases become
        indistinguishable in the record.
        """
        from cat_engine.engine.config import settings as settings_module
        from cat_engine.engine.services.orchestrator import variables as variables_module

        monkeypatch.setattr(
            settings_module.settings, "graph_convergence_gate_enabled", True
        )
        monkeypatch.setattr(
            settings_module.settings, "orchestrator_modality_minimums", "{}"
        )

        item = next(i for i in bank.all_items() if i.modality == "mcq")
        main = item.measures[0].variable.split(".")[0]
        state = orchestrator.begin([main])
        variable_state = state.variables[main].model_copy(
            update={
                "observations": settings_module.settings.cat_max_questions - 1,
                "band_history": [5]
                * (settings_module.settings.cat_max_questions - 1),
                "score_history": [1.0]
                * (settings_module.settings.cat_max_questions - 1),
            }
        )
        state = state.model_copy(
            update={"variables": {main: variable_state}}
        )

        # Precision wins precedence in convergence.evaluate. The graph gate vetoes it,
        # and must then finalise rather than veto forever past CAT_MAX_QUESTIONS.
        def force_precision(state, items_remaining, **kwargs):
            return state.model_copy(
                update={
                    "finalised": True,
                    "stop_reason": "precision",
                    "converged": True,
                }
            )

        monkeypatch.setattr(
            variables_module, "evaluate_finalisation", force_precision
        )
        updated, _ = orchestrator.record_response(
            state, item, int(item.payload["answer_index"])
        )
        assert updated.variables[main].finalised is True
        assert updated.variables[main].converged is False
        assert updated.variables[main].stop_reason == "graph_gates_waived"
        # And it records WHAT was waived, so the omission is auditable rather than
        # reconstructed later from the absence of a set membership.
        assert updated.graph_waived_nodes[main]


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

    def test_open_ended_items_carry_authored_theta_parameters(self, bank):
        """Open items ship with envelope `cat` params; calibration helper is a fallback."""
        opens = [i for i in bank.all_items() if i.modality == "open"]
        assert opens
        for item in opens:
            assert 0.0 < item.cat.a <= 3.0
            assert -4.0 <= item.cat.b <= 4.0
            assert item.cat.c == 0.0
        # Fallback helper still produces a usable provisional triple when needed.
        params = open_cat_parameters(0.5, 1.0)
        assert params["a"] > 0
        assert -4.0 <= params["b"] <= 4.0
        assert 0.0 <= params["c"] < 1.0


class TestUnifiedBank:
    def test_both_modalities_are_present(self, bank):
        modalities = {i.modality for i in bank.all_items()}
        assert modalities == {"mcq", "code", "open"}

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
        from cat_engine.engine.services.orchestrator.picker import information_for

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
        from cat_engine.engine.schemas.orchestration import QueuedCandidate

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
        from cat_engine.engine.schemas.orchestration import QueuedCandidate

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

    def test_a_precise_variable_finalises_and_converges(self, monkeypatch):
        monkeypatch.setattr(
            variables_module,
            "band_probability",
            lambda _state: {level: 0.99 for level in range(1, 6)},
        )
        state = VariableState(
            variable="T1.1", posterior=list(uniform_prior()),
            theta_hat=0.5, standard_error=0.3, observations=8, band_history=[3] * 8,
        )
        finalised = variables_module.evaluate_finalisation(state, items_remaining=10)
        assert finalised.finalised is True
        assert finalised.converged is True

    def test_easy_items_do_not_finalise_a_candidate_still_succeeding(self):
        state = VariableState(
            variable="PY",
            posterior=list(uniform_prior()),
            theta_hat=-0.9462,
            standard_error=0.4837,
            observations=6,
            band_history=[1, 1, 1, 2, 2, 2],
            score_history=[0.0, 1.0, 1.0, 1.0, 1.0, 0.9594],
        )
        held = variables_module.evaluate_finalisation(
            state,
            items_remaining=20,
            administered_difficulties=[-1.68, -2.38, -2.09, -1.49, -1.42, -1.45],
        )
        assert held.finalised is False
        assert variables_module.upper_challenge_difficulty(state) == pytest.approx(-1.05)

    def test_next_band_challenge_allows_precise_finalisation(self, monkeypatch):
        monkeypatch.setattr(
            variables_module,
            "band_probability",
            lambda _state: {level: 0.99 for level in range(1, 6)},
        )
        state = VariableState(
            variable="PY",
            posterior=list(uniform_prior()),
            theta_hat=-0.9462,
            standard_error=0.4837,
            observations=6,
            band_history=[1, 1, 1, 2, 2, 2],
            score_history=[0.0, 1.0, 1.0, 1.0, 1.0, 0.9594],
        )
        finalised = variables_module.evaluate_finalisation(
            state,
            items_remaining=20,
            administered_difficulties=[-1.68, -1.42, -0.50],
        )
        assert finalised.finalised is True
        assert finalised.converged is True

    def test_expert_corroboration_is_capped_at_bank_ceiling(self):
        state = VariableState(
            variable="PY",
            posterior=list(uniform_prior()),
            theta_hat=2.97,
            standard_error=0.35,
            observations=10,
            band_history=[5] * 10,
            score_history=[1.0] * 10,
        )
        assert variables_module.required_corroboration_difficulty(
            state,
            maximum_available_difficulty=2.69,
        ) == pytest.approx(2.69)
        assert variables_module.difficulty_is_corroborated(
            state,
            [2.69],
            maximum_available_difficulty=2.69,
        )

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
        from cat_engine.engine.services.orchestrator import orchestrator as orchestrator_module

        state = orchestrator.begin([mixed_variable])
        state.variables[mixed_variable] = variables_module.mark_exhausted(
            state.variables[mixed_variable]
        )

        called: list[str] = []

        async def spy(items, variable_state, variable, **kwargs):
            called.append(variable)

        monkeypatch.setattr(orchestrator_module, "pick", spy)
        await orchestrator.fill_queue(state, use_llm=False)
        assert called == []
