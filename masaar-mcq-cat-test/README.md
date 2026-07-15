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

## Full LLM CAT controller (OpenAI)

MCQ **grading stays deterministic** (exact index match). The **LLM updates theta/SE and selects the next item** from validated candidates:

A CAT step is **ordered**: a response updates θ, and the *updated* θ decides which item
is most informative next. So the step is two LLM calls, not one:

| # | Actor | Does |
|---|---|---|
| 1 | **LLM** | Updates θ̂ from the graded response (3PL score function + Newton update) |
| 2 | code | Checks the direction invariant; derives SE and certainty from the exact posterior; applies the **stopping rule** |
| 3 | code | Ranks unserved items by KL/Fisher **at the LLM's new θ̂** → top-5 shortlist |
| 4 | **LLM** | Picks from that scored shortlist, applies tie-breaks, optionally rewords the stem |

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

**And code now owns the rule's *input*, not just its comparison.** That claim was
previously only half true: `rule_stop = se <= SE_TARGET` consumed the **LLM's** `se`, and
the prompt's Newton update `se = sqrt(1/(1/se_prev² + info))` is monotonically
non-increasing since `info ≥ 0` — it never widened once in 48,000 measured updates, while
the grid EAP SE rises ~10% of the time after a surprising response. A rule fed by a
quantity that cannot move against it guarantees nothing. Code now derives
`posterior_se_about(posterior, θ̂) = sqrt(E[(θ − θ̂)²])`, which widens when the evidence
says so and, being measured about the LLM's θ̂, equals `sqrt(Var + (mean − θ̂)²)` — so a
bad estimate reports as uncertain and stops later, rather than confidently wrong and
sooner. The model's `se` is still traced as `se_llm` beside `se_used`.

Certainty is computed in code for the same reason. The prompt asked for `100*(1 − se/2)`,
which disagrees with `combined_certainty_pct` by **22.5 points at the SE target** (67.5%
vs 90.0%), so certainty silently changed scale whenever the LLM fell back.

### What is validated, and what deliberately is not

| Check | Status | Why |
|---|---|---|
| Correct → θ̂ must not fall; incorrect → θ̂ must not rise | **Enforced** (falls back to coded EAP) | Exact invariant: the 3PL likelihood is monotone in θ. Verified against 480 (item, prior) pairs including grid-edge priors with `c>0`: 0 violations. |
| A response with no usable `theta_hat` | **Falls back, and is counted as a fallback** | Otherwise garbage books as a successful LLM step with deviation 0.0 and *improves* the branch's headline metric. |
| SE must not increase | **Not enforced, and no longer the LLM's to report** | *Looks* like an invariant but isn't — the grid EAP SE rises ~10% of the time after a surprising response. See above for why code derives SE instead. |
| `selected_id` ∈ shortlist | **Enforced** | Otherwise selection isn't information-driven. |
| Stem rewrite flips polarity | **Enforced** | `TRUE` → `NOT TRUE` was graded against the original key: the examinee is marked wrong for answering correctly. |
| Deviation from coded EAP > `DEVIATION_WARN` | **Warned, never rejected** | The Newton update approximates the grid EAP; demanding agreement would make the comparison vacuous. |

The LLM is never shown the coded EAP result — otherwise this branch would measure copying
rather than capability. `test_approach.py` asserts the coded θ̂ appears in no payload.

> **Corrected claims.** This table asserted the EAP SE rises in "23%" of 8000 updates (up
> to +0.28) and that `DEVIATION_WARN=0.35` trips "~2.9%" of the time. Measured: ~10% (up
> to +0.23), and 0.35 trips **0.00%** — |Δθ̂| runs median 0.010 / p95 0.090 / p99 0.169.
> The decisions were right; the numbers weren't. `DEVIATION_WARN` is now 0.20, since a
> threshold a correct implementation never reaches cannot flag a wrong one.

### Am I actually measuring the LLM?

Two calls per question means two chances to fall back, and a fallback is silent by
construction: θ̂ still looks like a plausible number. The sidebar audit now leads with
`LLM ran N/M math steps`, warns above 20% fallback, and **errors** when every step fell
back — that run measures the engine and must not be reported as approach 3. Previously the
panel early-returned when there was nothing to show, so a 100%-fallback run displayed
nothing at all while the trace tags still said `math:llm`.

### Adaptive rephrasing and its guardrail

**Off by default** (it was on). Every rewrite is validated by `rephrase_guard.py` before
display and rejected if it **flips the question's polarity**, restates the key's unique
wording, drops an identifier the item turns on, or changes length drastically. Rejections
fall back to the original calibrated stem and surface in the UI and log.

> The polarity check was missing, and it was the worst of the four. `TRUE` → `NOT TRUE` /
> `FALSE` / `…EXCEPT which one` all passed the guard clean while grading still used the
> original `answer_index` — the examinee was asked the opposite question and marked wrong
> for answering it right. The guard was blind to it by construction: negation words are
> stopwords, so the leak and drift checks compare content and see nothing.

Rephrasing remains an uncalibrated deviation from IRT: the guard still cannot prove a
rewrite preserved difficulty, and the item is scored with the `b` calibrated on the
original stem. Leave it off for a clean run.

### Known property of this approach

The LLM emits a scalar `theta_hat`, so the ability estimate is a point summary with no
shape. That is inherent to this branch's design.

What is **no longer** true is that the belief is collapsed to a Gaussian. The step used to
rebuild the posterior from `(theta, se)` on every path — including the coded fallback, so
this branch's "coded reference" was never approach 1's coded EAP — leaving `E[Fisher]`
selection integrating over a symmetric fiction while real 3PL posteriors are skewed near
the guessing floor. Measured, it changed the selected item in 11.0% of trials. Code now
carries the exact grid posterior, as approach 1 does.

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
what happens when the LLM owns part of the CAT; a run that quietly used coded logicinstead produces a number that looks like a result and answers a different question. The
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
| approach id | `approach-3-llm-full-cat` |
| math actor | `math:llm` || selection actor | `selection:llm` |
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
