"""`cat_aberrance_drives_verification`: what it does, and what it must never do.

WHAT THE SETTING IS FOR

`personfit` computes a posterior-predictive residual for every response and flags the ones
the current posterior did not expect. That flag was recorded in the session, counted in the
report — and read by nothing. The setting that claimed to act on it was declared,
documented, shipped, and consulted by no branch.

The failure it addresses is measurable. A simulated candidate at true theta +1.8 with a 20%
slip rate produced, in 4 of 25 sessions, a report whose 95% credible interval did not contain
the true ability WHILE carrying a recorded aberrance that nothing consulted. The engine had
noticed and had no way to say so.

WHAT IT DOES

A competency with ANY aberrant response recorded against it may not take a measurement stop.
It still takes every budget stop, so the cost is bounded by the question budget — measured at
0.15 of an item per session, because most sessions record no aberrance at all.

THE LINE IT MUST NOT CROSS

`personfit`'s contract is that it flags and never scores. Delaying a stop is a stopping
decision; rescoring is not. So the tests below pin both halves: the gate blocks convergence,
and the estimate is bit-for-bit what it would have been anyway.

AND IT IS OFF BY DEFAULT, so every number this engine has ever produced is unchanged. That is
asserted here rather than assumed, because "default off" is the claim the whole change rests on.
"""

from __future__ import annotations

import numpy as np
import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.schemas.orchestration import VariableState
from cat_engine.engine.services.adaptive import convergence
from cat_engine.engine.services.adaptive.irt import THETA_GRID
from cat_engine.engine.services.orchestrator.orchestrator import _verification_pending


def concentrated_posterior(centre: float = 0.0, sd: float = 0.3) -> list[float]:
    """Mass in one band, so `band_probability` clears its target and a stop can fire.

    A uniform prior spreads across all five bands and converges under no rule at all, which
    would make the "gate on vs gate off" comparison below vacuous — both arms would stay
    open for reasons having nothing to do with the gate.
    """
    density = np.exp(-0.5 * ((THETA_GRID - centre) / sd) ** 2)
    return list(density / density.sum())

CONVERGED_ARGS = {
    "standard_error": 0.30,
    "band_history": [3, 3, 3, 3],
    "questions_answered": 10,
    "items_remaining": 40,
    "difficulty_corroborated": True,
    "band_probability": 0.95,
}


def variable_state(**over) -> VariableState:
    base = {
        "variable": "DA",
        "posterior": concentrated_posterior(),
        "theta_hat": 0.0,
        "standard_error": 0.3,
        "observations": 4,
        "served_item_ids": ["Q1", "Q2", "Q3"],
    }
    base.update(over)
    return VariableState(**base)


def aberrance(variable="DA", item_id="Q3") -> list[dict]:
    return [{"variable": variable, "item_id": item_id, "z": 2.6}]


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "cat_aberrance_drives_verification", True)


class TestTheDefaultIsOff:
    """The claim the whole change rests on."""

    def test_the_setting_ships_disabled(self):
        assert settings.cat_aberrance_drives_verification is False

    def test_with_it_off_an_aberrant_last_response_pends_nothing(self):
        assert _verification_pending("DA", variable_state(), aberrance()) is False

    def test_with_it_off_a_converged_variable_still_stops(self):
        assert convergence.evaluate(**CONVERGED_ARGS).should_stop is True


class TestWhatPendsAndWhatDoesNot:
    def test_an_aberrant_last_response_pends_verification(self, flag_on):
        assert _verification_pending("DA", variable_state(), aberrance()) is True

    def test_an_aberrance_EARLIER_in_the_session_also_pends(self, flag_on):
        """The anchoring choice, and the measurement that settled it.

        Written first as "only the most recent response counts", on the reasoning that a
        variable surprising at item two and steady since has been verified by what followed.
        Measured over 100 simulated sessions, that removed one bad certification in a
        hundred — because the failure the gate exists for is an EARLY run of bad luck, where
        theta crashes over the first few items and the session stops later on a response
        that is no longer surprising.

        Counting any aberrance removes five in a hundred and costs 0.15 of an item per
        session. This test is that decision, so reverting to last-item-only fails here
        rather than quietly halving the gate's value.
        """
        assert (
            _verification_pending("DA", variable_state(), aberrance(item_id="Q1"))
            is True
        )

    def test_another_variables_aberrance_does_not_pend_this_one(self, flag_on):
        """Two competencies in one session are two measurements."""
        assert (
            _verification_pending("DA", variable_state(), aberrance(variable="C3"))
            is False
        )

    def test_a_variable_with_no_responses_yet_pends_nothing(self, flag_on):
        assert _verification_pending("DA", variable_state(served_item_ids=[]), []) is False

    def test_no_aberrance_at_all_pends_nothing(self, flag_on):
        assert _verification_pending("DA", variable_state(), []) is False


class TestTheGateBlocksMeasurementStopsAndOnlyThose:
    def test_it_blocks_a_band_probability_stop(self):
        """The stop that fires in the shipped configuration."""
        assert convergence.evaluate(**CONVERGED_ARGS).reason == "band_probability"
        held = convergence.evaluate(**CONVERGED_ARGS, verification_pending=True)
        assert held.should_stop is False
        assert held.converged is False

    def test_it_blocks_precision_and_stable_band_in_additive_mode(self, monkeypatch):
        """The other two branches, reachable only with the conjunction off."""
        monkeypatch.setattr(
            settings, "cat_band_probability_stop_conjunctive", False
        )
        args = {**CONVERGED_ARGS, "band_probability": 0.1}
        assert convergence.evaluate(**args).reason in ("precision", "stable_band")
        assert (
            convergence.evaluate(**args, verification_pending=True).should_stop is False
        )

    def test_it_does_NOT_block_the_question_budget(self, monkeypatch):
        """A pending verification must not make a session run past its budget.

        The candidate's time is bounded by the budget whatever the residuals say; what the
        gate buys is that the report says `question_budget` and `converged=False` instead of
        claiming a level it had reason to doubt.
        """
        monkeypatch.setattr(settings, "cat_max_questions", 10)
        stop = convergence.evaluate(
            **{**CONVERGED_ARGS, "questions_answered": 10},
            verification_pending=True,
        )
        assert stop.should_stop is True
        assert stop.reason == "question_budget"
        assert stop.converged is False

    def test_it_does_NOT_block_bank_exhaustion(self):
        stop = convergence.evaluate(
            **{**CONVERGED_ARGS, "items_remaining": 0}, verification_pending=True
        )
        assert stop.should_stop is True
        assert stop.reason == "bank_exhausted"
        assert stop.converged is False


class TestItGatesStoppingAndNeverScoring:
    def test_the_estimate_is_untouched_by_a_pending_verification(self):
        """`personfit` flags and never scores, and this must not be the exception.

        Same state, same arithmetic, gate on and off — the only difference permitted is
        whether the competency is allowed to declare itself finished.
        """
        from cat_engine.engine.services.orchestrator import (
            variables as variables_module,
        )

        state = variable_state(observations=10, band_history=[3, 3, 3, 3])
        free = variables_module.evaluate_finalisation(state, 40)
        held = variables_module.evaluate_finalisation(
            state, 40, verification_pending=True
        )
        assert held.theta_hat == free.theta_hat
        assert held.standard_error == free.standard_error
        assert np.array_equal(np.array(held.posterior), np.array(free.posterior))
        assert held.observations == free.observations
        assert free.finalised is True and held.finalised is False


class TestTheSettingsAreInTheFingerprint:
    def test_both_are_hashed(self):
        """Two deployments that disagree about either do not mean the same thing by
        `converged`, which is the test for belonging in the fingerprint."""
        from cat_engine.engine.config.fingerprint import MEASUREMENT_SETTINGS

        assert "cat_aberrance_drives_verification" in MEASUREMENT_SETTINGS
        assert "cat_aberrant_residual_threshold" in MEASUREMENT_SETTINGS

    def test_turning_it_on_changes_the_fingerprint(self, monkeypatch):
        from cat_engine.engine.config.fingerprint import engine_config_fingerprint

        before = engine_config_fingerprint()
        monkeypatch.setattr(settings, "cat_aberrance_drives_verification", True)
        assert engine_config_fingerprint() != before
