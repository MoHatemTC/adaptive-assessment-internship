"""The statistical machinery the test plan names but does not specify.

Section 24 asks for "paired bootstrap CI", "McNemar test" and "effect size" without saying
which interval, how many resamples, or one-sided against what. The validation document's
smaller-corrections section pins those down, and this module is where they are pinned:

    paired binary difference   Tango's score interval, NOT McNemar's p-value. McNemar
                               tests EQUALITY, which is not the hypothesis — non-inferiority
                               asks whether the difference is bounded, and a test of the
                               wrong null cannot answer it.
    paired continuous          BCa bootstrap, 10,000 resamples, one-sided 95% bound
    safety rates               Clopper-Pearson one-sided upper bound. A rate gate is met
                               when the UPPER BOUND clears it, never when the point
                               estimate does — the difference is the whole of A1.
    clustering                 resample at the SIMULEE level within profile family. The
                               cohort is 8 strata x 5 families, so simulees are not
                               independent draws and an unclustered bootstrap understates
                               every interval.
    effect size                Cohen's d paired for continuous, Cliff's delta for question
                               counts, which are heavily non-normal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import beta as beta_dist  # type: ignore[import-untyped]
from scipy.stats import norm  # type: ignore[import-untyped]

Z95 = float(norm.ppf(0.95))
Z80 = float(norm.ppf(0.80))
Z90 = float(norm.ppf(0.90))


@dataclass(frozen=True)
class Interval:
    point: float
    lower: float
    upper: float

    def as_dict(self) -> dict:
        return {"point": self.point, "lower": self.lower, "upper": self.upper}


# --- rates -------------------------------------------------------------------------


def clopper_pearson_upper(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided exact upper bound on a binomial rate.

    With zero observed failures this is the rule of three: ~3/n. A gate at 3% therefore
    needs ~100 verified events even when nothing goes wrong, and no sample size at all can
    demonstrate compliance if the true rate equals the gate.
    """
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    return float(beta_dist.ppf(1.0 - alpha, k + 1, n - k))


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> Interval:
    """Two-sided exact interval, for descriptive reporting."""
    if n <= 0:
        return Interval(float("nan"), 0.0, 1.0)
    lower = 0.0 if k == 0 else float(beta_dist.ppf(alpha / 2, k, n - k + 1))
    upper = 1.0 if k == n else float(beta_dist.ppf(1 - alpha / 2, k + 1, n - k))
    return Interval(k / n, lower, upper)


def events_needed_for_upper_bound(gate: float, true_rate: float, alpha: float = 0.05) -> int:
    """Verified events required before the one-sided UCB clears `gate`.

    Returns 0 when no sample size suffices, which is the honest answer whenever the true
    rate is at or above the gate.
    """
    if true_rate >= gate:
        return 0
    n = 10
    while n <= 500_000:
        k = int(round(true_rate * n))
        if clopper_pearson_upper(k, n, alpha) < gate:
            return n
        n = int(n * 1.05) + 1
    return 0


# --- paired binary: the primary endpoint --------------------------------------------


def discordance(a: np.ndarray, b: np.ndarray) -> float:
    """psi = P(arms disagree). The quantity the level-endpoint sample size is driven by."""
    a = np.asarray(a, dtype=bool)
    b = np.asarray(b, dtype=bool)
    return float(np.mean(a != b))


def tango_score_interval(
    b: int, c: int, n: int, alpha: float = 0.05, one_sided: bool = True
) -> Interval:
    """CI for the difference of two PAIRED proportions (Tango 1998), by score inversion.

    `b` = pairs where arm 1 succeeded and arm 2 failed, `c` = the reverse, `n` = pairs.
    The estimated difference is (c - b) / n, signed so that a POSITIVE value means arm 2
    is worse — which is the direction a non-inferiority margin is stated in.

    Score inversion rather than a Wald interval because the endpoint sits near 0 by
    construction under the null and a Wald interval has known coverage failure there.
    """
    if n <= 0:
        return Interval(float("nan"), -1.0, 1.0)

    z = norm.ppf(1 - alpha) if one_sided else norm.ppf(1 - alpha / 2)

    def score(delta: float) -> float:
        """Tango's score statistic at a hypothesised difference `delta`."""
        # Constrained MLE of the discordant-pair probability under H: p_c - p_b = delta.
        # Closed form from Tango (1998) eq. 5.
        a_coef = 2.0 * n
        b_coef = -b + c - (2.0 * n + b - c) * delta
        c_coef = -c * delta * (1.0 - delta)
        disc = b_coef * b_coef - 4.0 * a_coef * c_coef
        disc = max(disc, 0.0)
        p_b = (-b_coef + np.sqrt(disc)) / (2.0 * a_coef) if a_coef else 0.0
        p_b = float(np.clip(p_b, 1e-12, 1.0))
        variance = n * (2.0 * p_b + delta * (1.0 - delta))
        if variance <= 0:
            return 0.0
        return float((c - b - n * delta) / np.sqrt(variance))

    def solve(target: float, low: float, high: float) -> float:
        for _ in range(200):
            mid = (low + high) / 2.0
            if score(mid) > target:
                low = mid
            else:
                high = mid
        return (low + high) / 2.0

    point = (c - b) / n
    upper = solve(-z, -0.9999, 0.9999)
    lower = solve(z, -0.9999, 0.9999) if not one_sided else -1.0
    return Interval(float(point), float(lower), float(upper))


def non_inferiority_binary(
    correct_ref: np.ndarray, correct_test: np.ndarray, margin: float, alpha: float = 0.05
) -> dict:
    """Paired non-inferiority on a binary endpoint (M-03).

    H0: accuracy_ref - accuracy_test >= margin   (the test arm is materially worse)
    H1: accuracy_ref - accuracy_test <  margin

    Declared non-inferior when the one-sided upper bound on the degradation is below the
    margin. One primary endpoint at alpha = 0.05 is what makes that alpha mean anything;
    see `gates.py` for why the other twenty-odd comparisons carry no verdict.
    """
    ref = np.asarray(correct_ref, dtype=bool)
    test = np.asarray(correct_test, dtype=bool)
    n = ref.size
    b = int(np.sum(~ref & test))  # test better
    c = int(np.sum(ref & ~test))  # test worse
    interval = tango_score_interval(b, c, n, alpha=alpha, one_sided=True)
    return {
        "n_pairs": n,
        "accuracy_ref": float(ref.mean()) if n else float("nan"),
        "accuracy_test": float(test.mean()) if n else float("nan"),
        "degradation_pp": round(100.0 * interval.point, 4),
        "upper_bound_pp": round(100.0 * interval.upper, 4),
        "margin_pp": round(100.0 * margin, 4),
        "discordance_psi": round(discordance(ref, test), 5),
        "discordant_pairs": {"test_better": b, "test_worse": c},
        "non_inferior": bool(interval.upper < margin),
    }


# --- paired continuous ----------------------------------------------------------------


def bca_bootstrap(
    values: np.ndarray,
    strata: np.ndarray | None = None,
    *,
    resamples: int = 10_000,
    alpha: float = 0.05,
    one_sided: bool = True,
    seed: int = 20260804,
) -> Interval:
    """BCa interval on a mean, resampling CANDIDATES WITHIN PROFILE FAMILY.

    `values` is one number per simulee — the paired difference averaged over that
    simulee's main competencies. Aggregating to the candidate first is what makes the
    resampling unit the candidate, which is what the cohort's structure requires: it is a
    balanced 8 strata x 5 families design, so the three mains of one candidate are not
    three independent observations and resampling rows would treat shared structure as new
    information. `strata` is the profile family, resampled within.

    BCa rather than percentile because the paired squared-error difference the RMSE
    endpoint uses is skewed — squared errors are heavy-tailed — and a percentile interval
    inherits that skew as a coverage error.
    """
    values = np.asarray(values, dtype=float)
    n = values.size
    if n < 3:
        return Interval(float("nan"), float("nan"), float("nan"))
    if strata is None:
        strata = np.zeros(n, dtype=int)
    strata = np.asarray(strata)

    rng = np.random.default_rng(seed)
    observed = float(values.mean())

    # Draw every replicate's indices at once, per stratum. A Python loop over resamples x
    # strata is what made this the slowest thing in the analysis.
    replicate_sums = np.zeros(resamples)
    for stratum in np.unique(strata):
        idx = np.flatnonzero(strata == stratum)
        picks = rng.integers(0, idx.size, size=(resamples, idx.size))
        replicate_sums += values[idx][picks].sum(axis=1)
    replicates = replicate_sums / n

    proportion_below = float(np.mean(replicates < observed))
    proportion_below = min(max(proportion_below, 1.0 / resamples), 1.0 - 1.0 / resamples)
    z0 = float(norm.ppf(proportion_below))

    # Jackknife over candidates, in closed form: the leave-one-out mean of a mean.
    total = values.sum()
    jackknife = (total - values) / (n - 1)
    centred = jackknife.mean() - jackknife
    denominator = 6.0 * (float(np.sum(centred**2)) ** 1.5)
    acceleration = float(np.sum(centred**3) / denominator) if denominator else 0.0

    def endpoint(q: float) -> float:
        zq = float(norm.ppf(q))
        adjusted = z0 + (z0 + zq) / max(1.0 - acceleration * (z0 + zq), 1e-9)
        return float(np.percentile(replicates, 100.0 * norm.cdf(adjusted)))

    if one_sided:
        return Interval(observed, float("-inf"), endpoint(1.0 - alpha))
    return Interval(observed, endpoint(alpha / 2), endpoint(1.0 - alpha / 2))


def cohens_d_paired(differences: np.ndarray) -> float:
    d = np.asarray(differences, dtype=float)
    sd = float(d.std(ddof=1))
    return float(d.mean() / sd) if sd > 0 else 0.0


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """Non-parametric effect size for question counts, which are discrete and skewed."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if a.size == 0 or b.size == 0:
        return 0.0
    # Rank-based rather than the O(n^2) pairwise form, so 4,000 x 4,000 stays cheap.
    combined = np.concatenate([a, b])
    order = np.argsort(combined, kind="mergesort")
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, combined.size + 1)
    # Average ranks over ties.
    sorted_values = combined[order]
    start = 0
    for i in range(1, combined.size + 1):
        if i == combined.size or sorted_values[i] != sorted_values[start]:
            ranks[order[start:i]] = (start + 1 + i) / 2.0
            start = i
    rank_a = ranks[: a.size].sum()
    u = rank_a - a.size * (a.size + 1) / 2.0
    return float(2.0 * u / (a.size * b.size) - 1.0)


# --- power (validation blocker B1) ----------------------------------------------------


def n_for_paired_binary(margin: float, psi: float, power: float = 0.80, alpha: float = 0.05) -> int:
    """Pairs needed for paired non-inferiority on a binary endpoint.

    n = (z_alpha + z_beta)^2 * psi / margin^2, where psi is the DISCORDANCE rate. Because
    Approach C asks a different set of questions, psi is not small — which is why the
    level endpoint, not theta, sets the sample size.
    """
    z_beta = float(norm.ppf(power))
    return int(np.ceil((Z95 + z_beta) ** 2 * psi / margin**2))


def detectable_margin(n: int, psi: float, power: float = 0.80) -> float:
    z_beta = float(norm.ppf(power))
    return float((Z95 + z_beta) * np.sqrt(psi / max(n, 1)))


# --- prediction metrics ---------------------------------------------------------------


def auc(p: np.ndarray, y: np.ndarray) -> float:
    """ROC AUC by rank, with ties handled."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=int)
    positives, negatives = int(y.sum()), int((1 - y).sum())
    if positives == 0 or negatives == 0:
        return float("nan")
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(p.size, dtype=float)
    sorted_p = p[order]
    start = 0
    for i in range(1, p.size + 1):
        if i == p.size or sorted_p[i] != sorted_p[start]:
            ranks[order[start:i]] = (start + 1 + i) / 2.0
            start = i
    return float((ranks[y == 1].sum() - positives * (positives + 1) / 2.0) / (positives * negatives))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def log_loss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(np.asarray(p, float), 1e-9, 1 - 1e-9)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def expected_calibration_error(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    if p.size == 0:
        return float("nan")
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = 0.0
    for i in range(bins):
        mask = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if not mask.any():
            continue
        total += mask.mean() * abs(y[mask].mean() - p[mask].mean())
    return float(total)


def delong_auc_difference(p_ref: np.ndarray, p_test: np.ndarray, y: np.ndarray) -> dict:
    """DeLong's test for two CORRELATED ROC curves on the same labels.

    Named because the plan gates on an AUC difference and does not say how to test one.
    Two AUCs computed on the same held-out responses are correlated, so an unpaired
    comparison overstates the standard error.
    """
    y = np.asarray(y, dtype=int)
    pos = np.flatnonzero(y == 1)
    neg = np.flatnonzero(y == 0)
    if pos.size == 0 or neg.size == 0:
        return {"auc_ref": float("nan"), "auc_test": float("nan"), "difference": float("nan"),
                "se": float("nan"), "z": float("nan"), "p_two_sided": float("nan")}

    def midrank(values: np.ndarray) -> np.ndarray:
        """Ranks with ties averaged. The kernel of the fast DeLong algorithm."""
        order = np.argsort(values, kind="mergesort")
        sorted_values = values[order]
        ranks = np.empty(values.size, dtype=float)
        i = 0
        while i < values.size:
            j = i
            while j + 1 < values.size and sorted_values[j + 1] == sorted_values[i]:
                j += 1
            ranks[order[i : j + 1]] = (i + j + 2) / 2.0
            i = j + 1
        return ranks

    def structural(scores):
        """Placement values, in O(n log n) rather than O(positives x negatives).

        The naive pairwise form is correct and unusable: at 60,000 held-out predictions it
        is 10^9 comparisons per arm.
        """
        scores = np.asarray(scores, dtype=float)
        x, yv = scores[pos], scores[neg]
        m, n_neg = x.size, yv.size
        tx, ty = midrank(x), midrank(yv)
        tz = midrank(np.concatenate([x, yv]))
        v10 = (tz[:m] - tx) / n_neg
        v01 = 1.0 - (tz[m:] - ty) / m
        return v10, v01, float(v10.mean())

    a10, a01, auc_ref = structural(p_ref)
    b10, b01, auc_test = structural(p_test)
    s10 = np.cov(np.vstack([a10, b10]))
    s01 = np.cov(np.vstack([a01, b01]))
    covariance = s10 / pos.size + s01 / neg.size
    variance = float(covariance[0, 0] + covariance[1, 1] - 2 * covariance[0, 1])
    difference = auc_test - auc_ref
    se = float(np.sqrt(max(variance, 0.0)))
    z = difference / se if se > 0 else 0.0
    return {
        "auc_ref": round(auc_ref, 5),
        "auc_test": round(auc_test, 5),
        "difference": round(difference, 5),
        "se": round(se, 6),
        "z": round(float(z), 4),
        "p_two_sided": round(float(2 * (1 - norm.cdf(abs(z)))), 6),
    }


# --- reliability ----------------------------------------------------------------------


def marginal_reliability(theta_hat: np.ndarray, se: np.ndarray) -> float:
    """1 - E[SE^2] / (Var(theta_hat) + E[SE^2]).

    The share of observed-score variance that is not measurement error. Reported because
    the plan has no absolute quality bar anywhere (validation B3): every gate is B-versus-C
    relative, so both approaches can pass by being equally unfit.
    """
    theta_hat = np.asarray(theta_hat, float)
    se = np.asarray(se, float)
    error = float(np.mean(se**2))
    observed = float(np.var(theta_hat, ddof=1)) + error
    return float(1.0 - error / observed) if observed > 0 else float("nan")


def decision_consistency(band_probability_rows: list[dict[str, float]]) -> float:
    """P(two independent administrations assign the same level), per Livingston & Lewis.

    Estimated from the posterior itself: sum_b P(band = b)^2 is the probability that two
    draws from the same belief land in the same band. Companion to decision ACCURACY —
    an instrument can be consistent and consistently wrong.
    """
    if not band_probability_rows:
        return float("nan")
    per = [sum(float(p) ** 2 for p in row.values()) for row in band_probability_rows]
    return float(np.mean(per))


# --- within-candidate error correlation -------------------------------------------------


def tetrachoric(table: np.ndarray, *, tol: float = 1e-9) -> float:
    """Tetrachoric correlation from a 2x2 table of binary indicators.

    THE STUDY'S PRIMARY OBJECTIVE, and the input to the K decision. Corroboration only
    reduces the wrong-inference rate if the errors it averages over are independent. If a
    candidate's misjudged node drives every inference drawn from it, requiring more
    observations of the same candidate buys nothing, and the plan's section 2.2 table
    collapses from "K=3 clears the gate" to "no K clears it".

    Phi (the plain correlation of the two indicators) is the wrong estimator here: it is
    bounded by the marginals, so two indicators that are both rare cannot show a high phi
    even when they are perfectly dependent. The tetrachoric assumes the binary outcomes
    are thresholded draws from a bivariate normal — which is exactly the latent-trait
    story the rest of this harness already commits to — and estimates the correlation of
    that latent pair.

    scipy has no tetrachoric, so it is solved directly: fix the thresholds at the inverse
    normal of the observed marginals, then find the rho whose bivariate orthant
    probability reproduces the observed (1,1) cell. Monotone in rho, so a bisection is
    both sufficient and robust; Newton would be faster and can leave the interval.

    Returns NaN when the table is degenerate — a zero margin means one indicator never
    varied, and a correlation with something constant is not defined rather than zero.
    """
    from scipy.stats import multivariate_normal

    table = np.asarray(table, dtype=float)
    n = float(table.sum())
    if n <= 0:
        return float("nan")

    p_row = float((table[1, 0] + table[1, 1]) / n)
    p_col = float((table[0, 1] + table[1, 1]) / n)
    if not (0.0 < p_row < 1.0) or not (0.0 < p_col < 1.0):
        return float("nan")

    # Thresholds, with the sign convention that "1" is the upper tail.
    h = float(norm.ppf(1.0 - p_row))
    k = float(norm.ppf(1.0 - p_col))
    target = float(table[1, 1] / n)

    def orthant(rho: float) -> float:
        """P(X > h, Y > k) under a standard bivariate normal with correlation rho."""
        return float(
            multivariate_normal(mean=[0.0, 0.0], cov=[[1.0, rho], [rho, 1.0]]).cdf(
                [-h, -k]
            )
        )

    low, high = -0.999, 0.999
    if target <= orthant(low):
        return low
    if target >= orthant(high):
        return high
    for _ in range(200):
        mid = 0.5 * (low + high)
        if orthant(mid) < target:
            low = mid
        else:
            high = mid
        if high - low < tol:
            break
    return float(0.5 * (low + high))


def bonett_price_tetrachoric(table: np.ndarray) -> float:
    """Closed-form tetrachoric approximation, as a cross-check on the solved value.

    Two independent routes to one number. The solver could silently converge to a wrong
    root through a sign error in the threshold convention, and that mistake would be
    invisible in the output — a plausible correlation is still a plausible correlation.
    """
    table = np.asarray(table, dtype=float)
    a, b, c, d = table[0, 0], table[0, 1], table[1, 0], table[1, 1]
    if min(a, b, c, d) <= 0:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    p_row = (c + d) / table.sum() if table.sum() else 0.5
    p_col = (b + d) / table.sum() if table.sum() else 0.5
    p_min = min(p_row, 1 - p_row, p_col, 1 - p_col)
    if p_min <= 0:
        return float("nan")
    c_exp = (1.0 - abs(p_row - p_col) / 5.0 - (0.5 - p_min) ** 2) / 2.0
    omega = (a * d) / (b * c)
    return float(np.cos(np.pi / (1.0 + omega**c_exp)))


def within_candidate_error_correlation(
    pairs_by_candidate: dict[str, list[int]],
    *,
    resamples: int = 2000,
    seed: int = 20260805,
) -> dict:
    """`r`: how correlated two inference errors are when they share a candidate.

    `pairs_by_candidate` maps a candidate id to the correctness indicators (1 = wrong) of
    every verified inference for that candidate. Within each candidate, all C(m, 2)
    unordered pairs are formed and pooled into one 2x2 table.

    Clusters of size 1 contribute nothing and are counted separately. A candidate with one
    inference says nothing about whether that candidate's errors repeat, and silently
    dropping them would hide how little of the data actually speaks to the question.

    The CI is a cluster bootstrap over candidates, not over pairs. Pairs from one candidate
    are the dependency being measured, so resampling them independently would assume away
    the thing the statistic exists to estimate.
    """
    rng = np.random.default_rng(seed)
    candidates = [c for c, v in pairs_by_candidate.items() if len(v) >= 2]
    singletons = sum(1 for v in pairs_by_candidate.values() if len(v) == 1)

    def table_for(ids: list[str]) -> np.ndarray:
        table = np.zeros((2, 2), dtype=float)
        for cid in ids:
            values = pairs_by_candidate[cid]
            for i in range(len(values)):
                for j in range(i + 1, len(values)):
                    x, y = int(values[i]), int(values[j])
                    # Unordered: enter both orientations so the table is symmetric and the
                    # marginals are equal, which is what the estimator assumes.
                    table[x, y] += 1.0
                    table[y, x] += 1.0
        return table

    if len(candidates) < 2:
        return {
            "estimable": False,
            "reason": "fewer than 2 candidates have 2+ verified inferences",
            "clusters_with_multiple": len(candidates),
            "clusters_with_one": singletons,
            "point": None,
            "ci95": None,
        }

    table = table_for(candidates)
    pairs = float(table.sum() / 2.0)
    point = tetrachoric(table)
    if not np.isfinite(point):
        return {
            "estimable": False,
            "reason": "degenerate margin: every paired inference had the same outcome",
            "clusters_with_multiple": len(candidates),
            "clusters_with_one": singletons,
            "pairs": pairs,
            "point": None,
            "ci95": None,
        }

    draws = []
    for _ in range(resamples):
        sample = [candidates[i] for i in rng.integers(0, len(candidates), len(candidates))]
        value = tetrachoric(table_for(sample))
        if np.isfinite(value):
            draws.append(value)

    ci = (
        (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))
        if len(draws) >= 100
        else None
    )
    mean_cluster = float(np.mean([len(pairs_by_candidate[c]) for c in candidates]))
    return {
        "estimable": True,
        "point": round(float(point), 5),
        "cross_check_bonett_price": round(float(bonett_price_tetrachoric(table)), 5),
        "ci95": [round(ci[0], 5), round(ci[1], 5)] if ci else None,
        "clusters_with_multiple": len(candidates),
        "clusters_with_one": singletons,
        "pairs": pairs,
        "mean_cluster_size": round(mean_cluster, 3),
        # What r actually COSTS: the factor by which clustering inflates the variance of
        # any rate computed over these inferences. Reported beside r because a correlation
        # is abstract and a design effect is the number that changes a sample size.
        "design_effect": round(1.0 + (mean_cluster - 1.0) * max(float(point), 0.0), 3),
        "bootstrap_draws": len(draws),
    }


def reliability_at_population_sd(
    theta_hat: np.ndarray, se: np.ndarray, true_theta: np.ndarray, population_sd: float = 1.0
) -> dict:
    """Marginal reliability, re-weighted to a STATED population, and the raw value beside it.

    PRE-4. `marginal_reliability` divides by the observed spread of theta_hat, and this
    cohort's spread is a design choice: eight strata deliberately spanning [-2.8, 2.8] give
    an SD near 2.0 where a real intake is closer to 1.0. Reliability rises with the spread
    of the population it is measured on, so the design cohort inflates it — a figure of
    0.95 on this cohort can be 0.79 on the population it will serve.

    That makes an unqualified reliability number not merely imprecise but uninterpretable:
    it is a property of the cohort as much as of the instrument, and nothing in the output
    said which cohort. Both are now reported, with the assumed SD named.

    The re-weighting is importance sampling: each candidate is weighted by how likely their
    true ability would be under N(0, population_sd) relative to the flat design density.
    """
    theta_hat = np.asarray(theta_hat, float)
    se = np.asarray(se, float)
    true_theta = np.asarray(true_theta, float)

    raw = marginal_reliability(theta_hat, se)
    if theta_hat.size < 2 or population_sd <= 0:
        return {
            "marginal_reliability_design_cohort": raw,
            "marginal_reliability_at_population_sd": float("nan"),
            "population_sd": population_sd,
            "design_cohort_theta_sd": float(np.std(true_theta, ddof=1)) if true_theta.size > 1 else float("nan"),
        }

    design_sd = float(np.std(true_theta, ddof=1))
    # Target density over the flat-ish design density. The design is uniform across strata
    # by construction, so its density is constant and cancels except for the range.
    weights = norm.pdf(true_theta, loc=0.0, scale=population_sd)
    total = float(weights.sum())
    if total <= 0:
        reweighted = float("nan")
    else:
        weights = weights / total
        error = float(np.sum(weights * se**2))
        mean_theta = float(np.sum(weights * theta_hat))
        variance = float(np.sum(weights * (theta_hat - mean_theta) ** 2))
        observed = variance + error
        reweighted = float(1.0 - error / observed) if observed > 0 else float("nan")

    return {
        "marginal_reliability_design_cohort": round(raw, 5) if np.isfinite(raw) else raw,
        "marginal_reliability_at_population_sd": (
            round(reweighted, 5) if np.isfinite(reweighted) else reweighted
        ),
        "population_sd": population_sd,
        "design_cohort_theta_sd": round(design_sd, 4),
        "note": (
            "Reliability is conditional on the ability spread it is measured over. The "
            "design cohort is deliberately wide, so its figure is an upper bound on what "
            f"a N(0, {population_sd}) intake would see."
        ),
    }


def main_effects(
    responses: dict[str, float],
    levels: dict[str, dict[str, str]],
    factors: list[str],
    centre_responses: list[float],
) -> dict:
    """Screening main effects from a two-level fractional factorial, with pure error.

    `effect = mean(y | high) - mean(y | low)`, which for a balanced design is the ordinary
    least-squares estimate and needs no model fitting.

    Pure error comes from the centre-point replicates, so the error estimate does not
    assume the linear model is right — which matters, because that model is exactly what
    the curvature check below is testing.

    THE 2-SIGMA RULE IS A SCREENING HEURISTIC AND IS LABELLED AS ONE. With 3 degrees of
    freedom the standard error is itself very uncertain, and treating |effect| > 2*SE as a
    hypothesis test at any particular alpha would be false precision. It is a triage rule
    for deciding which two or three factors deserve a response surface.
    """
    cells = [c for c in responses if c in levels]
    usable = [c for c in cells if np.isfinite(responses[c])]
    dropped = [c for c in cells if not np.isfinite(responses[c])]

    centre = [float(v) for v in centre_responses if np.isfinite(v)]
    if len(centre) >= 2:
        pure_error_sd = float(np.std(centre, ddof=1))
        pure_error_df = len(centre) - 1
    else:
        pure_error_sd = float("nan")
        pure_error_df = 0

    n_factorial = len(usable)
    effect_se = (
        2.0 * pure_error_sd / np.sqrt(n_factorial) if n_factorial and np.isfinite(pure_error_sd) else float("nan")
    )

    rows: dict[str, dict[str, object]] = {}
    for factor in factors:
        high = [responses[c] for c in usable if levels[c].get(factor) == "high"]
        low = [responses[c] for c in usable if levels[c].get(factor) == "low"]
        if not high or not low:
            rows[factor] = {
                "effect": None,
                "n_high": len(high),
                "n_low": len(low),
                "active": None,
                "note": "factor did not vary among cells with a finite response",
            }
            continue
        effect = float(np.mean(high) - np.mean(low))
        rows[factor] = {
            "effect": round(effect, 6),
            "mean_high": round(float(np.mean(high)), 6),
            "mean_low": round(float(np.mean(low)), 6),
            "n_high": len(high),
            "n_low": len(low),
            "active": (
                bool(abs(effect) > 2.0 * effect_se) if np.isfinite(effect_se) else None
            ),
        }

    factorial_mean = float(np.mean([responses[c] for c in usable])) if usable else float("nan")
    curvature = (
        float(factorial_mean - np.mean(centre)) if centre and np.isfinite(factorial_mean) else float("nan")
    )

    return {
        "effects": rows,
        "pure_error_sd": round(pure_error_sd, 6) if np.isfinite(pure_error_sd) else None,
        "pure_error_df": pure_error_df,
        "effect_standard_error": round(effect_se, 6) if np.isfinite(effect_se) else None,
        "factorial_mean": round(factorial_mean, 6) if np.isfinite(factorial_mean) else None,
        "centre_mean": round(float(np.mean(centre)), 6) if centre else None,
        "curvature": round(curvature, 6) if np.isfinite(curvature) else None,
        "cells_used": n_factorial,
        # Named, not counted. Which cells are missing decides whether what remains is
        # still balanced, and a bare count cannot answer that.
        "cells_dropped": sorted(dropped),
        "activity_rule": (
            "|effect| > 2 x SE(effect), where SE comes from centre-point pure error with "
            f"{pure_error_df} degrees of freedom. A SCREENING HEURISTIC for choosing which "
            "factors deserve a response surface — not a hypothesis test at any alpha."
        ),
    }
