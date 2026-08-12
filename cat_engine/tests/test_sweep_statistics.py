"""The three statistics the screening conclusions rest on.

`r` decides whether corroboration can work at all, the main effects decide which factors
get a response surface, and the reliability re-weighting decides whether a reliability
figure means anything. Each is easy to get subtly wrong in a way that produces a plausible
number, so each is checked against a case whose answer is known independently.
"""

from __future__ import annotations

import numpy as np
import pytest

from cat_engine.evaluation import stats


class TestTetrachoric:
    """Checked against simulated data with a known latent correlation."""

    @pytest.mark.parametrize("true_rho", [0.0, 0.2, 0.4, 0.7, -0.3])
    def test_it_recovers_a_known_correlation(self, true_rho):
        rng = np.random.default_rng(11)
        draws = rng.multivariate_normal(
            [0.0, 0.0], [[1.0, true_rho], [true_rho, 1.0]], size=200_000
        )
        binary = (draws > 0.5).astype(int)
        table = np.zeros((2, 2))
        for i in range(2):
            for j in range(2):
                table[i, j] = np.sum((binary[:, 0] == i) & (binary[:, 1] == j))

        assert stats.tetrachoric(table) == pytest.approx(true_rho, abs=0.02)

    def test_the_closed_form_agrees_with_the_solver(self, ):
        """Two independent routes to one number.

        A sign error in the threshold convention would make the solver converge to a
        confidently wrong root, and a plausible correlation is indistinguishable from a
        correct one by inspection.
        """
        rng = np.random.default_rng(5)
        draws = rng.multivariate_normal([0.0, 0.0], [[1.0, 0.45], [0.45, 1.0]], size=100_000)
        binary = (draws > 0.3).astype(int)
        table = np.zeros((2, 2))
        for i in range(2):
            for j in range(2):
                table[i, j] = np.sum((binary[:, 0] == i) & (binary[:, 1] == j))

        assert stats.tetrachoric(table) == pytest.approx(
            stats.bonett_price_tetrachoric(table), abs=0.03
        )

    def test_it_beats_phi_where_phi_is_bounded_by_the_marginals(self):
        """Why the tetrachoric and not the plain correlation.

        Two rare indicators cannot show a high phi however dependent they are, because
        phi is bounded by the marginals. Wrong inferences ARE rare, so phi would
        systematically understate `r` and make corroboration look more promising than it is.
        """
        rng = np.random.default_rng(3)
        draws = rng.multivariate_normal([0.0, 0.0], [[1.0, 0.6], [0.6, 1.0]], size=200_000)
        binary = (draws > 1.5).astype(int)  # ~6.7% marginal: a rare event
        table = np.zeros((2, 2))
        for i in range(2):
            for j in range(2):
                table[i, j] = np.sum((binary[:, 0] == i) & (binary[:, 1] == j))

        n = table.sum()
        p_row = (table[1, 0] + table[1, 1]) / n
        p_col = (table[0, 1] + table[1, 1]) / n
        phi = (table[1, 1] / n - p_row * p_col) / np.sqrt(
            p_row * (1 - p_row) * p_col * (1 - p_col)
        )
        tetra = stats.tetrachoric(table)

        assert tetra == pytest.approx(0.6, abs=0.03)
        assert phi < tetra - 0.15, "phi should be badly attenuated at this marginal"

    def test_a_degenerate_table_is_not_estimable(self):
        """A zero margin means one indicator never varied. That is undefined, not zero."""
        assert np.isnan(stats.tetrachoric(np.array([[100.0, 0.0], [0.0, 0.0]])))
        assert np.isnan(stats.tetrachoric(np.zeros((2, 2))))


class TestWithinCandidateErrorCorrelation:
    def test_independent_errors_give_r_near_zero(self):
        rng = np.random.default_rng(7)
        pairs = {f"c{i}": list(rng.integers(0, 2, 4)) for i in range(400)}
        result = stats.within_candidate_error_correlation(pairs, resamples=200)
        assert result["estimable"]
        assert abs(result["point"]) < 0.12

    def test_perfectly_repeating_errors_give_a_high_r(self):
        """A candidate whose misjudged node drives every inference from it."""
        rng = np.random.default_rng(9)
        pairs = {}
        for i in range(400):
            value = int(rng.random() < 0.3)
            pairs[f"c{i}"] = [value] * 4
        result = stats.within_candidate_error_correlation(pairs, resamples=200)
        assert result["estimable"]
        assert result["point"] > 0.9

    def test_singleton_clusters_are_counted_not_silently_dropped(self):
        """A candidate with one inference says nothing about whether errors repeat.

        Dropping them quietly would hide how little of the data speaks to the question.
        """
        pairs = {"a": [1, 0], "b": [0, 1], "c": [1], "d": [0]}
        result = stats.within_candidate_error_correlation(pairs, resamples=50)
        assert result["clusters_with_one"] == 2
        assert result["clusters_with_multiple"] == 2

    def test_too_few_clusters_is_reported_as_not_estimable(self):
        """Never a number. `r` decides the K question, and a fabricated one decides it wrong."""
        result = stats.within_candidate_error_correlation({"a": [1, 0]})
        assert result["estimable"] is False
        assert result["point"] is None
        assert "fewer than 2" in result["reason"]

    def test_a_uniform_outcome_is_reported_as_not_estimable(self):
        result = stats.within_candidate_error_correlation(
            {f"c{i}": [1, 1, 1] for i in range(50)}, resamples=50
        )
        assert result["estimable"] is False
        assert "degenerate" in result["reason"]

    def test_the_design_effect_is_reported_beside_r(self):
        """A correlation is abstract; a design effect is what changes a sample size."""
        rng = np.random.default_rng(4)
        pairs = {}
        for i in range(300):
            value = int(rng.random() < 0.3)
            pairs[f"c{i}"] = [value] * 3
        result = stats.within_candidate_error_correlation(pairs, resamples=100)
        assert result["design_effect"] > 1.0


class TestMainEffects:
    """A balanced two-level design where the true effect is known by construction."""

    @staticmethod
    def _design(effect_of_a: float, noise: float = 0.0, seed: int = 1):
        rng = np.random.default_rng(seed)
        responses, levels = {}, {}
        for i in range(8):
            bits = [(i >> b) & 1 for b in range(3)]
            cell = f"r{i:02d}"
            levels[cell] = {
                "A": "high" if bits[0] else "low",
                "B": "high" if bits[1] else "low",
                "C": "high" if bits[2] else "low",
            }
            responses[cell] = effect_of_a * bits[0] + rng.normal(0.0, noise)
        return responses, levels

    def test_it_recovers_a_planted_effect(self):
        responses, levels = self._design(effect_of_a=0.5)
        result = stats.main_effects(responses, levels, ["A", "B", "C"], [0.0, 0.0, 0.0, 0.0])
        assert result["effects"]["A"]["effect"] == pytest.approx(0.5, abs=1e-9)
        assert result["effects"]["B"]["effect"] == pytest.approx(0.0, abs=1e-9)
        assert result["effects"]["C"]["effect"] == pytest.approx(0.0, abs=1e-9)

    def test_pure_error_comes_from_the_centre_points(self):
        responses, levels = self._design(effect_of_a=0.5)
        result = stats.main_effects(
            responses, levels, ["A", "B", "C"], [0.10, 0.12, 0.08, 0.11]
        )
        assert result["pure_error_df"] == 3
        # Reported values are rounded to 6 places for the JSON record, so the tolerance
        # is that rounding rather than float precision.
        assert result["pure_error_sd"] == pytest.approx(
            float(np.std([0.10, 0.12, 0.08, 0.11], ddof=1)), abs=1e-6
        )

    def test_a_real_effect_is_active_and_noise_is_not(self):
        responses, levels = self._design(effect_of_a=0.5)
        result = stats.main_effects(
            responses, levels, ["A", "B", "C"], [0.0, 0.01, -0.01, 0.005]
        )
        assert result["effects"]["A"]["active"] is True
        assert result["effects"]["B"]["active"] is False

    def test_dropped_cells_are_named_not_counted(self):
        """Which cells are missing decides whether what remains is balanced."""
        responses, levels = self._design(effect_of_a=0.5)
        responses["r03"] = float("nan")
        result = stats.main_effects(responses, levels, ["A", "B", "C"], [0.0, 0.0, 0.0, 0.0])
        assert result["cells_dropped"] == ["r03"]
        assert result["cells_used"] == 7

    def test_the_activity_rule_is_labelled_a_heuristic(self):
        responses, levels = self._design(effect_of_a=0.5)
        result = stats.main_effects(responses, levels, ["A", "B", "C"], [0.0, 0.0, 0.0, 0.0])
        assert "not a hypothesis test" in result["activity_rule"]

    def test_curvature_is_the_factorial_mean_minus_the_centre_mean(self):
        responses, levels = self._design(effect_of_a=0.4)
        result = stats.main_effects(responses, levels, ["A", "B", "C"], [1.0, 1.0, 1.0, 1.0])
        assert result["curvature"] == pytest.approx(result["factorial_mean"] - 1.0, abs=1e-9)


class TestReliabilityAtPopulationSD:
    """PRE-4: a reliability figure without its population is uninterpretable."""

    def test_a_wide_cohort_reports_a_higher_reliability_than_a_narrow_one(self):
        """The defect, stated as behaviour: the same instrument, two different numbers."""
        rng = np.random.default_rng(2)
        se = np.full(4000, 0.55)

        wide_true = rng.uniform(-2.8, 2.8, 4000)
        narrow_true = rng.normal(0.0, 1.0, 4000)
        wide_hat = wide_true + rng.normal(0, 0.55, 4000)
        narrow_hat = narrow_true + rng.normal(0, 0.55, 4000)

        wide = stats.marginal_reliability(wide_hat, se)
        narrow = stats.marginal_reliability(narrow_hat, se)
        assert wide > narrow + 0.05, "the design cohort should inflate reliability"

    def test_reweighting_brings_the_wide_cohort_down_toward_the_narrow_one(self):
        rng = np.random.default_rng(2)
        true_theta = rng.uniform(-2.8, 2.8, 8000)
        se = np.full(8000, 0.55)
        theta_hat = true_theta + rng.normal(0, 0.55, 8000)

        result = stats.reliability_at_population_sd(theta_hat, se, true_theta, population_sd=1.0)
        assert (
            result["marginal_reliability_at_population_sd"]
            < result["marginal_reliability_design_cohort"]
        )

    def test_both_figures_and_the_assumed_sd_are_always_reported(self):
        rng = np.random.default_rng(6)
        true_theta = rng.uniform(-2.8, 2.8, 500)
        se = np.full(500, 0.55)
        result = stats.reliability_at_population_sd(
            true_theta + rng.normal(0, 0.55, 500), se, true_theta, population_sd=1.0
        )
        assert result["population_sd"] == 1.0
        assert "design_cohort_theta_sd" in result
        assert "conditional on the ability spread" in result["note"]
