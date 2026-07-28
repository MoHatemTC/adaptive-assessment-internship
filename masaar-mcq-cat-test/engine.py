"""
Tuned IRT/CAT engine for the Masaar MCQ harness.

Changes vs earlier harness build (driven by live-session log failures):
- Difficulty map spans very_easy..very_hard (b ∈ {-2..+2}) so Fisher stays
  informative when θ̂ drifts outside {-1, +1}.
- Fisher uses the exact 3PL formula (not the 2PL a²p(1-p) approximation).
- Selection: KL information for the first 3 items, Fisher thereafter
  (restored after Fisher-only failed to converge in real sessions).
- select_item_detailed returns the criterion score used for selection.
- Stopping is a three-rule convergence check (see CONFIDENCE_TARGET below), and
  exposure control is applied to the ranked window rather than the final pick.

Measured caveat on the KL phase: on `enriched_bank_cat.json` it earns nothing.
Over 750 simulated candidates per arm, KL-for-first-3 scored RMSE 0.668 against
0.657 for Fisher-only, and posterior-expected vs point Fisher was 0.657 vs 0.656.
The differences are within noise, but the docstring's claim that Fisher-only
"failed to converge" does not reproduce. Kept because it is harmless and matches
the documented design; do not cite it as a win without re-measuring.
"""

import os
from dataclasses import dataclass

import numpy as np

GRID = np.arange(-4, 4.0001, 0.2)
SE_TARGET = 0.65          # empirically reachable in MCQ 3PL short tests with a spread bank.
                          # Original 0.45 stayed out of reach (live SE≈0.71 with coarse b-ladder;
                          # enriched bank sims ≈0.58–0.62 at q=12). 0.65 marks genuine convergence.
MAX_QUESTIONS = 12

# --- Convergence -----------------------------------------------------------
# A competency ends when ANY of three conditions holds:
#   1. certainty >= CONFIDENCE_TARGET      — the estimate is precise enough
#   2. the same level STABLE_WINDOW times  — the reported band has settled
#   3. q_count >= MAX_QUESTIONS            — budget spent
#
# Rule 1 is exactly equivalent to the older `se <= SE_TARGET` test, because
# certainty.posterior_certainty_pct maps SE_TARGET to exactly 90% and is capped at
# 89% above it. Stating it as confidence keeps the stopping rule in the same units
# the candidate's report is written in.
CONFIDENCE_TARGET = 0.90

# Rule 2 stops on a *coarse* statistic: level = round(3 + θ̂) is a 5-way bucket, so
# three identical levels mostly means θ̂ has not moved much yet, not that it converged.
# Two guards, both measured rather than guessed (200 candidates × 7 θ × 5 competencies):
#
#   MIN_QUESTIONS   keeps the rule from firing at q=3, where SE ≈ 1.2 and repeat levels
#                   are an artefact of the flat prior rather than evidence.
#   STABILITY_FLOOR keeps it from firing while the estimate is still vague. Without it
#                   the rule fired in 98% of sessions at SE ≈ 0.85 and RMSE degraded
#                   0.712 → 0.825: MIN_QUESTIONS alone was not enough, because θ̂ moves
#                   slowly enough that the bucket repeats regardless of precision.
#
# With the floor: RMSE 0.747 at ~9 items, vs 0.712 at ~12 with no stability rule. The
# rule is a test-length optimisation that costs accuracy — it is not evidence the
# estimate is precise, which is why a stability stop still reports low_confidence.
STABLE_WINDOW = 3
MIN_QUESTIONS = 6
STABILITY_FLOOR = 0.80

# --- Content balancing -----------------------------------------------------
# Sub-competency coverage was documented as a tie-break, and a tie-break cannot deliver
# it: it sits behind |b - theta|, which is continuous, so it essentially never decides.
# Measured coverage of 7.4/8 sub-competencies per session was the bank being well spread,
# not the algorithm balancing — a lumpier bank would silently produce a competency score
# resting on three sub-competencies out of eight.
#
# So it is a constraint instead. Among items whose information is within
# CONTENT_INFO_TOLERANCE of the best available, prefer the least-served sub-competency.
# The tolerance is what makes this safe: the trade is bounded to a small, known
# information cost, rather than the open-ended one a hard quota would impose.
#
# 0.80 chosen from a measured sweep (1050 sessions per point, prior SD 2.0):
#
#   tolerance   coverage /8   RMSE
#   off              6.32     0.746
#   0.90             6.46     0.756
#   0.80             6.59     0.749     <- here
#   0.70             6.72     0.771
#   0.60             6.86     0.770
#
# Be honest about the size of this: +0.27 sub-competencies for an RMSE cost inside noise.
# It is a real improvement and a cheap one, but it does not rescue a lumpy bank — the
# worst session still touched only 4 of 8, in every condition including 0.60. Widening the
# shortlist to 8 changed nothing (6.59 either way), so the top-5 window was never the
# limiter; the binding constraint is that a 9-item test cannot cover 8 sub-competencies
# and still put its items where the information is. Real blueprint enforcement needs a
# longer test or a per-sub quota, both of which cost more than this is worth here.
CONTENT_INFO_TOLERANCE = 0.80

# --- Exposure control ------------------------------------------------------
# Randomesque (Kingsbury & Zara 1989): administer a uniform pick from the k most
# informative items instead of the argmax. Pure argmax is deterministic, so every
# candidate at a given θ̂ receives an identical form — the bank leaks after one cohort.
# k=1 restores the old argmax behaviour for reproducible simulation and tests.
EXPOSURE_TOP_K = int(os.getenv("CAT_EXPOSURE_TOP_K", "3"))
_exposure_rng = np.random.default_rng()

# Extended ladder — previous {-1,0,1} left high-ability candidates with near-zero
# Fisher information (see assessment.log: Fisher_I ≈ 0.02–0.05 at θ̂ ≈ 2.5).
DIFFICULTY_MAP = {
    "very_easy": -2.0,
    "easy": -1.0,
    "medium": 0.0,
    "hard": 1.0,
    "very_hard": 2.0,
}
DISCRIMINATION_MAP = {"low": 0.6, "medium": 1.0, "high": 1.5}

SD_UNINFORMED = 2.0
SD_LOW_CONF = 1.7
SD_HIGH_CONF = 1.1


def resolve_b(item_or_difficulty):
    """Accept numeric b or a difficulty label."""
    if isinstance(item_or_difficulty, (int, float)):
        return float(item_or_difficulty)
    if isinstance(item_or_difficulty, dict):
        if "b" in item_or_difficulty and isinstance(item_or_difficulty["b"], (int, float)):
            return float(item_or_difficulty["b"])
        return DIFFICULTY_MAP.get(item_or_difficulty.get("difficulty", "medium"), 0.0)
    return DIFFICULTY_MAP.get(item_or_difficulty, 0.0)


def resolve_a(item_or_discrimination):
    if isinstance(item_or_discrimination, (int, float)):
        return float(item_or_discrimination)
    if isinstance(item_or_discrimination, dict):
        if "a" in item_or_discrimination and isinstance(item_or_discrimination["a"], (int, float)):
            return float(item_or_discrimination["a"])
        return DISCRIMINATION_MAP.get(item_or_discrimination.get("discrimination", "medium"), 1.0)
    return DISCRIMINATION_MAP.get(item_or_discrimination, 1.0)


def resolve_c(item, n_options=None):
    """Guessing floor: the item's calibrated `c` when it has one, else 1/k.

    1/k was hardcoded, silently discarding any calibrated value. It is a *theoretical*
    floor and asserting it as fact is optimistic in both directions: real 3PL calibration
    usually lands below 1/k, because attractive distractors pull low-ability examinees
    under chance, and a genuinely guessable item can sit above it. Since c is what makes
    3PL information vanish at low theta, a wrong c is not a rounding detail — it decides
    how precisely weak candidates can be measured at all.

    Every item in the shipped bank carries c = 0.25 == 1/4, so this changes nothing today.
    It stops the next calibrated bank from being quietly overwritten.
    """
    if isinstance(item, dict):
        c = item.get("c")
        if isinstance(c, (int, float)) and 0.0 <= float(c) < 1.0:
            return float(c)
        n = n_options if n_options else len(item.get("options", [])) or 4
        return 1.0 / n
    return 1.0 / (n_options or 4)


def p_correct(theta, a, b, c):
    return c + (1 - c) / (1 + np.exp(-a * (theta - b)))


def calibrated_prior_sd(self_rating, self_reported_confidence):
    if self_rating is None:
        return SD_UNINFORMED
    return SD_HIGH_CONF if self_reported_confidence == "high" else SD_LOW_CONF


def prior_from_level(level_1to5, sd):
    mean = (level_1to5 - 3) * 1.0
    p = np.exp(-0.5 * ((GRID - mean) / sd) ** 2)
    return p / p.sum()


def eap_update(posterior, item, is_correct):
    a, b, c = item["a"], item["b"], item["c"]
    p = p_correct(GRID, a, b, c)
    x = 1.0 if is_correct else 0.0
    L = x * p + (1 - x) * (1 - p)
    L = np.clip(L, 1e-9, 1)
    post = posterior * L
    post = post / post.sum()
    theta_hat = float(np.sum(GRID * post))
    se = float(np.sqrt(np.sum((GRID - theta_hat) ** 2 * post)))
    return post, theta_hat, se


def kl_info(theta_hat, item, delta):
    a, b, c = item["a"], item["b"], item["c"]
    mask = np.abs(GRID - theta_hat) <= delta
    if not mask.any():
        return 0.0
    p_hat = np.clip(p_correct(theta_hat, a, b, c), 1e-9, 1 - 1e-9)
    p = np.clip(p_correct(GRID[mask], a, b, c), 1e-9, 1 - 1e-9)
    kl = p_hat * np.log(p_hat / p) + (1 - p_hat) * np.log((1 - p_hat) / (1 - p))
    return float(np.sum(kl))


def fisher_info(theta_hat, item):
    """Exact 3PL Fisher information at theta_hat."""
    a, b, c = item["a"], item["b"], item["c"]
    p = np.clip(p_correct(theta_hat, a, b, c), 1e-9, 1 - 1e-9)
    # I(θ) = a² · ((P−c)/(1−c))² · (1−P)/P
    if c >= 1.0:
        return 0.0
    ratio = (p - c) / (1.0 - c)
    return float((a ** 2) * (ratio ** 2) * ((1.0 - p) / p))


def expected_fisher(posterior, item):
    """Posterior-averaged Fisher — more stable than the point estimate early on."""
    infos = np.array([fisher_info(float(th), item) for th in GRID])
    return float(np.sum(posterior * infos))


def selection_score(theta_hat, q_count, item, posterior=None):
    """KL early, then Fisher — posterior-expected when the posterior is available.

    Point-estimate Fisher at theta_hat is only optimal if theta_hat is right. Early on
    it is not: SE is ~0.8-2.0, so maximising I(theta_hat) chases an estimate that is
    still moving and can lock onto a b that the next answer invalidates. Averaging
    Fisher over the posterior weights each candidate by where the examinee plausibly
    *is*, which is what the EAP estimator itself uses.

    This is what the README already documents ("posterior-expected 3PL Fisher") and
    what callers already plumb `posterior` through for; it just was never wired up.
    Falls back to the point estimate when no posterior is supplied.
    """
    if q_count < 3:
        delta = 3.0 / np.sqrt(q_count + 1)
        return kl_info(theta_hat, item, delta)
    if posterior is not None:
        return expected_fisher(posterior, item)
    return fisher_info(theta_hat, item)


def select_item(theta_hat, q_count, pool, served_ids, posterior=None, rng=None, top_k=None):
    candidates = [q for q in pool if q["id"] not in served_ids]
    if not candidates:
        return None
    # Primary: information. Tie-break: item difficulty closest to θ̂.
    candidates.sort(
        key=lambda q: (
            selection_score(theta_hat, q_count, q, posterior),
            -abs(q["b"] - theta_hat),
        ),
        reverse=True,
    )
    return candidates[choose_with_exposure_control(len(candidates), rng, top_k)]


def rank_candidates(theta_hat, q_count, pool, served_ids, posterior=None, top_n: int = 5,
                    rng=None, top_k=None):
    """Score and rank unserved items; return list of (item, score, fisher, kl).

    `score` is the criterion actually used to rank (KL early, else posterior-expected
    Fisher). `fisher` stays the point estimate at theta_hat purely for display, so the
    UI and traces can show both without changing what selection optimises.

    Exposure control lives here rather than at the point of choice, because on the LLM
    branches the LLM *is* the chooser — randomising after its pick would silently
    discard the decision those branches exist to measure. Instead the returned window
    starts at a uniform random rank in [0, k), so whoever picks the best item from the
    window administers rank o ~ U[0,k). That is exactly randomesque, and it composes
    with a coded picker and an LLM picker identically.
    """
    criterion = "KL" if q_count < 3 else ("E[Fisher]" if posterior is not None else "Fisher")
    candidates = [q for q in pool if q["id"] not in served_ids]
    if not candidates:
        return [], criterion

    delta = 3.0 / np.sqrt(q_count + 1) if q_count < 3 else None
    scored = []
    for q in candidates:
        kl = kl_info(theta_hat, q, delta) if delta else kl_info(theta_hat, q, 1.0)
        fi = fisher_info(theta_hat, q)
        info = kl if q_count < 3 else selection_score(theta_hat, q_count, q, posterior)
        scored.append((q, info, fi, kl))

    scored.sort(
        key=lambda x: (x[1], -abs(x[0]["b"] - theta_hat)),
        reverse=True,
    )
    offset = choose_with_exposure_control(len(scored), rng, top_k)
    return scored[offset:offset + top_n], criterion


def select_item_detailed(theta_hat, q_count, pool, served_ids, posterior=None):
    ranked, _ = rank_candidates(theta_hat, q_count, pool, served_ids, posterior)
    if not ranked:
        return None, 0.0
    item, score, _, _ = ranked[0]
    return item, float(score)


def theta_to_pct(theta_hat):
    """θ̂ → 0-100, as the normal-population percentile of the ability estimate.

    `level * 20` was a 5-way bucket wearing a percentage's clothes: a Novice scored
    exactly 20% and never less, θ=+2 and θ=+4 both reported 100%, and a candidate could
    improve substantially without the number moving at all. Percentile against the N(0,1)
    ability scale the model already assumes is continuous, monotone in θ̂, and actually
    means something to a reader: "better than X% of the reference population".

    Note this reports *ability*, not proportion-correct. They are different quantities and
    only the first is what a CAT estimates.
    """
    # Φ(θ) via erf, without pulling in scipy.
    from math import erf, sqrt
    return float(np.clip(100.0 * 0.5 * (1.0 + erf(float(theta_hat) / sqrt(2.0))), 0.0, 100.0))


def level_and_band(theta_hat, se):
    level = int(np.clip(round(3 + theta_hat), 1, 5))
    pct = round(theta_to_pct(theta_hat))
    bands = {1: "Novice", 2: "Developing", 3: "Competent", 4: "Proficient", 5: "Expert"}
    low_confidence = se > SE_TARGET
    return level, pct, bands[level], low_confidence


@dataclass(frozen=True)
class Convergence:
    """Why a competency stopped.

    `converged` is True only when a *measurement* criterion was met. Running out of
    questions or items is a budget outcome, not convergence, and must not be reported
    as one.
    """
    stop: bool
    reason: str = ""       # confidence | stable_level | max_questions | bank_exhausted
    converged: bool = False

    @property
    def label(self) -> str:
        return {
            "confidence": f"certainty ≥ {CONFIDENCE_TARGET:.0%}",
            "stable_level": f"same level {STABLE_WINDOW}× in a row",
            "max_questions": f"reached {MAX_QUESTIONS} questions",
            "bank_exhausted": "bank exhausted",
        }.get(self.reason, "")


def levels_stable(level_history) -> bool:
    """True when the last STABLE_WINDOW level estimates are identical."""
    if len(level_history) < STABLE_WINDOW:
        return False
    return len(set(level_history[-STABLE_WINDOW:])) == 1


def check_convergence(certainty_pct, level_history, q_count) -> Convergence:
    """Apply the three stopping rules, in precedence order.

    Bank exhaustion is handled by the caller, which is the only place that knows the
    pool. Note a stability stop can land with certainty < CONFIDENCE_TARGET: that is
    intended, and `level_and_band` will flag it low_confidence, so the report says the
    band settled without claiming the precision it did not reach.
    """
    confidence = certainty_pct / 100.0
    if confidence >= CONFIDENCE_TARGET:
        return Convergence(True, "confidence", True)
    if (
        q_count >= MIN_QUESTIONS
        and confidence >= STABILITY_FLOOR
        and levels_stable(level_history)
    ):
        return Convergence(True, "stable_level", True)
    if q_count >= MAX_QUESTIONS:
        return Convergence(True, "max_questions", False)
    return Convergence(False)


def choose_with_exposure_control(n_candidates, rng=None, top_k=None) -> int:
    """Index of the item to administer among `n_candidates` ranked best-first.

    Returns 0 (the argmax) when k <= 1, so simulation and tests stay deterministic.
    """
    k = EXPOSURE_TOP_K if top_k is None else top_k
    if k <= 1 or n_candidates <= 1:
        return 0
    r = _exposure_rng if rng is None else rng
    return int(r.integers(min(k, n_candidates)))
