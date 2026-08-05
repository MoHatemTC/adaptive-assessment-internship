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
from scipy.stats import beta as beta_dist
from scipy.stats import norm

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
