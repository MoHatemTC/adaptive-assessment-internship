"""Invariants of the measurement core.

These are properties the engine must hold whatever the bank contains, so they are asserted
rather than sampled. Several of them encode a defect that reached a live session.
"""

from __future__ import annotations

import pytest

from app.services.code_adaptive.irt import (
    KL_PHASE_ITEMS,
    MASTERY_GRID,
    beta_posterior_grid,
    expected_fisher_information,
    fisher_information,
    kl_delta,
    kl_information,
    mastery_band,
    posterior_mean,
    posterior_standard_error,
    probability_correct,
    selection_criterion,
    stop_rule_calibration,
)


def test_information_is_the_exact_3pl_quantity_at_zero_guessing():
    """a^2 P(1-P) is not an approximation here — with c = 0 it IS the 3PL formula.

    Nobody guesses their way to a passing test suite, so c = 0 is a fact about the domain.
    If this ever diverges, the engine has silently adopted the 2PL approximation of a
    different model.
    """
    a, b = 1.3, 0.55
    for mastery in (0.1, 0.5, 0.55, 0.9):
        p = probability_correct(mastery, a, b)
        exact_3pl = (a**2) * (((p - 0.0) / (1.0 - 0.0)) ** 2) * ((1.0 - p) / p)
        assert fisher_information(mastery, a, b) == pytest.approx(exact_3pl, rel=1e-12)


def test_information_peaks_where_the_outcome_is_uncertain():
    """Maximal at mastery == difficulty, which is what makes the test adaptive.

    A question far below or far above the estimate has an almost certain outcome and
    therefore tells you almost nothing, whatever its difficulty label says.
    """
    a, b = 1.2, 0.6
    at_peak = fisher_information(b, a, b)
    assert at_peak > fisher_information(b - 0.4, a, b)
    assert at_peak > fisher_information(b + 0.4, a, b)


def test_information_scales_with_discrimination_squared():
    sharp = fisher_information(0.5, 2.0, 0.5)
    blunt = fisher_information(0.5, 1.0, 0.5)
    assert sharp == pytest.approx(4.0 * blunt, rel=1e-9)


def test_posterior_is_a_normalised_distribution():
    for alpha, beta in [(1, 1), (3, 1), (9.4, 4.6)]:
        grid = beta_posterior_grid(alpha, beta)
        assert len(grid) == len(MASTERY_GRID)
        assert sum(grid) == pytest.approx(1.0, abs=1e-9)
        assert all(w >= 0.0 for w in grid)


def test_posterior_survives_large_parameters():
    """Built in log space because m**(alpha-1) underflows across most of the grid.

    Without it the posterior silently collapses onto a single point once alpha and beta
    reach double figures — which happens within a few questions.
    """
    grid = beta_posterior_grid(60.0, 25.0)
    assert sum(grid) == pytest.approx(1.0, abs=1e-9)
    grid_mean = sum(m * w for m, w in zip(MASTERY_GRID, grid))
    assert grid_mean == pytest.approx(posterior_mean(60.0, 25.0), abs=0.01)
    assert sum(1 for w in grid if w > 1e-6) > 5  # not degenerate


def test_expected_information_differs_from_information_at_the_mean():
    """The whole reason for averaging over the posterior rather than evaluating at a point."""
    grid = beta_posterior_grid(3.0, 1.0)
    averaged = expected_fisher_information(grid, 1.3, 0.55)
    at_mean = fisher_information(posterior_mean(3.0, 1.0), 1.3, 0.55)
    assert averaged != pytest.approx(at_mean, rel=1e-6)


def test_kl_prefers_a_wider_region_early():
    """KL over a neighbourhood is non-negative and grows as the neighbourhood widens."""
    wide = kl_information(0.5, 1.2, 0.5, delta=0.375)
    narrow = kl_information(0.5, 1.2, 0.5, delta=0.1)
    assert wide >= narrow >= 0.0


def test_criterion_switches_after_the_kl_phase():
    assert selection_criterion(0) == "KL"
    assert selection_criterion(KL_PHASE_ITEMS - 1) == "KL"
    assert selection_criterion(KL_PHASE_ITEMS) == "E[Fisher]"


def test_kl_neighbourhood_shrinks_as_evidence_accumulates():
    assert kl_delta(0) > kl_delta(1) > kl_delta(5)


def test_standard_error_falls_only_with_evidence():
    prior = posterior_standard_error(1.0, 1.0)
    after_one = posterior_standard_error(2.0, 1.0)
    after_five = posterior_standard_error(4.5, 2.5)
    assert prior > after_one > after_five


def test_unmeasured_competency_has_no_level():
    """A Beta(1,1) mean is exactly 0.5, which would otherwise read as a measured 'Competent'."""
    level, band = mastery_band(0.5, observed=False)
    assert level is None
    assert band == "Not assessed"


@pytest.mark.parametrize(
    "mastery,expected_level",
    [(0.10, 1), (0.30, 2), (0.50, 3), (0.70, 4), (0.90, 5), (1.0, 5)],
)
def test_bands_cover_the_whole_scale(mastery, expected_level):
    level, band = mastery_band(mastery, observed=True)
    assert level == expected_level
    assert band != "Not assessed"


class TestStopRuleCalibration:
    """A stopping rule that cannot bind is not adaptive, and it fails silently both ways.

    Both regimes below have occurred in this system: TRIVIAL in the code engine at SE 0.20,
    UNREACHABLE in the MCQ bank at SE 0.65 with a 12-question cap.
    """

    def test_target_above_the_prior_is_trivial(self):
        result = stop_rule_calibration(target=0.20, min_questions=5, max_questions=12)
        assert result["verdict"] == "TRIVIAL"

    def test_the_shipped_default_binds(self):
        result = stop_rule_calibration(target=0.15, min_questions=5, max_questions=12)
        assert result["verdict"] == "BINDING"
        assert result["expected_stop_question"] > 5

    def test_target_below_the_floor_is_unreachable(self):
        result = stop_rule_calibration(target=0.05, min_questions=5, max_questions=12)
        assert result["verdict"] == "UNREACHABLE"
        assert result["expected_stop_question"] is None
        assert result["reachable_floor"] > 0.05
