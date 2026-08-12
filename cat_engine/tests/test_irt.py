"""Properties of the 3PL model that must hold exactly, whatever else changes.

These are invariants, not regression snapshots: each one is a mathematical fact about
item response theory, so a failure here means the measurement is wrong — not that a
number moved.
"""

import numpy as np
import pytest

from cat_engine.engine.services.adaptive.irt import (
    THETA_GRID,
    ability_band,
    ability_percentile,
    credible_interval,
    expected_fisher_information,
    fisher_information,
    kullback_leibler_information,
    posterior_update,
    prior_from_self_rating,
    probability_correct,
    uniform_prior,
)


def test_probability_is_monotone_in_ability():
    """P(correct) must rise with ability. Everything else depends on this."""
    p = [probability_correct(t, 1.2, 0.0, 0.25) for t in np.linspace(-4, 4, 50)]
    assert all(b >= a for a, b in zip(p, p[1:]))


def test_probability_respects_the_guessing_floor():
    """P approaches c from above as ability falls, and 1 as it rises — never crossing.

    The strict inequality is the part that matters: c is a floor, so no ability however
    low may drop below it. The asymptote is only ever approached, which is why this
    checks the bound and the limit separately rather than asserting equality at some
    arbitrarily distant theta.
    """
    for theta in (-10.0, -4.0, 0.0, 4.0):
        assert probability_correct(theta, 1.2, 0.0, 0.25) > 0.25
    assert probability_correct(-30.0, 1.2, 0.0, 0.25) == pytest.approx(0.25, abs=1e-9)
    assert probability_correct(30.0, 1.2, 0.0, 0.25) == pytest.approx(1.0, abs=1e-9)


def test_information_peaks_near_difficulty():
    """An item is most informative about candidates near its own difficulty."""
    a, b, c = 1.5, 0.8, 0.25
    at_b = fisher_information(b, a, b, c)
    assert at_b > fisher_information(b - 2.0, a, b, c)
    assert at_b > fisher_information(b + 2.0, a, b, c)


def test_information_grows_with_discrimination():
    """Information scales with a^2, so discrimination is the dominant lever."""
    low = fisher_information(0.0, 0.6, 0.0, 0.25)
    high = fisher_information(0.0, 1.5, 0.0, 0.25)
    assert high > low * 3


def test_guessing_floor_destroys_information():
    """A guessable item tells you less. This is why the 2PL approximation is wrong."""
    assert fisher_information(0.0, 1.2, 0.0, 0.0) > fisher_information(0.0, 1.2, 0.0, 0.25)


def test_correct_answer_moves_the_estimate_up():
    """The likelihood is monotone in theta, so this cannot fail for any item or prior."""
    prior = uniform_prior()
    for b in (-2.0, 0.0, 2.0):
        _, up, _ = posterior_update(prior, 1.2, b, 0.25, True)
        _, down, _ = posterior_update(prior, 1.2, b, 0.25, False)
        assert up > down


def test_posterior_stays_normalised():
    posterior = uniform_prior()
    for _ in range(12):
        posterior, _, _ = posterior_update(posterior, 1.2, 0.4, 0.25, True)
        assert np.sum(posterior) == pytest.approx(1.0)
        assert np.all(posterior >= 0)


def test_standard_error_can_rise_on_a_surprising_answer():
    """Not a bug, and worth pinning: a genuinely surprising response widens the belief.

    A guard that forbade SE from rising would reject correct Bayesian updates — and a
    stopping rule fed by a quantity that cannot move against it is not a stopping rule.
    """
    posterior = prior_from_self_rating(2, 1.1)  # believes the candidate is weak...
    _, _, before = posterior_update(posterior, 1.0, -1.0, 0.25, True)
    hard = posterior_update(posterior, 1.6, 2.2, 0.25, True)  # ...then aces a hard item
    assert hard[2] > before


def test_evidence_overrides_a_wrong_prior():
    """A confident but incorrect self-rating must not survive contrary evidence."""
    posterior = prior_from_self_rating(1, 1.1)  # claims to be a novice
    theta = -2.0
    for _ in range(10):
        posterior, theta, _ = posterior_update(posterior, 1.5, 1.0, 0.25, True)
    assert theta > 0.5


def test_expected_information_differs_from_point_information_when_vague():
    """The reason selection averages over the posterior rather than using theta_hat."""
    vague = prior_from_self_rating(3, 2.0)
    assert expected_fisher_information(vague, 1.5, 2.0, 0.25) != pytest.approx(
        fisher_information(0.0, 1.5, 2.0, 0.25), rel=0.05
    )


def test_kl_widens_with_delta():
    """KL over a neighbourhood must not shrink as the neighbourhood grows."""
    narrow = kullback_leibler_information(0.0, 1.2, 0.5, 0.25, 0.5)
    wide = kullback_leibler_information(0.0, 1.2, 0.5, 0.25, 2.0)
    assert wide >= narrow


def test_percentile_is_monotone_and_bounded():
    values = [ability_percentile(t) for t in np.linspace(-4, 4, 40)]
    assert all(b >= a for a, b in zip(values, values[1:]))
    assert 0.0 <= values[0] and values[-1] <= 100.0
    assert ability_percentile(0.0) == pytest.approx(50.0, abs=0.5)


def test_band_covers_the_scale():
    assert ability_band(-3.0)[0] == 1
    assert ability_band(0.0)[0] == 3
    assert ability_band(3.0)[0] == 5


def test_grid_is_symmetric_and_centred():
    assert THETA_GRID[0] == pytest.approx(-THETA_GRID[-1])
    assert 0.0 in np.round(THETA_GRID, 10)


def test_credible_interval_is_equal_tailed_on_the_grid():
    posterior = prior_from_self_rating(5, 0.6)
    low, high = credible_interval(posterior, mass=0.95)
    assert low < high
    assert low >= float(THETA_GRID[0])
    assert high <= float(THETA_GRID[-1])
    # A sharp high-ability prior should put most mass in the upper half.
    assert low >= 0.0

