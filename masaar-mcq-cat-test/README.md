# Masaar MCQ CAT Test Harness

Standalone Streamlit app to validate the Masaar IRT/CAT adaptive engine on MCQ-only questions.

## Quick start

```bash
cd masaar-mcq-cat-test
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # add OPENAI_API_KEY — the app will not start without it
streamlit run app.py
```

The app loads **`enriched_bank_cat.json`** (120 calibrated items across 5 competencies).
The bank is fixed and the LLM is required — see [The LLM is required](#the-llm-is-required).

## Tuned CAT behaviour (post live-session fixes)

Live `assessment.log` showed failures this build addresses:

| Failure | Fix |
|---|---|
| θ̂≈2.5 but items only had `b∈{-1,0,1}` → Fisher≈0.02, SE stalled ~0.71 | Extended difficulty ladder `very_easy…very_hard` → `b∈{-2…+2}` + richer bank |
| Certainty % fell after correct answers | Certainty is now monotonic in SE reduction (intake only blends at q=0) |
| Streamlit crash on `sim_comp` overwrite | Store sim results under `sim_comp_name` |
| Clone synthesis recycling stems | Curated unique bank; clone path only fills gaps |

Selection: **KL information for questions 1–3**, then **posterior-expected 3PL Fisher**.

## Convergence

A competency ends when **any** of three rules fires. `engine.check_convergence` owns all
three; `app.should_stop` adds bank exhaustion, which only it can see.

| Rule | Condition | Reported as converged? |
|---|---|---|
| Confidence | `certainty ≥ 90%` (`CONFIDENCE_TARGET`) | yes |
| Stable level | same level `3×` in a row (`STABLE_WINDOW`), after `6` questions (`MIN_QUESTIONS`) and only at `certainty ≥ 80%` (`STABILITY_FLOOR`) | yes, but flagged low-confidence when `SE > 0.65` |
| Budget | `12` questions (`MAX_QUESTIONS`) | **no** — running out is not convergence |

The confidence rule is *exactly* the old `SE ≤ 0.65` test: `posterior_certainty_pct` maps
`SE_TARGET` to precisely 90% and caps at 89% above it. Restating it in confidence units
puts the stopping rule in the same units as the candidate's report; it does not change
behaviour.

### Why the stable-level rule has two guards

`level = round(3 + θ̂)` is a 5-way bucket, and θ̂ moves slowly, so the bucket repeats
long before the estimate is precise. Measured over 200 candidates × 7 θ × 5 competencies:

| Config | RMSE | mean items | mean SE | stability stops |
|---|---:|---:|---:|---:|
| No guards beyond `MIN_QUESTIONS=6` | 0.825 | 7.2 | 0.853 | **98%** |
| `+ STABILITY_FLOOR = 0.80` (shipped) | 0.747 | 9.1 | 0.781 | 87% |
| Stability rule removed | 0.712 | 11.8 | 0.730 | 0% |

Without the floor the rule fired in 98% of sessions at SE ≈ 0.85 and swallowed the
confidence rule entirely (which fired in 0.9%). `MIN_QUESTIONS` alone was not enough.

**The stability rule is a test-length optimisation, not evidence of precision.** It buys
a shorter test and costs accuracy. A stability stop still reports `low_confidence` when
SE exceeds 0.65, and the report names the exit route, so a short run cannot read as a
certain one.

## Exposure control

`EXPOSURE_TOP_K` (default 3, `CAT_EXPOSURE_TOP_K` to override) applies **randomesque**
exposure control: the ranked window returned by `rank_candidates` starts at a uniform
random rank in `[0, k)`, so the administered item is rank `o ~ U[0,k)`.

It is applied to the *window* rather than the final pick on purpose: on the LLM branches
the LLM is the chooser, and randomising after its pick would discard the decision those
branches exist to measure. This way it composes identically with a coded picker and an
LLM picker.

Set `top_k=1` to restore deterministic argmax — `simulate_cat.py` does, so recovery is
measured without randomesque noise. Before this existed, selection was fully
deterministic at `temperature=0`: every candidate at a given θ̂ received an identical
form, and one item was administered in 100% of 200 sessions.

## LLM procedural selection (OpenAI)

MCQ **grading stays deterministic** (exact index match). The **LLM selects the next item** by executing a fixed procedure on an engine-scored shortlist:

1. Engine ranks unserved items by **KL** (q&lt;3) or **3PL Fisher** (q≥3)
2. Top 5 candidates sent to OpenAI with θ̂, SE, served history, IRT params
3. LLM must follow tie-break rules (info → |b−θ| → sub-competency coverage → discrimination)
4. LLM returns `selected_id` **only from the shortlist** — invalid ids fall back to engine
5. LLM also returns `adaptation_note` explaining why the item refines the estimate
6. LLM may rephrase the selected stem based on competency level and certainty, while preserving the original answer/options

The LLM never controls the criterion or the maths. `q_count` decides KL vs Fisher, the
engine ranks the shortlist, and code grades and updates θ̂. The model chooses **which of
5 pre-scored candidates** to administer, and how to word it.

### Adaptive rephrasing and its guardrail

Rephrasing is in tension with IRT: `b` is calibrated for specific wording, so a rewritten
stem is not strictly the item the parameters describe. **It is OFF by default** — tick
**Adaptively rephrase question stems** on the setup screen to study the feature. Every
rewrite is treated as untrusted output and validated by `rephrase_guard.py` before display:

| Rejected when | Why it matters |
|---|---|
| It flips the question's polarity (`TRUE` → `NOT TRUE` / `FALSE` / `…EXCEPT which one`) | Grading still uses the original `answer_index`, so the examinee is **marked wrong for answering correctly** |
| It restates the correct option's unique wording (measured against how much it echoes the distractors) | Turns a 4-way discrimination into a giveaway |
| It drops an identifier/literal/number from the stem (`sorted(nums)` → "the sorting function") | The item now tests something else |
| It is <0.5× or >2× the original length | Not a rephrase |

A rejected rewrite falls back to the original calibrated stem and is surfaced in the UI and
`assessment.log` (`REPHRASE_REJECTED`) rather than silently swallowed.

> **The polarity check exists because the guard was blind to negation by construction.**
> Negation words are stopwords, so `_words()` strips them and `code_tokens()` never sees
> them — the leak and drift checks compare *content*, and "Which is TRUE" and "Which is
> NOT TRUE" have identical content. `NOT TRUE`, `FALSE`, `incorrect` and `…EXCEPT which
> one` all passed the guard clean while inverting the question. That was a scoring bug,
> not calibration drift.

**What the guard still cannot do is prove difficulty is preserved.** A stem reworded
easier or harder, with polarity and identifiers intact, is administered with its original
`b` and θ̂ is computed from parameters that no longer describe it. There is no post-hoc
recalibration. This is why the default is off; for a psychometrically clean run, leave it off.

### Tests

```bash
python test_rephrase_guard.py   # guard accepts faithful rewrites, rejects leaks/drift
python test_approach.py         # drives the real CAT loop with a stubbed LLM, no API key
```

`test_approach.py` asserts the properties that must hold regardless of what the model does:
the LLM cannot administer an item outside the engine's shortlist, a malformed or hostile
response degrades to the coded CAT path, stopping rules are enforced by code, and θ̂ still
recovers true ability.

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

The setup screen probes the connection on load and shows live usage and cost.

For Streamlit deployment, use `masaar-mcq-cat-test/streamlit_app.py` as the main file path. See `DEPLOYMENT.md`.

If OpenAI fails, the app falls back to the same procedure executed deterministically in
Python — and as of the tie-break fix below, that claim is actually true.

> The fallback used to be a *different estimator*. `deterministic_select` keyed its sort
> on raw `info` first, and tuple comparison is lexicographic, so a 0.5% information
> difference settled the order outright and the documented "within 1% → prefer smaller
> |b−θ|" rule was unreachable: across 505 θ-points on this bank the top two scores were
> never exactly equal, so the tie-breaks ran **0 times**. The LLM was told to apply the
> 1% band (and did). Measured across all 5 competencies, the two procedures disagreed at
> 39 θ-points. `NEAR_TOP_REL` now collapses the 1% band into a single sort key, so code
> and prompt agree.
>
> Content balancing is still weak: `|b−θ|` is continuous, so it almost always decides
> before the sub-competency tie-break is reached. Sub-competency coverage measures
> 7.4/8 per session, but that is the bank being well spread, not the algorithm
> balancing. Real content balancing needs a constraint, not a tie-break.

## The LLM is required

There is no engine-only mode and no "use the LLM" toggle. Each branch exists to measure
what happens when the LLM owns part of the CAT; a run that quietly used coded logic
instead produces a number that looks like a result and answers a different question. The
connection is probed automatically on load and the app stops with an actionable error if
it fails.

The bank is likewise fixed to `enriched_bank_cat.json` — no uploader. The three branches
are only comparable if they run identical items, and an arbitrary uploaded bank may carry
no numeric IRT parameters at all, in which case the engine silently falls back to label
maps and the CAT degrades without saying so.

## LLM calls and cost

Measured, not estimated: `llm_client` reads `usage` off every API response, and
`measure_llm_cost.py` drives the real controller against the real API.

```bash
python measure_llm_cost.py --competencies 2   # real calls, real tokens
python measure_llm_cost.py --dry-run          # structure only, no spend
```

Measured on `gpt-4o-mini` ($0.15/$0.60 per 1M tokens), 2 sessions of 12 questions per
branch. A **competency** is up to 12 questions; a full assessment is 5 competencies.

> **These per-competency figures are ~1.30× high, and cannot currently be re-metered.**
> They were metered when every session ran to the 12-question cap. Under the convergence
> rule now in force, a competency averages **9.2 questions** (median 9, p95 12; measured
> over 500 simulated sessions across all five competencies), so calls, tokens and dollars
> per competency all scale by ~9.2/12. The token counts are real API measurements and are
> deliberately not rescaled by hand — tokens per call are not constant (the shortlist
> shrinks as items are served), so multiplying them by 0.77 would produce a fabricated
> number wearing a measured number's clothes. Re-run `measure_llm_cost.py` with a valid
> `OPENAI_API_KEY` to replace the table; the key currently in `.env` returns 401, which is
> also why `validate_llm_path.py` has never run. Treat the table as an **upper bound**.

| Branch | Calls/question | Calls/competency | Tokens/competency | $/competency | $/assessment | $/100 candidates |
|---|---:|---:|---|---:|---:|---:|
| `approach-1-code-math-llm-pick` | 1 (selection) | 12 | 34,754 in / 3,971 out | $0.0038 | $0.0190 | $1.90 |
| `approach-2-llm-math-code-pick` | 1 (ability update) | 12 | 14,047 in / 7,300 out | $0.0032 | $0.0162 | $1.62 |
| `approach-3-llm-full-cat` | 2 (update, then selection) | 24 | 45,715 in / 8,760 out | $0.0061 | $0.0303 | $3.03 |

Approach 3 costs ~1.9× approach 1 because it cannot be adaptive in one call: selecting the
next item and updating ability in a single response means the item is chosen against a
stale θ̂. Approach 2 is cheapest despite doing the harder task — its prompt carries one
item's parameters, while approach 1 ships a 5-item shortlist every turn.

Pricing lives in `MODEL_PRICING_USD_PER_1M` in `llm_client.py`; an unpriced model reports
no cost rather than a wrong one. The setup screen has a live **LLM usage & cost** panel.

## Langfuse tracing

When Langfuse keys are present in `.env`, the app records comparable trace events for:

- session start
- next-item selection
- LLM selection responses
- answer updates
- competency finalization

LLM calls are recorded as **generations carrying model, `usage_details` and
`cost_details`**, so cost per approach is visible in the Langfuse dashboard rather than
showing 0.

### Trace tags

Every trace is named `masaar-cat-<approach_id>` and tagged, so the branches can be sliced
against each other:

| Tag | Value on this branch |
|---|---|
| approach id | `approach-1-code-math-llm-pick` |
| math actor | `math:code` |
| selection actor | `selection:llm` |
| bank | `bank:enriched_bank_cat` |
| model | `model:gpt-4o-mini` |

Tags are namespaced `key:value` so `math:llm` groups approaches 2 and 3 regardless of how
they select. Add your own with `CAT_TRACE_TAGS="run:pilot-3,cohort:2026-summer"`. Metadata
carries `approach_id`, `math_actor`, `selection_actor` and `bank_id`.

> Tags previously never reached Langfuse. `trace_event` called
> `client.update_current_trace()`, which does not exist on the langfuse v4 client: it
> raised `AttributeError` into a bare `except: pass`, so every trace landed with `tags=[]`
> and was named after its observation. v4 sets trace-level attributes via
> `langfuse.propagate_attributes()`.

```bash
python test_usage_tracing.py   # metering + cost attribution; no API key or Langfuse needed
```

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
| `enriched_bank_cat.json` | **The bank the app runs.** 120 MCQs, 5 competencies × 24, calibrated `a`/`b`/`c` per item, cueing-controlled |
| `enriched_bank.json` | Earlier 122-item bank, kept for reference only — labels only (no numeric IRT) and heavily cued. Not loadable from the UI; see below |
| `sample_bank.json` | Small 21-item smoke bank, reference only |

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
- **`a` should be independent of `b`.** Discrimination is spread across every difficulty
  band (a very_easy item can still separate sharply). Pinning high `a` to high `b` starves
  the low-θ end of information and leaves weak candidates measured with blunt items —
  `validate_bank.py` fails on `corr(difficulty, a) > 0.75` for this reason.
  **This bank measures `corr(difficulty, a) = +0.50`**, which passes the gate but is not
  independence: mean `a` climbs monotonically 0.90 → 1.46 from `very_easy` to `very_hard`.
  So the low-θ information collapse below is *partly a bank property*, not only the 3PL
  guessing floor it is attributed to.
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

`simulate_cat.py` drives **`selection_pipeline.select_next_item(use_llm=False)`** — the
pipeline the app administers — not `engine.select_item`.

> It used to call `engine.select_item`: pure greedy argmax, with no shortlist, no 1%
> band, no tie-breaks and no exposure control. That is a path **no branch ships**, so
> every number below was evidence for a program nobody runs. `use_llm=False` keeps the
> run offline and deterministic while measuring the coded procedure the LLM is held
> against — which is the baseline the branch comparison needs. Numbers moved when this
> was fixed; the older figures in git history are not comparable.

`validate_bank.py` treats "no cueing" as a hypothesis to test rather than a judgement call:
chi-square on key position, exact binomial on longest-is-key **and** shortest-is-key, and a
mean length-rank z-test (a bank can pass "is longest" while the key is reliably *second*
longest — equally learnable). It also walks the ability range and fails if the best available
item at any θ is too blunt to measure there.

### Measured behaviour (200 simulated candidates per point)

Driven through `select_next_item(use_llm=False)` with `top_k=1`, under the three-rule
convergence check:

| Operating condition | RMSE(θ̂) | Converged (confidence or stable level) |
|---|---|---|
| No self-rating (pessimistic bound) | 0.764 | 86% |
| Honest self-rating (expected case) | 0.649 | 81% |

The convergence rate is **not** comparable to the pre-convergence-rule figures (16% / 35%),
which counted only `SE ≤ 0.65` and measured the unshipped `engine.select_item` path. Most
of the 81–86% here is the stable-level rule, which stops at SE ≈ 0.78 — precision did not
improve, the exit route changed. `certainty ≥ 90%` alone still fires in roughly 15% of
sessions, matching the old 16%.

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
