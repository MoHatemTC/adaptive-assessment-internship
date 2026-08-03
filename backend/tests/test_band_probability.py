"""What the report says about a level, and how likely that level is to be right.

The review's T5: at SE 0.55 the certainty scale reports 90 while the probability that the
reported band is the true band is about 0.64 at a band centre. Both are honest numbers;
only one of them answers the question a reader is actually asking.

The bands are 1.6 logits wide, which is what earns that 0.85. Five LABELS over 1.0-wide
bands — what this codebase had — were as narrow as the eight-level scale the review was
criticising, and bought none of the honesty five bands were supposed to.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.config.settings import settings
from app.services.adaptive import convergence, personfit
from app.services.adaptive.irt import THETA_GRID, ability_band, band_probabilities
from app.services.orchestrator import variables as variables_module


def gaussian(mu: float, sd: float) -> np.ndarray:
    density = np.exp(-0.5 * ((THETA_GRID - mu) / sd) ** 2)
    return density / density.sum()


class TestBandProbabilities:
    def test_they_sum_to_one(self) -> None:
        assert sum(band_probabilities(gaussian(0.0, 0.55)).values()) == pytest.approx(1.0)

    def test_every_grid_point_is_assigned_the_band_ability_band_would_report(self) -> None:
        """One rule, so the reported level and its probability cannot disagree."""
        for theta in THETA_GRID:
            point = np.zeros_like(THETA_GRID)
            point[np.argmin(np.abs(THETA_GRID - theta))] = 1.0
            level, _label = ability_band(float(theta))
            assert band_probabilities(point)[level] == pytest.approx(1.0)

    def test_the_reported_certainty_overstates_the_chance_the_level_is_right(self) -> None:
        """The number the review computed, reproduced on this repo's own scale."""
        at_target = band_probabilities(gaussian(0.0, settings.cat_se_target))

        assert max(at_target.values()) == pytest.approx(0.850, abs=0.01)
        # ...while the certainty scale reads exactly 90 at the same standard error.
        assert convergence.measurement_certainty_pct(settings.cat_se_target) == pytest.approx(
            90.0
        )

    def test_a_candidate_near_a_cut_point_needs_more_evidence_not_infinite_evidence(
        self,
    ) -> None:
        """Near a boundary the level is genuinely less certain — and it does resolve.

        Under the previous banker's-rounding scheme a candidate at the midpoint between
        two bands capped at P = 0.50 no matter how precisely they were measured, because
        an exact half tied down on one side and up on the other. `np.digitize` is
        consistently right-open, so extra evidence buys certainty everywhere. Requiring
        more of it near a cut point is a stop rule working, not a stop rule stuck.
        """
        loose = max(band_probabilities(gaussian(0.8, 0.55)).values())
        precise = max(band_probabilities(gaussian(0.8, 0.06)).values())

        assert loose < max(band_probabilities(gaussian(0.0, 0.55)).values())
        assert precise > 0.95

    def test_precision_raises_it(self) -> None:
        loose = max(band_probabilities(gaussian(0.0, 0.55)).values())
        tight = max(band_probabilities(gaussian(0.0, 0.30)).values())
        assert tight > loose


class TestBandProbabilityStop:
    def test_it_is_off_by_default(self) -> None:
        assert settings.cat_band_probability_stop_enabled is False
        decision = convergence.evaluate(
            0.90, [4] * 8, 8, items_remaining=10, band_probability=0.99
        )
        assert decision.reason != "band_probability"

    def test_enabled_it_can_end_a_competency_early(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "cat_band_probability_stop_enabled", True)
        decision = convergence.evaluate(
            0.90, [4] * 8, 8, items_remaining=10, band_probability=0.95
        )
        assert decision.should_stop is True
        assert decision.reason == "band_probability"
        assert decision.converged is True

    def test_it_still_respects_the_observation_floor(self, monkeypatch) -> None:
        """A prior is not a measurement, however concentrated it looks.

        The floor is what stops a self-rating being certified as an assessment. Wider
        bands plus a flatter prior mean a confident self-rater no longer reaches the
        stop threshold from their rating alone — but the floor is the thing that
        guarantees it, not the arithmetic happening to fall the right way.
        """
        prior = variables_module.seed_variable("DA", 5, True)
        assert max(variables_module.band_probability(prior).values()) < 0.80

        monkeypatch.setattr(settings, "cat_band_probability_stop_enabled", True)
        decision = convergence.evaluate(
            1.10, [], 0, items_remaining=10, band_probability=0.95
        )
        assert decision.reason != "band_probability"

    def test_it_never_holds_a_competency_open(self, monkeypatch) -> None:
        """Additive, not a veto: a precision stop still fires when P(band) is low."""
        monkeypatch.setattr(settings, "cat_band_probability_stop_enabled", True)
        decision = convergence.evaluate(
            0.30, [4] * 8, 8, items_remaining=10, band_probability=0.10
        )
        assert decision.reason == "precision"


class TestPriorDiscipline:
    def test_a_confident_self_rating_no_longer_moves_the_estimate_half_a_band(self) -> None:
        """Review A9/T7, measured on this module's own update.

        A candidate whose true ability is 0 but who self-rates 5, answering six items at
        their true response probability. Bands are 1.0 logits wide and the nearest
        boundary is 0.5 away, so a residual pull of 0.42 — what SD 1.1 produced — re-bands
        anyone within 0.42 of a cut point, in the direction they rated themselves, at the
        FULL observation floor rather than as an early-session artefact.
        """
        from app.services.adaptive.irt import probability_correct
        from app.services.orchestrator.outcome import GradedOutcome

        def bias_after_six(prior_sd: float) -> float:
            state = variables_module.seed_variable("DA", 5, True)
            if prior_sd != variables_module.PRIOR_SD_CONFIDENT:
                from app.services.adaptive.irt import prior_from_self_rating

                posterior = prior_from_self_rating(5, sd=prior_sd)
                state = state.model_copy(update={"posterior": posterior.tolist()})
            for index, b in enumerate([-0.5, -0.3, -0.1, 0.1, 0.3, 0.5]):
                # Scored at the TRUE response probability for theta = 0.
                score = float(probability_correct(0.0, 1.7, b, 0.09))
                state = variables_module.apply_outcome(
                    state,
                    GradedOutcome("DA", score=score, weight=1.0),
                    1.7,
                    b,
                    0.09,
                    f"item{index}",
                )
            return state.theta_hat

        at_current = abs(bias_after_six(variables_module.PRIOR_SD_CONFIDENT))
        at_old = abs(bias_after_six(1.1))

        assert variables_module.PRIOR_SD_CONFIDENT >= 1.5
        assert at_current < at_old, "flattening the prior must reduce the self-rating pull"
        assert at_current < 0.42, (
            "the self-rating still moves the estimate more than 42% of a band width"
        )

    def test_every_prior_is_at_least_as_flat_as_the_review_requires(self) -> None:
        assert variables_module.PRIOR_SD_CONFIDENT >= 1.5
        assert variables_module.PRIOR_SD_TENTATIVE >= 1.5
        assert variables_module.PRIOR_SD_NO_RATING >= 1.5

    def test_a_mid_self_rating_is_still_symmetric(self) -> None:
        """The rating that carries no directional claim must not create one."""
        neutral = variables_module.seed_variable("DA", 3, True)
        assert neutral.theta_hat == pytest.approx(0.0, abs=1e-9)


class TestPersonFit:
    def test_it_reduces_to_the_bernoulli_residual_at_full_weight(self) -> None:
        posterior = gaussian(0.0, 0.55)
        fit = personfit.residual(
            variable="DA",
            item_id="i",
            posterior=posterior,
            a=1.2,
            b=0.0,
            c=0.0,
            score=1.0,
            weight=1.0,
        )
        expected = personfit.posterior_predictive_mean(posterior, 1.2, 0.0, 0.0)
        assert fit.z == pytest.approx(
            (1.0 - expected) / np.sqrt(expected * (1 - expected))
        )

    def test_weaker_evidence_produces_a_smaller_residual(self) -> None:
        posterior = gaussian(0.0, 0.55)
        kwargs = dict(variable="DA", item_id="i", posterior=posterior, a=1.2, b=0.0, c=0.0)
        strong = personfit.residual(score=0.0, weight=1.0, **kwargs)
        weak = personfit.residual(score=0.0, weight=0.25, **kwargs)
        assert abs(weak.z) < abs(strong.z)

    def test_an_unscorable_response_is_never_aberrant(self) -> None:
        fit = personfit.residual(
            variable="DA",
            item_id="i",
            posterior=gaussian(0.0, 0.55),
            a=1.2,
            b=0.0,
            c=0.0,
            score=0.0,
            weight=0.0,
        )
        assert fit.z == 0.0 and fit.aberrant is False

    def test_a_confident_candidate_failing_an_easy_item_is_flagged(self) -> None:
        fit = personfit.residual(
            variable="DA",
            item_id="i",
            posterior=gaussian(2.0, 0.35),
            a=1.5,
            b=-1.5,
            c=0.0,
            score=0.0,
            weight=1.0,
        )
        assert fit.aberrant is True
        assert fit.z < -settings.cat_aberrant_residual_threshold

    def test_the_residual_module_cannot_reach_a_posterior_update(self) -> None:
        """Report-only, by construction: it returns a record, never a state."""
        import ast
        from pathlib import Path

        source = Path(personfit.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = {
            name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for name in (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
        }
        assert not any("orchestrator" in name for name in imported)
