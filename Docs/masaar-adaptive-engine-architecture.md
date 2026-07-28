# Masaar Adaptive Engine — Architecture Spec v1
### Statistical IRT/CAT core, judge-noise-aware, drop-in replacement for the discrete Bayesian engine in the PRD

This document specifies the adaptive engine referenced in the PRD's "Adaptive Engine — Detailed Behavior" section.
It changes the internals only — starting prior, per-answer estimation, convergence — and preserves every product-facing
contract: 1–5 level, 0–100% score, five named bands, per-competency and overall reporting.

---

## 0. Design principles

1. **Statistical selection algorithm (KL + Fisher information), not RL or meta-learning.** These require large-scale
   historical response data to train on, which the platform doesn't have yet. Statistical methods are simple to
   implement, require no training, and are the standard choice for a system without accumulated response history.
2. **Continuous latent ability (θ), not discrete 1–5 buckets internally.** Reporting stays 1–5 / 0–100% / five bands —
   only the internal estimator changes resolution.
3. **Judge noise is modeled explicitly**, not assumed away. Any LLM-graded response carries a trust weight derived
   from judge self-consistency, and that weight discounts its influence on the ability estimate.
4. **Cold start is handled by expert-set item parameters** at import time, with a path to online recalibration once
   real response data accumulates. No blocking dependency on data you don't have yet.
5. **CDM (Cognitive Diagnostic Models / Q-matrix) is a named v2 option**, not built now — your existing
   `track` / `sub_competency` fields are structurally a Q-matrix already, so this is a future upgrade, not a rewrite.

---

## 1. Measurement Model

### 1.1 Item parameters (3PL)

Every question gets three parameters instead of one difficulty label:

```
P(correct | θ) = c + (1 - c) / (1 + exp(-a * (θ - b)))
```

| Param | Meaning | Cold-start default |
|---|---|---|
| `b` (difficulty) | Ability level at which P(correct) = 50% | easy → -1.0, medium → 0.0, hard → 1.0 |
| `a` (discrimination) | How sharply the item separates ability levels near `b` | 1.0 for all items initially; item author may override to 0.6 (low) / 1.0 (med) / 1.5 (high) |
| `c` (guessing floor) | P(correct) for a candidate with θ → -∞ | MCQ: 1 / num_options; all other types: 0 |

`θ` lives on a continuous scale, roughly -4 to +4, mean 0 — this replaces the 1–5 level internally.
Mapping back to the product-facing level is a pure display transform (Section 1.4).

### 1.2 Ability grid and prior

Use a discretized grid for tractable Bayesian updates — no MCMC, no external solver needed:

```
GRID = [-4.0, -3.8, -3.6, ..., 3.8, 4.0]   # 41 points, step 0.2
```

Starting prior, per competency, blends CV estimate and self-rating exactly as the PRD specifies, just expressed on
the θ scale instead of 1–5:

```
level_prior = round(0.5 * cv_estimate + 0.5 * self_rating)   # if no CV: self_rating alone; if neither: 3
theta_prior_mean = (level_prior - 3) * 1.0    # maps 1..5 -> -2..2
```

**The mean is calibrated by the PRD's formula above — unchanged. The prior's *width* (its confidence) was not
calibrated in the original spec, and should be:** a fixed sd regardless of intake quality means a candidate whose
CV and self-rating closely corroborate each other starts the test exactly as uncertain, in the engine's eyes, as
one with no CV at all and a single unverified self-rating. Calibrate the width by how much signal actually
went into the mean:

```
SD_UNINFORMED   = 2.0    # neither self-rating nor CV available (PRD's default-to-3 case)
SD_SELF_ONLY    = 1.7    # self-rating alone, no corroborating signal
SD_MIN          = 0.9    # floor — even close agreement shouldn't make the prior *too* confident
SD_MAX          = 1.9    # ceiling for the disagreement case
DISAGREEMENT_SLOPE = 0.25

function calibrated_prior_sd(self_rating, cv_estimate):
    if self_rating is None and cv_estimate is None:
        return SD_UNINFORMED
    if cv_estimate is None:
        return SD_SELF_ONLY
    disagreement = abs(cv_estimate - self_rating)          # 0..4 on the 1-5 scale
    return clamp(SD_MIN + disagreement * DISAGREEMENT_SLOPE, SD_MIN, SD_MAX)

prior = Normal(mean = theta_prior_mean, sd = calibrated_prior_sd(self_rating, cv_estimate))
```

`SD_MIN` exists as a deliberate floor, not just a nice-to-have: an overly confident prior recreates the
lucky-guess-snowball failure mode from the edge-case simulation (Section 8 of the accompanying discussion) —
if the starting point is wrong and the prior is too narrow, item selection locks onto the wrong region before
enough evidence has accumulated to correct it. Simulation across 200 synthetic candidates confirmed the
direction of the effect: holding true θ fixed and only varying prior width from `SD_UNINFORMED` (2.0) down to
close-agreement (0.9), average final SE improved from 0.614 to 0.521 and average |error| improved from 0.427
to 0.342, for the same 10-question budget. It did not reduce the number of questions needed within the
`MAX_QUESTIONS=10` cap — treat this as "a more accurate result for the same test length," not "a shorter test."

Log `prior_sd` alongside `theta_hat`/`se` in `session_competency_results` (Section 7 schema) — this is useful
both for debugging a specific candidate's session and for later checking whether the calibration constants above
need retuning once real self-rating/CV data accumulates.

### 1.3 Per-answer update (EAP, judge-uncertainty-weighted)

Replaces the PRD's discrete posterior update. For MCQ, `judge_weight = 1.0` always (exact key match, no LLM noise).
For LLM-graded types (coding approach, open-ended/voice/data-analysis rubric), `judge_weight` comes from Section 5.2.

```
function update_posterior(posterior, item, response_score, judge_weight):
    for theta in GRID:
        p = item.c + (1 - item.c) / (1 + exp(-item.a * (theta - item.b)))
        # response_score is 0-5 for rubric items, or 0/1 for MCQ/pass-fail
        normalized = response_score / max_score            # -> [0,1]
        raw_likelihood = p if normalized >= 0.5 else (1 - p)   # simple binary collapse for v1
        # OR for a graded (non-binary) treatment, see Section 1.3.1
        likelihood[theta] = raw_likelihood ** judge_weight  # judge_weight in (0,1]; 1 = full trust

    posterior_new = normalize(posterior * likelihood)   # elementwise multiply, renormalize to sum 1
    theta_hat = sum(theta * posterior_new[theta] for theta in GRID)             # EAP estimate
    se = sqrt(sum((theta - theta_hat)**2 * posterior_new[theta] for theta in GRID))  # posterior SD
    return posterior_new, theta_hat, se
```

**Why `likelihood ** judge_weight`:** raising a probability to a power < 1 flattens it toward 0.5 (uniform) —
exactly "trust this observation less" without redesigning the Bayesian core. `judge_weight = 1.0` for a
clean/confident judge call, decaying toward ~0.3 for a highly disputed one.

#### 1.3.1 Graded response variant (v1.5)

For a more faithful treatment of the 0–5 rubric score than the binary collapse above, treat `normalized` directly
as a soft label and use it to interpolate:

```
raw_likelihood = normalized * p + (1 - normalized) * (1 - p)
```

This rewards partial credit properly. Ship the binary-collapse version first (simpler, matches the PRD's existing
"score/level" language); swap this in once the grading pipeline is stable.

### 1.4 Mapping back to the product surface (unchanged contract)

```
level = clamp(round(3 + theta_hat), 1, 5)          # same 1-5 scale as PRD
pct   = level * 20                                  # same as PRD's pct = level * 20
band  = five_band_lookup(pct)                       # unchanged five named bands
low_confidence_flag = (se > SE_TARGET) or hit_max_questions_without_converging
```

---

## 2. Selection Algorithm

### 2.1 Early stage (questions 1–3 per competency): KL information

Fisher information is unreliable when `theta_hat` is still far from the true value — exactly the situation for the
first few questions of a short test. Use global KL information instead, integrated over a shrinking window around
the current estimate:

```
delta = 3 / sqrt(question_count_so_far + 1)   # wide window early, narrows as t grows

function kl_divergence(theta, theta_hat, item):
    p_hat = P(correct | theta_hat, item)
    p     = P(correct | theta, item)
    return p_hat * log(p_hat / p) + (1 - p_hat) * log((1 - p_hat) / (1 - p))

function kl_information(theta_hat, item):
    return integrate(kl_divergence(theta, theta_hat, item) for theta in [theta_hat - delta, theta_hat + delta])
    # numerically: sum over GRID points within the window, trapezoidal rule
```

### 2.2 Later stage (questions 4+): Fisher information

Once `theta_hat` has had a few observations to stabilize, switch to the sharper, cheaper local criterion:

```
function fisher_information(theta_hat, item):
    p = P(correct | theta_hat, item)
    return (item.a ** 2) * p * (1 - p)
    # 3PL exact form (from item params a, b, c) if you want the full expression instead of the 2PL approximation:
    # I = (1-c) * a^2 * exp(-a*(theta-b)) / ((1 + exp(-a*(theta-b)))^2 * (1 - c + c*(1+exp(-a*(theta-b)))))
```

### 2.3 Combined selection rule

```
function select_next_item(theta_hat, question_count, candidate_pool, served_ids):
    candidates = [q for q in candidate_pool if q.id not in served_ids]

    if question_count < 3:
        candidates.sort(key = lambda q: kl_information(theta_hat, q), reverse = True)
    else:
        candidates.sort(key = lambda q: fisher_information(theta_hat, q), reverse = True)

    # type-variety rule from PRD, preserved: among top-N by information, prefer least-asked tool_type
    top_n = candidates[:5]
    top_n.sort(key = lambda q: tool_type_count[q.tool_type])
    return top_n[0]
```

---

## 3. Convergence (stopping rule)

Replaces the PRD's `confidence ≥ 0.90 OR same level 3x OR 10 questions` with a single principled criterion plus
the existing hard ceiling:

```
SE_TARGET = 0.35        # tune empirically once you have pilot data; start here
MAX_QUESTIONS = 10       # unchanged from PRD

stop_competency = (se <= SE_TARGET) or (question_count >= MAX_QUESTIONS)
low_confidence_flag = (question_count >= MAX_QUESTIONS) and (se > SE_TARGET)
```

The old "3-in-a-row same level" rule is no longer needed — SE naturally requires multiple consistent responses to
shrink, so the intuition it encoded is already inside the math. One fewer magic number in the spec.

---

## 4. Question Bank / Item Parameters

### 4.1 Cold start

At import time, item authors optionally set `discrimination` (low/med/high → 0.6/1.0/1.5) in addition to the
existing easy/medium/hard difficulty label. If omitted, default to medium (a=1.0) — fully backward-compatible with
the current bank format, no required schema migration on existing rows.

### 4.2 Bank-exhaustion generated items

Per the PRD, when a competency's bank runs out, the engine generates additional open-ended questions. These get
**no calibrated parameters** — assign `a = 0.7` (below-average discrimination, reflecting real uncertainty) and
`b = round(theta_hat)` (targeted at the current estimate). This keeps the Bayesian update honest: an uncalibrated
item shouldn't be allowed to swing the posterior as hard as a well-established one.

### 4.3 Online recalibration (v1.5/v2 — not required for v1 launch)

Once response data accumulates, run a lightweight Elo-style updater as a background job:

```
K = 0.1   # learning rate, tune empirically
function update_item_difficulty(item, theta_hat_before_this_item, was_correct):
    expected = P(correct | theta_hat_before_this_item, item)
    surprise = (1 if was_correct else 0) - expected
    item.b = item.b - K * surprise   # correct when expected-low -> item was easier than thought -> b decreases
```

No schema change needed beyond what's already logged (Section 7) — this reads `ai_logs` / `answers` history and
writes back to `question_bank.b`.

---

## 5. Test Control

### 5.1 Exposure control — v1.5

Not required for launch (bank/candidate volume too low to matter yet), but flag in the PRD as a known follow-up.
Sympson-Hetter is the standard approach: instead of always serving the top-ranked item, pass the selection through
a probability filter so high-information items aren't shown to every candidate. Revisit once usage volume makes
item leakage a real risk.

### 5.2 Robustness — judge ensemble (v1, required)

Directly addresses LLM-judge noise. For every LLM-graded response (coding approach, open-ended/voice/data-analysis
rubric):

```
function grade_with_uncertainty(response, rubric, judge_fn):
    samples = [judge_fn(response, rubric) for _ in range(3)]   # 3 independent judge calls
    median_score = median(samples)
    spread = max(samples) - min(samples)                        # 0-5 scale, so spread in [0,5]

    judge_weight = clamp(1.0 - (spread / 5.0) * 0.7, 0.3, 1.0)   # tight agreement -> ~1.0; wide split -> ~0.3
    return median_score, judge_weight
```

For coding items specifically: use the sandboxed test-case pass rate as a partial anchor — if test cases pass but
the LLM judge's "approach quality" score disagrees sharply with that objective signal, treat it as a wide-spread
case (lower `judge_weight`) even if the 3 judge samples individually agreed with each other, since the objective
signal is the more trustworthy one.

Log every judge sample (not just the median) to `ai_logs` — this is what enables Section 5.3's audit and any future
judge-calibration work.

### 5.3 Fairness — seed bank review checklist (v1, cheap, do before launch)

Once the seed bank is built, before first candidate cohort:
- Sort items by discrimination (`a`) descending — review the top decile for any items whose correctness depends on
  something outside the tested skill (idiom, a specific framework's naming conventions, cultural references).
- Spot-check that CV-based personalization (PRD feature 11) doesn't inadvertently make items easier/harder for
  candidates with more detailed CVs — the rewrite must be answer-invariant per the PRD's hard rule; verify this
  holds under a few real CV samples, not just synthetic tests.

---

## 6. State machine (extends the PRD's deliverable list)

```
INIT
  -> load prior (Section 1.2) per measured competency
  -> state per competency: {posterior[GRID], theta_hat, se, question_count=0, served_ids=[]}

ASK
  -> for each competency not yet converged:
       item = select_next_item(theta_hat, question_count, bank_pool, served_ids)   [Section 2]
       serve item (personalized per PRD's answer-invariant rewrite)
       served_ids.append(item.id)

GRADE
  -> MCQ: exact match, judge_weight = 1.0
  -> coding: sandbox test cases + LLM judge on approach, judge_weight via Section 5.2 (anchored to test pass rate)
  -> open-ended/voice/data-analysis: rubric via LLM judge, judge_weight via Section 5.2

ESTIMATE
  -> posterior, theta_hat, se = update_posterior(posterior, item, score, judge_weight)   [Section 1.3]
  -> question_count += 1

CONVERGE (check per competency)
  -> if se <= SE_TARGET or question_count >= MAX_QUESTIONS: mark competency done   [Section 3]
  -> else: loop back to ASK for this competency

FINALIZE (once all competencies done)
  -> level, pct, band, low_confidence_flag per Section 1.4
  -> overall_pct = mean(per-competency pct)   [unchanged from PRD]
  -> persist, generate report, send email   [unchanged from PRD]
```

---

## 7. Schema diff

Additive only — no destructive migration on existing tables.

```sql
-- question_bank: add IRT parameters (nullable, backward compatible)
ALTER TABLE question_bank ADD COLUMN discrimination FLOAT DEFAULT 1.0;   -- 'a' param
ALTER TABLE question_bank ADD COLUMN guessing FLOAT DEFAULT 0.0;         -- 'c' param, set per tool_type at import
-- existing 'difficulty' (easy/medium/hard) maps to 'b' via the fixed table already in the PRD (2/3/4 -> shift to -1/0/1)

-- session_competency_results: replace/extend the discrete level_history with continuous state
ALTER TABLE session_competency_results ADD COLUMN posterior JSONB;       -- array of 41 floats, the GRID posterior
ALTER TABLE session_competency_results ADD COLUMN theta_hat FLOAT;
ALTER TABLE session_competency_results ADD COLUMN se FLOAT;
ALTER TABLE session_competency_results ADD COLUMN prior_sd FLOAT;        -- calibrated starting confidence, §1.2
-- 'level', 'pct', 'band', 'low_confidence_flag' columns already in PRD schema: unchanged, now derived from theta_hat

-- answers: log judge ensemble detail for audit + future recalibration
ALTER TABLE answers ADD COLUMN judge_samples JSONB;    -- e.g. [3, 4, 4] raw scores from the 3 judge calls
ALTER TABLE answers ADD COLUMN judge_weight FLOAT;     -- the derived trust weight fed into the likelihood
```

No changes required to `question_sets`, `assessments`, `final_reports`, `email_logs` — this spec is scoped entirely
to the adaptive engine's internals per the PRD's existing deliverable boundaries.

---

## 8. Phased rollout

| Phase | Scope | Depends on |
|---|---|---|
| **v1 (this spec, launch-blocking)** | Sections 1–4.2, 5.2, 5.3, 6, 7 | Nothing beyond current PRD scope — same deliverable list, different internals |
| **v1.5** | Section 1.3.1 (graded likelihood), Section 5.1 (exposure control) | A few weeks of real usage to know if either matters in practice |
| **v2** | Section 4.3 (Elo recalibration), CDM/Q-matrix path (Section 0.5) | Enough response volume to make online calibration meaningful (roughly 100+ responses per competency as a starting order of magnitude — tune once you see real data) |

---

*This spec preserves every functional and non-functional requirement in the original PRD. The only internal change
is the estimator (discrete 1–5 Bayesian → continuous EAP/IRT) and the selection criterion (difficulty-matching →
KL/Fisher information) — both swap-in replacements behind the same state-machine shape the PRD already specifies.*
