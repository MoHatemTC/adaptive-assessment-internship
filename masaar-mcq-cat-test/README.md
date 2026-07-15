# Masaar MCQ CAT Test Harness

Standalone Streamlit app to validate the Masaar IRT/CAT adaptive engine on MCQ-only questions.

## Quick start

```bash
cd masaar-mcq-cat-test
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

By default the app loads **`enriched_bank.json`** (122 unique items across 5 competencies).
You can also upload any bank matching the JSON format below.

## Tuned CAT behaviour (post live-session fixes)

Live `assessment.log` showed failures this build addresses:

| Failure | Fix |
|---|---|
| θ̂≈2.5 but items only had `b∈{-1,0,1}` → Fisher≈0.02, SE stalled ~0.71 | Extended difficulty ladder `very_easy…very_hard` → `b∈{-2…+2}` + richer bank |
| Certainty % fell after correct answers | Certainty is now monotonic in SE reduction (intake only blends at q=0) |
| Streamlit crash on `sim_comp` overwrite | Store sim results under `sim_comp_name` |
| Clone synthesis recycling stems | Curated unique bank; clone path only fills gaps |

Selection: **KL information for questions 1–3**, then **posterior-expected 3PL Fisher**.
Stop when `SE ≤ 0.65` or `12` questions (recalibrated from unreachable 0.45@10 with coarse banks).

## Full LLM CAT controller (OpenAI)

MCQ **grading stays deterministic** (exact index match). The **LLM updates theta/SE and selects the next item** from validated candidates:

A CAT step is **ordered**: a response updates θ, and the *updated* θ decides which item
is most informative next. So the step is two LLM calls, not one:

| # | Actor | Does |
|---|---|---|
| 1 | **LLM** | Updates θ̂/SE from the graded response (3PL score function + Newton update) |
| 2 | code | Checks the direction invariant; applies the **stopping rule** |
| 3 | code | Ranks unserved items by KL/Fisher **at the LLM's new θ̂** → top-5 shortlist |
| 4 | **LLM** | Picks from that scored shortlist, applies tie-breaks, rewords the stem |

The model still makes every decision a person would call a decision. What it no longer
does is guess at quantities the engine can compute exactly.

### Why it changed

The previous design asked for the update **and** the selection in one call. That makes
the ordering above impossible to honour — the engine cannot score candidates at a θ it
hasn't been told yet — so the call shipped raw `a`/`b`/`c` for all ~24 unserved items and
let the model free-pick. Nothing computed how much information any candidate carried, and
*"prefer items whose difficulty is informative near theta"* left the model eyeballing
`|b − θ|`, which ignores discrimination and guessing entirely. That is not adaptive
testing. Two calls cost more per question; the branch is an experiment, and correctness
wins.

### Stopping is a rule, not an opinion

The LLM used to end the assessment by returning `stop=true`, and `app.py` obeyed it
(`stop = stop or controller.stop`). A stopping rule is part of the measurement: stop early
and the SE guarantee the report rests on is void. The model's `should_stop` is still
collected, traced, and counted in the sidebar as a **stop disagreement** — a genuinely
interesting signal — but code decides.

### What is validated, and what deliberately is not

| Check | Status | Why |
|---|---|---|
| Correct → θ̂ must not fall; incorrect → θ̂ must not rise | **Enforced** (falls back to coded EAP) | Exact invariant: the 3PL likelihood is monotone in θ. Held in 0/8000 coded EAP updates. |
| SE must not increase | **Not enforced** | *Looks* like an invariant but isn't — SE rose in **23%** of those 8000 updates (up to +0.28) after a surprising response. Enforcing it would reject correct maths. |
| `selected_id` ∈ shortlist | **Enforced** | Otherwise selection isn't information-driven. |
| Deviation from coded EAP > 0.35 | **Warned, never rejected** | The Newton update approximates the grid EAP; a correct implementation trips 0.35 ~2.9% of the time. |

The LLM is never shown the coded EAP result — otherwise this branch would measure copying
rather than capability. `test_approach.py` asserts the coded θ̂ appears in no payload.

### Adaptive rephrasing and its guardrail

Every rewrite is validated by `rephrase_guard.py` before display and rejected if it
restates the key's unique wording (measured against how much it echoes the distractors),
drops an identifier the item turns on, or changes length drastically. Rejections fall back
to the original calibrated stem and surface in the UI and log. Rephrasing is an
uncalibrated deviation from IRT — untick it on the setup screen for a clean run.

### Known property of this approach

The LLM returns `(theta, se)`, so `posterior_from_theta_se` rebuilds a **Gaussian** belief
from two moments and the true posterior shape is lost. Selection integrates Fisher
information over that belief, so it runs on the LLM's summary rather than an exact
posterior. Approach 1 keeps the full grid posterior.

### Tests

```bash
python test_approach.py         # 30 checks, no API key needed
python test_rephrase_guard.py
```

### Configure

```bash
cp .env.example .env
# edit .env:
# OPENAI_API_KEY=sk-proj-...
# OPENAI_MODEL=gpt-4o-mini
# LANGFUSE_SECRET_KEY=sk-lf-...
# LANGFUSE_PUBLIC_KEY=pk-lf-...
# LANGFUSE_BASE_URL=https://cloud.langfuse.com
pip install -r requirements.txt
streamlit run app.py
```

Setup screen shows connection status and a toggle for the full LLM CAT controller.
If OpenAI fails, the app falls back to deterministic CAT selection/math safeguards.

For Streamlit deployment, use `masaar-mcq-cat-test/streamlit_app.py` as the main file path. See `DEPLOYMENT.md`.

## Langfuse tracing

When Langfuse keys are present in `.env`, the app records comparable trace events for:

- session start
- next-item selection
- LLM selection responses
- answer updates
- competency finalization

Each trace includes `approach_id`, `math_actor`, and `selection_actor` metadata so the three experiment branches can be compared in Langfuse.

## Question bank JSON

```json
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
```

| Field | Notes |
|---|---|
| `difficulty` | `very_easy` / `easy` / `medium` / `hard` / `very_hard` → b ∈ {-2,-1,0,1,2}. Legacy easy/medium/hard still work. |
| `discrimination` | `low` / `medium` / `high` → a ∈ {0.6, 1.0, 1.5} |
| `c` | Not in JSON — set as `1/len(options)` |

### Bundled banks

| File | Contents |
|---|---|
| `enriched_bank_cat.json` | **Preferred** — 120 MCQs, 5 competencies × 24, calibrated `a`/`b`/`c` per item, cueing-controlled |
| `enriched_bank.json` | Earlier 122-item bank. Labels only (no numeric IRT), and heavily cued — see below |
| `sample_bank.json` | Small 21-item smoke bank (2 competencies) |

## `enriched_bank_cat.json` — the CAT-ready bank

Built from `AI_Internship_Technical_QuestionBank.xlsx` (62 MCQ rows, options rewritten;
58 new items authored to fill the ladder). Every item carries **numeric `a`, `b`, `c`**, so
`engine.resolve_a/resolve_b` use them directly instead of collapsing onto the label maps.

### Why the earlier bank was not usable for CAT

| Property | `enriched_bank.json` | `enriched_bank_cat.json` |
|---|---|---|
| Numeric IRT params | none — 5 discrete `b` values via label fallback | per-item, `b` continuous over ±2.4 |
| Key position A/B/C/D | 3 / 112 / 7 / 0 — **92% "B"** | 30 / 30 / 30 / 30 — 25% each |
| Longest option is the key | 109/122 (**89%**, p≈6e-51) | 31/120 (26%, p=0.83 — chance) |
| Options within 1.4× length | 7/122 | 120/120 |

A candidate who always picked "the long B" scored ~89% on the old bank without reading a stem.

### Design rules

- **24 items per competency**: 8 sub-competencies × 3, ladder 5/5/4/5/5 across `very_easy…very_hard`.
- **`a` is independent of `b`.** Discrimination is spread across every difficulty band
  (a very_easy item can still separate sharply). Pinning high `a` to high `b` starves the
  low-θ end of information and leaves weak candidates measured with blunt items —
  `validate_bank.py` fails on `corr(difficulty, a) > 0.75` for this reason.
- **Within a band, the sharpest item takes the outer edge** of the `b` range, since
  information is scarcest at the ladder's extremes.
- **Distractors are misconceptions, not filler.** Each is a specific error a candidate
  could hold; `misconceptions[i]` records which, with `"CORRECT"` at the keyed index.
- `rationale` and `misconceptions` are extra fields — the app ignores them.

### Regenerating and checking

```bash
python build_enriched_bank.py <authored_items_dir> enriched_bank_cat.json
python validate_bank.py enriched_bank_cat.json   # exit 1 on any violation
python simulate_cat.py enriched_bank_cat.json 300
```

`validate_bank.py` treats "no cueing" as a hypothesis to test rather than a judgement call:
chi-square on key position, exact binomial on longest-is-key **and** shortest-is-key, and a
mean length-rank z-test (a bank can pass "is longest" while the key is reliably *second*
longest — equally learnable). It also walks the ability range and fails if the best available
item at any θ is too blunt to measure there.

### Measured behaviour (300 simulated candidates per point)

| Operating condition | RMSE(θ̂) | Reaches SE ≤ 0.65 in 12 items |
|---|---|---|
| No self-rating (pessimistic bound) | 0.72 | 16% |
| Honest self-rating (expected case) | 0.63 | 35% |

θ̂ is **essentially unbiased at every ability level** (bias −0.11…+0.10 in the expected case),
including θ=±2.5.

**Known limit, not a bank defect:** SE ≤ 0.65 is reached ~80–90% of the time at θ≈+2 but
rarely below θ≈0. With 4-option MCQs, `c`=0.25, so as ability falls P(correct) → 0.25 and the
response becomes mostly guessing — 3PL information genuinely vanishes at low θ. No bank fixes
this. Raising precision for weak candidates needs more items (`MAX_QUESTIONS`), fewer
guessable options (5 options → c=0.20), or accepting `SE_TARGET` > 0.65 at the low end.

## Files

| File | Purpose |
|---|---|
| `app.py` | Streamlit UI |
| `engine.py` | IRT/CAT core (EAP update, KL→Fisher selection) |
| `certainty.py` | Combined assessment certainty % |
| `selection_pipeline.py` | LLM procedural + deterministic item selection |
| `llm_client.py` | OpenAI client for procedural selection |
| `tracing.py` | Optional Langfuse trace events for approach comparison |
| `bank_synth.py` | Gap-fill only when a bank lacks coverage |
| `build_enriched_bank.py` | Assigns per-item `a`/`b`/`c`; rebalances key positions (seeded) |
| `validate_bank.py` | Gates schema, ladder, information coverage, and cueing. Exit 1 on failure |
| `simulate_cat.py` | Replays the real engine against known-θ candidates to score recovery |
| `assessment.log` | SELECT/UPDATE/SYNTH traces |
