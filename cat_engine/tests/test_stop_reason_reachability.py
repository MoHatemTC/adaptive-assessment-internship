"""Which stopping rules can actually fire, and under which configuration.

WHY THIS FILE EXISTS

`convergence.evaluate` documents four ways a competency can end. In the shipped
configuration only two of them are reachable: `cat_band_probability_stop_conjunctive`
defaults to True, and the `precision` and `stable_band` branches are guarded by
`not conjunctive`.

That is not a bug — the conjunction is deliberate, and the docstring explains why a
mass-in-one-band claim is not the same claim as a precise estimate. But it was written
nowhere, and it has two consequences that bite in opposite directions:

    Someone reads the four rules and reasons about a session as though `precision` might
    have fired. It cannot.

    Someone greps for `stable_band`, finds no session that ever produced one, concludes the
    branch is dead, and deletes it — removing the additive mode that is the way back from
    the conjunction.

So both arms are pinned here. Across 125 simulated competency estimates the observed
distribution was 83 `band_probability` and 42 `question_budget`, and neither of the other
two once; these tests are the reason that stays a fact about configuration rather than a
coincidence nobody checked.
"""

from __future__ import annotations

import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.adaptive import convergence
from cat_engine.engine.services.adaptive.convergence import StopReason

#: Precise, corroborated, band-stable and confident — every measurement rule would fire.
ALL_RULES_SATISFIED = {
    "standard_error": 0.30,
    "band_history": [3, 3, 3, 3],
    "questions_answered": 10,
    "items_remaining": 40,
    "difficulty_corroborated": True,
}


@pytest.fixture
def additive(monkeypatch):
    monkeypatch.setattr(settings, "cat_band_probability_stop_conjunctive", False)


class TestTheShippedConfiguration:
    def test_the_conjunction_is_the_default(self):
        assert settings.cat_band_probability_stop_enabled is True
        assert settings.cat_band_probability_stop_conjunctive is True

    def test_a_measurement_stop_is_always_band_probability(self):
        stop = convergence.evaluate(**ALL_RULES_SATISFIED, band_probability=0.95)
        assert stop.reason == StopReason.BAND_PROBABILITY
        assert stop.converged is True

    @pytest.mark.parametrize("band_probability", [0.0, 0.5, 0.79])
    def test_below_the_band_target_nothing_converges_however_precise(
        self, band_probability
    ):
        """The point of the conjunction, and the reason the other two are unreachable.

        SE 0.30 is well under target and the band has been stable for four items — both
        `precision` and `stable_band` would fire in additive mode. Under the conjunction the
        competency keeps going, because concentrated mass is the claim being made and this
        estimate does not have it.
        """
        stop = convergence.evaluate(
            **ALL_RULES_SATISFIED, band_probability=band_probability
        )
        assert stop.should_stop is False
        assert stop.reason == StopReason.IN_PROGRESS

    def test_the_only_other_outcome_is_a_budget_stop(self, monkeypatch):
        monkeypatch.setattr(settings, "cat_max_questions", 10)
        stop = convergence.evaluate(
            **{**ALL_RULES_SATISFIED, "questions_answered": 10}, band_probability=0.1
        )
        assert stop.reason == StopReason.QUESTION_BUDGET
        assert stop.converged is False


class TestAdditiveModeIsTheWayBack:
    """Why the two branches are kept rather than deleted as dead code."""

    def test_precision_becomes_reachable(self, additive):
        stop = convergence.evaluate(**ALL_RULES_SATISFIED, band_probability=0.1)
        assert stop.reason == StopReason.PRECISION
        assert stop.converged is True

    def test_stable_band_becomes_reachable(self, additive, monkeypatch):
        """Only below the precision target, since precision is checked first."""
        monkeypatch.setattr(settings, "cat_se_target", 0.20)
        monkeypatch.setattr(settings, "cat_stability_se_ceiling", 0.55)
        stop = convergence.evaluate(
            **{**ALL_RULES_SATISFIED, "standard_error": 0.40}, band_probability=0.1
        )
        assert stop.reason == StopReason.STABLE_BAND
        assert stop.converged is True

    def test_band_probability_still_fires_first_when_it_can(self, additive):
        stop = convergence.evaluate(**ALL_RULES_SATISFIED, band_probability=0.95)
        assert stop.reason == StopReason.BAND_PROBABILITY


class TestEveryReasonIsNamed:
    def test_no_stop_is_ever_the_empty_string(self):
        """A blank is not a stop reason; it is the absence of one, and it used to be the
        most common value in the report."""
        for band_probability in (0.0, 0.5, 0.95):
            reason = convergence.evaluate(
                **ALL_RULES_SATISFIED, band_probability=band_probability
            ).reason
            assert reason
            assert reason in set(StopReason)
