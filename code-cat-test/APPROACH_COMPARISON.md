# Code-question approach comparison — measured

**Recommendation: Approach C (LLM-led scoring), with the code-enforced anchoring left in
place. Mid rubric.**

540 runs: 3 approaches × 3 rubrics × 60 seeded submissions, each executed in an E2B
sandbox and scored against declared ground truth. Reproduce with `python run_matrix.py`.

Read the caveats at the end before acting on this — two of them are material.

---

## The grid

| | | loose | mid | tight |
|---|---|---:|---:|---:|
| **Separation rate** | A objective-only | 60% | 60% | 60% |
| (weak scored below strong) | B test-anchored | 73% | 63% | 63% |
| | C LLM-led | 73% | 73% | 63% |
| **Mean margin** | A | +0.085 | +0.085 | +0.085 |
| (how decisive) | B | +0.106 | +0.097 | +0.123 |
| | C | **+0.150** | **+0.160** | **+0.196** |
| **Misconception recall** | A | 12% | 12% | 12% |
| | B | 100% | 100% | 93% |
| | C | 100% | 100% | 98% |
| **Anchoring violations** | all nine cells | **0** | **0** | **0** |
| **Tokens / submission** | A | 0 | 0 | 0 |
| | B | 6,500 | 7,455 | 8,473 |
| | C | 7,010 | 6,894 | 8,283 |

The control arm holds: approach A never calls the model, and its three cells are
identical to the digit. Rubric effects elsewhere are therefore real rather than
run-to-run noise.

## Paired comparison — the decisive test

Same submissions, both approaches, bootstrapped over 4,000 resamples of the per-submission
margin difference:

| rubric | A→B | A→C | B→C |
|---|---|---|---|
| loose | +0.022 `[-0.010, +0.054]` | **+0.065** `[+0.016, +0.118]` | +0.043 `[-0.000, +0.089]` |
| mid | +0.012 `[-0.012, +0.037]` | **+0.075** `[+0.022, +0.136]` | **+0.063** `[+0.026, +0.107]` |
| tight | **+0.039** `[+0.014, +0.066]` | **+0.111** `[+0.044, +0.184]` | **+0.072** `[+0.019, +0.127]` |

**C beats B significantly under mid and tight, and beats A under every rubric.** B beats A
only under tight. Bold is a confidence interval excluding zero.

## What actually separates them

**Objective evidence alone cannot name a defect.** This is the largest effect in the study
and it is not close:

| seeded defect | A recall | B | C |
|---|---:|---:|---:|
| missing_empty_guard | 50% | 100% | 100% |
| off_by_one | 0% | 100% | 100% |
| wrong_algorithm | 0% | 100% | 100% |
| hardcoded | 0% | 100% | 100% |
| syntax_error | 0% | 100% | 100% |

Static analysis finds a missing guard about half the time — it can see that no length
check exists — and finds nothing else. It cannot tell an off-by-one from a correct bound,
because both are a loop over a range. A diagnostic assessment that cannot say *what the
learner got wrong* is a pass/fail test with extra steps, and that is what approach A is.

**The anchoring guards work, and they are why C is safe to use.** Zero violations across
540 runs, including all 180 approach-C submissions where the model scores every criterion.
C is not "trust the model": functional correctness still carries a 50% test weight, and
both `llm_evaluator.validate` and `scoring.combine` refuse a correctness claim above what
execution demonstrated. The measurement says that guard held under the most permissive
weighting tested.

**Rubric specificity buys decisiveness, not accuracy.** Tight raises the margin for both
LLM approaches (B +0.097→+0.123, C +0.160→+0.196) and makes A→B significant — but the
separation *rate* dips for both. Tight makes a correct diagnosis sharper and flips a
couple of borderline cases; it does not make the approach more often right. It also costs
15-25% more tokens. Mid is the better default.

**`wrong_algorithm` is hard for everyone** — 20% / 20% / 30% separation. A wrong strategy
fails tests broadly rather than in one identifiable place, so the failure does not localise
onto a competency. This is a limit of attributing a whole-submission failure to a
sub-skill, not of any approach, and no rubric fixed it.

## Why not A, despite being free

A costs nothing and runs in 1.5s. It is the right choice only if the product needs a
score and not a diagnosis. It reaches 60% separation and 12% misconception recall — it can
often tell that something is weak, and almost never what. If the assessment feeds a
learning path, that gap is the product.

## Why not B, despite being the doc's pattern

B is sound and never violated anchoring. It is simply dominated: C matches or beats its
separation rate, diagnoses significantly more decisively under two of three rubrics, and
costs the same. B's extra caution buys nothing measurable here, because the guards that
make B safe are still present in C.

---

## Caveats — read before acting

**1. This ran on the pre-relabel bank.** The competency taxonomy has since been rebuilt
onto the real T1–T5 sub-competencies, and the mapping collapsed seven invented
competencies into `T1.1` with only `boundary_conditions` going to `T1.4`. As a result 25
of 150 seeded files now have an empty `strong_competencies` set, which makes them
ungradable for separation. **The separation numbers above will change under the new
taxonomy and should be re-measured.** The recall numbers, which do not depend on the
weak/strong split, will not.

**2. A validator bug depressed B and C during this run.** `INVENTED_TEST_EVIDENCE` fired 13
times; 10 were false positives where the model cited real test ids comma-joined into one
reference field, and each discarded a sound diagnosis. Fixed after the run. The effect is
small — 10 of 360 LLM submissions — and it worked *against* the approaches that won, so
the ranking stands, but the B and C numbers here are slightly pessimistic.

**3. n=30 gradable submissions per cell.** Separation is only defined where a seeded
submission declares both weak and strong competencies. A 10-point shift in separation rate
is three submissions.

**4. Six of the 21 real code questions cannot be graded at all** — a Dockerfile, a prompt,
two pseudocode sketches, and two FastAPI endpoints the sandbox has no fastapi for. There is
no executable test to anchor to, so for those question types this entire comparison does
not apply and the choice is between unanchored LLM judgement and not assessing them. See
`bank/not_gradable.json`.

## Suggested next step

Re-run the grid on the rebuilt bank after giving the seeded ground truth a richer
sub-competency spread — the current collapse to two ids is what cost 25 files their
separation signal. Expect the recall result to hold and the separation numbers to move.
