# Cursor Prompt — Streamlit MCQ Adaptive Testing Harness (v1 test build)

Paste everything below into Cursor as the task prompt. It builds a standalone Streamlit app to validate the
CAT/IRT adaptive engine on MCQ-only questions before it gets wired into the full Masaar platform. The core
estimation/selection logic below has already been simulated and validated — implement it as-is rather than
re-deriving it, and only the known edge-case fixes noted in Section 6 should change the numbers.

---

## 1. Goal

Build a Streamlit app that runs one adaptive MCQ assessment session end-to-end:
self-rating intake → adaptive question loop (KL info early, Fisher info later) → live ability/uncertainty
visualization after every answer → convergence → final report (level, %, band, low-confidence flag).

This is a **test harness**, not the production platform — no auth, no database, no email. Session state lives
in Streamlit's `st.session_state`. I will supply my own question bank as a JSON file (format specified in
Section 2) and upload it through the app.

## 2. Question bank format (I will provide this file — build the loader to expect exactly this shape)

```json
[
  {
    "id": "q001",
    "competency": "Backend Engineering",
    "sub_competency": "API Design",
    "difficulty": "medium",
    "discrimination": "medium",
    "stem": "Which HTTP status code indicates a successful resource creation?",
    "options": ["200 OK", "201 Created", "204 No Content", "301 Moved Permanently"],
    "answer_index": 1
  }
]
```

- `difficulty` ∈ {"easy","medium","hard"} → maps to `b` ∈ {-1.0, 0.0, 1.0}
- `discrimination` ∈ {"low","medium","high"} → maps to `a` ∈ {0.6, 1.0, 1.5}. Default to `"medium"` if the
  field is missing (backward-compatible with a simpler bank that only has `difficulty`).
- `c` (guessing floor) is **not** in the JSON — compute it in code as `1 / len(options)` per question, since
  it depends on option count, not something a bank author should hand-set.
- Group questions by `competency` — the app runs one adaptive sub-test per competency present in the bank.

## 3. Core engine — implement this exactly, do not modify the math

This is the validated core (already simulated against multiple edge cases). Put it in `engine.py`:

```python
import numpy as np

GRID = np.arange(-4, 4.0001, 0.2)
SE_TARGET = 0.45          # see Section 6 note #1 — recalibrated from the original 0.35 default
MAX_QUESTIONS = 10

DIFFICULTY_MAP = {"easy": -1.0, "medium": 0.0, "hard": 1.0}
DISCRIMINATION_MAP = {"low": 0.6, "medium": 1.0, "high": 1.5}

def p_correct(theta, a, b, c):
    return c + (1 - c) / (1 + np.exp(-a * (theta - b)))

SD_UNINFORMED = 2.0   # no self-rating given at all (shouldn't normally happen, but handle it)
SD_LOW_CONF = 1.7     # self-rating given, but candidate marked themselves "not very confident"
SD_HIGH_CONF = 1.1    # self-rating given, candidate marked themselves "confident"

def calibrated_prior_sd(self_rating, self_reported_confidence):
    # self_reported_confidence: "low" or "high", from the extra intake question below.
    # This harness has no CV signal, so width is calibrated purely from how confident the
    # candidate says they are in their own self-rating -- a cheap proxy for the full CV+self-rating
    # agreement calibration used in the production spec (see architecture doc §1.2).
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
    post = posterior * L                    # judge_weight = 1.0 always for MCQ — exact match, no LLM noise
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
    a, b, c = item["a"], item["b"], item["c"]
    p = np.clip(p_correct(theta_hat, a, b, c), 1e-9, 1 - 1e-9)
    return (a ** 2) * p * (1 - p)

def select_item(theta_hat, q_count, pool, served_ids):
    candidates = [q for q in pool if q["id"] not in served_ids]
    if not candidates:
        return None
    if q_count < 3:
        delta = 3 / np.sqrt(q_count + 1)
        candidates.sort(key=lambda q: kl_info(theta_hat, q, delta), reverse=True)
    else:
        candidates.sort(key=lambda q: fisher_info(theta_hat, q), reverse=True)
    return candidates[0]

def level_and_band(theta_hat, se):
    level = int(np.clip(round(3 + theta_hat), 1, 5))
    pct = level * 20
    bands = {1: "Novice", 2: "Developing", 3: "Competent", 4: "Proficient", 5: "Expert"}
    low_confidence = se > SE_TARGET
    return level, pct, bands[level], low_confidence
```

## 4. App flow (`app.py`)

**Screen 1 — Setup**
- File uploader for the question bank JSON (Section 2 format).
- Validate on upload: every competency has at least ~8-10 items spanning all three difficulty levels
  (warn in the UI if any competency is missing a full difficulty spread — this was a real failure mode we
  found in simulation: a bank with no difficulty variety badly degrades accuracy for candidates far from the
  bank's average difficulty).
- Self-rating intake: for each competency found in the bank, a 1-5 slider ("rate your own proficiency").
- Immediately below each slider, a small radio/toggle: "How confident are you in this self-rating?" → Low / High.
  This feeds `calibrated_prior_sd` (Section 3) — it's a stand-in for the CV-agreement signal used in the full
  production spec, letting this harness exercise calibrated-confidence starts without needing a CV pipeline.
  Store both values per competency in `st.session_state` (e.g. `self_rating["Backend"] = 4`,
  `self_confidence["Backend"] = "high"`).
- "Start Assessment" button — on click, call `prior_from_level(level, calibrated_prior_sd(rating, confidence))`
  for each competency to build its starting posterior, instead of a fixed sd.

**Screen 2 — Adaptive loop (one competency at a time)**
- Show current competency name and question count (e.g. "Question 3 of up to 10").
- Render the MCQ stem and options as `st.radio`.
- On submit: grade (exact index match), call `eap_update`, then `select_item` for the next question.
- **Live sidebar**, updated after every answer:
  - Current θ̂ and SE as numbers
  - A line chart of the posterior distribution over `GRID` (use `st.line_chart` or matplotlib) — this should
    visibly narrow after each answer, same as the posterior-narrowing behavior we validated in simulation.
  - A small table logging every question so far: item id, difficulty, correct/incorrect, θ̂ after, SE after.
- When this competency converges (`se <= SE_TARGET` or `question_count >= MAX_QUESTIONS`), show a brief
  "Competency complete" message and move to the next competency, or to Screen 3 if all are done.

**Screen 3 — Final report**
- Per competency: level (1-5), pct, band, low_confidence flag (rendered as a visible warning badge if True —
  do not just print it as plain text, this flag matters and should be visually distinct).
- A results table across all competencies.
- A "Run again" button that resets `st.session_state` and returns to Screen 1.

**Sidebar always visible — "Debug / Simulate" expander**
- A toggle to run a synthetic candidate through the same engine with a chosen true θ, using randomized
  correct/incorrect draws from `p_correct`, so I can sanity-check the engine's behavior without manually
  answering questions myself. Show the same live chart for the simulated run.

## 5. File structure to produce

```
masaar-mcq-cat-test/
├── app.py                # Streamlit UI, all three screens
├── engine.py             # Section 3 code, unmodified
├── sample_bank.json      # a small 15-20 item synthetic bank spanning 2 competencies, for smoke-testing
├── requirements.txt      # streamlit, numpy, pandas
└── README.md             # how to run: streamlit run app.py, plus the bank JSON format from Section 2
```

## 6. Known edge cases — implement these guards, they came from real simulation failures

1. **SE_TARGET has been set to 0.45, not the naive 0.35** — a 0.35 target was tested and found to be
   essentially unreachable even after 40 questions with a well-behaved bank. Don't "fix" this back to 0.35.
2. **Selection in the first question should not fully chase the point estimate.** For `q_count == 0`, use the
   widest KL window (already handled by `delta = 3/sqrt(1)` in the code above) — do not shrink this further.
3. **Guessing floor must be set from actual option count**, not assumed to be 0.25 — a bank with some
   3-option and some 5-option questions needs per-question `c = 1/len(options)`.
4. **If a competency's bank has fewer than 4 unserved items remaining, stop early and flag
   `bank_exhausted = True`** in that competency's result rather than crashing on `select_item` returning `None`.
5. **Display the low_confidence flag prominently** on the final report — this is a product requirement, not
   just a debug detail, per the finding that an erratic/lucky candidate can otherwise look artificially
   confident on the raw level number alone.
6. **Never let prior sd go below `SD_HIGH_CONF = 1.1`.** A narrower prior converges to a more accurate final
   estimate on average (validated: ~0.34 vs ~0.43 average error over 200 simulated candidates), but too narrow
   and a single early wrong answer can lock item selection onto the wrong region before there's enough evidence
   to correct it — the same failure mode as the lucky-guess snowball found earlier. The floor exists to prevent
   that; don't remove it even if it looks like it's "limiting" the confidence calibration.

## 7. Explicitly out of scope for this build

No coding/voice/data-analysis question types, no LLM judge, no judge-weighting logic, no database, no auth,
no email, no exposure control, no online item recalibration. Those come later once this MCQ-only core is
validated. Keep `engine.py` free of any code paths for those types — a clean MCQ-only implementation now
makes the eventual merge into the full Masaar platform easier to review.

---

Build this now. Ask me before making any change to the formulas in Section 3 — everything else (UI layout,
chart library choice, file organization) is your call.
