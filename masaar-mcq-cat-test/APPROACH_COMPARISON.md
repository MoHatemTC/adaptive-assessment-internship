# Masaar CAT Approach Comparison

This document compares the three experiment branches for the Masaar MCQ CAT harness.
The shared CAT target is:

- MCQ grading is deterministic by exact `answer_index`.
- Ability is represented as `theta_hat` and uncertainty as `SE`.
- Item parameters use the 3PL model: discrimination `a`, difficulty `b`, and guessing `c`.
- Adaptive selection should follow the CAT rule used in this project: KL information for the first 3 questions, then Fisher information (`KL -> FI`) after the estimate has enough response evidence.
- All approaches use the same fixed bank, `enriched_bank_cat.json`, so comparisons are about controller behavior rather than item differences.

`main` is the shared baseline and documentation branch. The actual approach code lives on the dedicated branches:

- `approach-1-code-math-llm-pick`
- `approach-2-llm-math-code-pick`
- `approach-3-llm-full-cat`

## Executive Summary

Approach 1 is the strongest CAT implementation because code owns the psychometric math and KL/FI scoring while the LLM only chooses from a code-computed candidate set. It remains probabilistic at the selection explanation/rephrasing layer, but the mathematical instrument stays deterministic and auditable.

Approach 2 is the safest way to study whether an LLM can do IRT math because code still owns next-item selection. The weakness is that code must compute a posterior/SE guard and fallback path, so the branch must report accepted LLM math separately from coded fallback math.

Approach 3 is the purest LLM-control experiment after the latest fix: the LLM owns math, stopping, and item choice in one controller call. That makes it the best experiment for “can the LLM run the CAT?”, but the weakest strict `KL -> FI` CAT implementation because code no longer deterministically enforces KL/FI selection.

## Scorecard

Scores are for the current branch intent and CAT quality after the latest fixes. The “CAT `KL -> FI` score” specifically measures how faithfully the branch follows this project’s adaptive testing rule.

| Approach | Branch | Overall score | CAT `KL -> FI` score | Main reason |
|---|---|---:|---:|---|
| Approach 1 | `approach-1-code-math-llm-pick` | 90/100 | 93/100 | Code computes IRT math and KL/FI ranking; LLM selection is constrained and audited. |
| Approach 2 | `approach-2-llm-math-code-pick` | 84/100 | 88/100 | Code selection preserves CAT behavior; LLM math is measured but guarded by code. |
| Approach 3 | `approach-3-llm-full-cat` | 72/100 | 62/100 | LLM owns the whole controller, which is experimentally pure but no longer code-enforced `KL -> FI`. |

## Deterministic vs Probabilistic Meaning

In this project:

- Deterministic means code applies a fixed algorithm and the same inputs produce the same decision, except for explicit exposure-control randomness.
- Probabilistic means the LLM produces a JSON decision. The call uses temperature `0.0`, but it is still model-generated behavior, not a formal guarantee.
- Audited means the app records a comparator, validity flag, fallback/invalid status, or Langfuse trace field so the behavior can be checked later.

## Approach 1: Code Math, LLM Picks

Branch: `approach-1-code-math-llm-pick`

### Responsibility Split

| Task | Actor | Deterministic or probabilistic | Notes |
|---|---|---|---|
| MCQ grading | Code | Deterministic | Exact selected option index equals `answer_index`. |
| Prior construction | Code | Deterministic | Self-rating and confidence become a calibrated prior distribution. |
| Ability update | Code | Deterministic | Uses coded EAP/grid posterior update after each answer. |
| SE/certainty update | Code | Deterministic | Derived from the posterior and confidence rules. |
| CAT criterion | Code | Deterministic | KL early, then Fisher/posterior-expected Fisher. |
| Candidate ranking | Code | Deterministic with optional exposure randomness | Code computes information scores and shortlist metadata. |
| Next-item choice | LLM | Probabilistic, constrained | LLM must pick an ID from the code-ranked shortlist. |
| Procedure audit | Code | Deterministic | Code computes the expected procedural choice and records whether the LLM followed it. |
| Question rephrasing | LLM then code guard | Probabilistic proposal, deterministic validation | LLM may rephrase; code rejects leaks, dropped identifiers, and polarity/sense flips. |
| Tracing/cost | Code | Deterministic metering | Token usage and trace metadata are emitted from code. |

### Current Fixes

The branch now records whether the LLM followed the exact documented selection procedure. It stores and traces:

- `expected_selected_id`
- `llm_selected_id`
- `procedure_followed`
- `procedure_deviation_reason`
- `fallback_used`
- rephrase rejection counts

The important design choice is that a valid LLM-selected shortlist ID is still administered, even if it deviates from the expected procedure. This preserves the experiment, while making deviations visible in UI, logs, and Langfuse.

### CAT `KL -> FI` Assessment

Approach 1 is closest to the intended CAT design. Code computes the information criterion and candidate scores, so the adaptive engine is mathematically stable. The LLM affects the final item choice only inside a constrained shortlist and is now audited against the procedure.

Score: 93/100 for CAT `KL -> FI`.

Remaining risk:

- If the LLM chooses a valid but procedurally suboptimal item, the test still administers it. This is correct for studying the LLM picker, but it can reduce CAT optimality unless deviation rates stay low.

## Approach 2: LLM Math, Code Picks

Branch: `approach-2-llm-math-code-pick`

### Responsibility Split

| Task | Actor | Deterministic or probabilistic | Notes |
|---|---|---|---|
| MCQ grading | Code | Deterministic | Exact answer-index grading. |
| Prior construction | Code | Deterministic | Same prior rules as other branches. |
| Ability update proposal | LLM | Probabilistic | LLM returns `theta_hat` and claimed `se`. |
| Posterior update/comparator | Code | Deterministic | Code computes coded EAP as a comparator and fallback. |
| SE used by assessment | Code | Deterministic | Code derives posterior spread about the LLM theta; LLM SE is recorded but not trusted. |
| Direction invariant | Code | Deterministic validation | Correct cannot lower theta; incorrect cannot raise theta. |
| Magnitude gate | Code | Deterministic validation | Large drift from coded EAP is rejected and counted. |
| CAT criterion | Code | Deterministic | KL early, then Fisher. |
| Next-item choice | Code | Deterministic with optional exposure randomness | Code owns item selection. |
| Tracing/cost | Code | Deterministic metering | LLM math call is traced with raw and coded comparison metadata. |

### Current Fixes

The branch now separates:

- raw LLM `theta_hat`
- raw LLM `se`
- code-used posterior SE
- coded EAP theta/SE comparator
- accepted LLM math steps
- fallback math steps
- direction rejections
- magnitude rejections
- rejection reason

The sidebar audit panel now makes it clear whether the session is mostly measuring the LLM or mostly measuring coded fallback.

### CAT `KL -> FI` Assessment

Approach 2 preserves the CAT selection rule because code still picks the next item using the same KL/FI machinery. The weakness is that the `theta_hat` driving that selection can come from the LLM. If the LLM drifts, code may still pick deterministically, but it is picking deterministically around an LLM-owned ability estimate.

Score: 88/100 for CAT `KL -> FI`.

Remaining risk:

- If fallback rates are high, the branch no longer measures LLM math.
- If LLM theta is accepted but biased, code selection remains formally correct but targets the wrong point on the ability scale.

## Approach 3: Full LLM CAT

Branch: `approach-3-llm-full-cat`

### Responsibility Split

| Task | Actor | Deterministic or probabilistic | Notes |
|---|---|---|---|
| MCQ grading | Code | Deterministic | Code still grades the selected option. |
| Ability update | LLM | Probabilistic | LLM returns `theta_hat`, `se`, and certainty. |
| Stop decision | LLM | Probabilistic | The latest pure-controller fix honors the LLM stop decision, with a hard max-question cap. |
| Next-item choice | LLM | Probabilistic | LLM chooses from available unserved items, not a code-ranked shortlist. |
| Rephrasing | LLM then code guard | Probabilistic proposal, deterministic validation | Same hard guard idea as Approach 1. |
| Numeric bounds | Code | Deterministic validation | Code clamps/validates numeric ranges. |
| Duplicate/invented ID prevention | Code | Deterministic validation | Invented or already-served IDs invalidate the LLM step. |
| Coded EAP comparator | Code | Deterministic audit only | Used for trace/audit comparison, not as the driving state. |
| Fallback behavior | Code | Deterministic invalidation | Bad LLM steps are marked invalid; no deterministic CAT selection is substituted. |

### Current Fixes

Approach 3 has been changed from a guarded hybrid to a purer LLM CAT experiment:

- one full-controller LLM call per CAT step
- available item pool is passed to the LLM instead of a code-ranked shortlist
- LLM returns math, uncertainty, stop decision, selected item, reason, and rephrased stem
- invalid item IDs, duplicate IDs, bad numeric outputs, and answer-leaking rephrases are rejected by code
- deterministic CAT fallback is not substituted for invalid LLM decisions

### CAT `KL -> FI` Assessment

Approach 3 is now much purer as an LLM-control experiment, but this makes it weaker as a strict CAT `KL -> FI` implementation. The model can be instructed to use IRT/CAT reasoning, but code no longer enforces KL for the first 3 questions and Fisher after that. Therefore, the score is lower for psychometric CAT fidelity even though the experiment is cleaner.

Score: 62/100 for CAT `KL -> FI`.

Remaining risk:

- LLM item choice is not guaranteed to maximize KL/FI.
- LLM stop decisions may stop earlier or later than the coded psychometric rule.
- Invalid LLM steps abort/invalidate the session instead of being repaired by code, which is experimentally honest but less robust for deployment.

## Cost and Token Comparison

The table below separates measured values from current expected behavior. Existing measured values came from earlier runs on `gpt-4o-mini` and are documented as upper bounds because they were metered when sessions ran closer to the 12-question cap. The current convergence behavior averages about 9.2 questions per competency.

Important: Approach 3 changed shape after the pure-controller fix. The older measured Approach 3 row used two calls per question: math update, then selection. The current pure-controller version uses one call per step but sends a larger available-item pool. It must be re-metered before claiming final cost.

### Previously Metered Upper Bounds

| Approach | Calls/question at measurement time | Tokens/competency | Cost/competency | Cost/full 5-competency assessment | Cost/100 candidates |
|---|---:|---|---:|---:|---:|
| Approach 1 | 1 selection call | 34,754 input / 3,971 output | $0.0038 | $0.0190 | $1.90 |
| Approach 2 | 1 math call | 14,047 input / 7,300 output | $0.0032 | $0.0162 | $1.62 |
| Approach 3, old hybrid | 2 calls: math + selection | 45,715 input / 8,760 output | $0.0061 | $0.0303 | $3.03 |

### Current Expected Call Shape After Fixes

| Approach | Current calls/question | Token expectation | Cost expectation |
|---|---:|---|---|
| Approach 1 | 1 | Moderate input: shortlist stems and metadata. Lower output. | Similar to measured upper bound unless prompt changes significantly. |
| Approach 2 | 1 | Lowest input: one answered item and response metadata. Higher output because math steps are returned. | Similar to measured upper bound. Usually cheapest. |
| Approach 3 | 1 | Highest input per call: available item pool plus full controller instructions. Output includes math, stop, and selection. | Unknown after pure-controller refactor; must be re-metered. Could be lower or higher than old hybrid depending on pool prompt size. |

## Determinism and Probabilism by System Layer

| System layer | Approach 1 | Approach 2 | Approach 3 |
|---|---|---|---|
| Grading | Code deterministic | Code deterministic | Code deterministic |
| Ability math | Code deterministic | LLM probabilistic, code guarded | LLM probabilistic |
| SE/certainty used | Code deterministic | Code deterministic around LLM theta | LLM probabilistic, code bounded |
| Stop rule | Code deterministic | Code deterministic | LLM probabilistic with hard cap |
| KL/FI ranking | Code deterministic | Code deterministic | LLM instructed, not code-enforced |
| Final item choice | LLM probabilistic inside shortlist | Code deterministic | LLM probabilistic over available pool |
| Rephrasing | LLM probabilistic with code guard | Not part of code-pick path | LLM probabilistic with code guard |
| Invalid LLM output | Code fallback/audit | Code fallback/audit | Invalid step, no deterministic CAT substitution |

## Recommendation

Use Approach 1 for the best balance of CAT correctness, LLM involvement, and auditability. It follows the `KL -> FI` CAT design most faithfully while still testing LLM item-picking behavior.

Use Approach 2 to study LLM ability-estimation behavior. It is not the cleanest psychometric instrument because accepted LLM theta can drift, but code selection keeps the CAT item choice stable and comparable.

Use Approach 3 as an experimental boundary test, not as the main deployed measurement approach. It best answers whether an LLM can run the whole CAT loop, but it should be judged separately from strict `KL -> FI` CAT quality unless the prompt is later changed to require and prove KL/FI calculations per candidate.

## Next Measurements Needed

Before final publication or deployment comparison:

1. Re-run `measure_llm_cost.py` on all three branches with a valid `OPENAI_API_KEY`.
2. Re-run `validate_llm_path.py` on all three branches with enough candidates for stable estimates.
3. For Approach 1, report LLM procedure-deviation rate.
4. For Approach 2, report accepted LLM math rate and fallback rate.
5. For Approach 3, report invalid LLM step rate, stop-reason distribution, and actual token/cost numbers for the pure-controller prompt.
