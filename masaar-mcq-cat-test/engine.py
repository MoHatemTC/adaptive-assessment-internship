"""
Tuned IRT/CAT engine for the Masaar MCQ harness.

Changes vs earlier harness build (driven by live-session log failures):
- Difficulty map spans very_easy..very_hard (b ∈ {-2..+2}) so Fisher stays
  informative when θ̂ drifts outside {-1, +1}.
- Fisher uses the exact 3PL formula (not the 2PL a²p(1-p) approximation).
- Selection: KL information for the first 3 items, Fisher thereafter
  (restored after Fisher-only failed to converge in real sessions).
- select_item_detailed returns the criterion score used for selection.
"""

import numpy as np

GRID = np.arange(-4, 4.0001, 0.2)
SE_TARGET = 0.65          # empirically reachable in MCQ 3PL short tests with a spread bank.
                          # Original 0.45 stayed out of reach (live SE≈0.71 with coarse b-ladder;
                          # enriched bank sims ≈0.58–0.62 at q=12). 0.65 marks genuine convergence.
MAX_QUESTIONS = 12

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


def select_item(theta_hat, q_count, pool, served_ids, posterior=None):
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
    return candidates[0]


def rank_candidates(theta_hat, q_count, pool, served_ids, posterior=None, top_n: int = 5):
    """Score and rank unserved items; return list of (item, score, fisher, kl).

    `score` is the criterion actually used to rank (KL early, else posterior-expected
    Fisher). `fisher` stays the point estimate at theta_hat purely for display, so the
    UI and traces can show both without changing what selection optimises.
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
    return scored[:top_n], criterion


def select_item_detailed(theta_hat, q_count, pool, served_ids, posterior=None):
    ranked, _ = rank_candidates(theta_hat, q_count, pool, served_ids, posterior)
    if not ranked:
        return None, 0.0
    item, score, _, _ = ranked[0]
    return item, float(score)


def level_and_band(theta_hat, se):
    level = int(np.clip(round(3 + theta_hat), 1, 5))
    pct = level * 20
    bands = {1: "Novice", 2: "Developing", 3: "Competent", 4: "Proficient", 5: "Expert"}
    low_confidence = se > SE_TARGET
    return level, pct, bands[level], low_confidence
