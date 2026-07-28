# CAT Approach Comparison — measured

**Recommendation: ship Approach 1** (code owns the maths and the KL→FI ranking; the LLM
picks from a code-computed shortlist).

Every number here was produced by running the branches. `score_approach.py` drives 40
simulated candidates per branch through that branch's real controller against the real
API, paired against a coded-only arm on common random numbers, and writes
`scores/<approach>.json`. Reproduce with:

```bash
python score_approach.py --candidates 40 --out scores/     # on each branch
```

Model `kimi-k2.6` via the Sprints LiteLLM gateway; bank `enriched_bank_cat.json`;
exposure control pinned off (`top_k=1`) in both arms so disagreement is not the engine's
own dice. Human-test sessions quoted below were run through the Streamlit apps.

> The previous version of this file scored the branches 90/84/72 and their KL→FI fidelity
> 93/88/62. Those were assigned by reading the code — no script in this repository
> computed them, and the same was true of the paired tables quoted in `llm_math.py`. They
> are replaced, not adjusted.

---

## Scorecard (n = 40 candidates per branch)

| | Approach 1 | Approach 2 | Approach 3 |
|---|---:|---:|---:|
| | code math, LLM picks | LLM math, code picks | full LLM controller |
| RMSE (LLM arm) | 0.642 | 0.633 | 0.693 |
| RMSE (coded arm, same seeds) | 0.642 | 0.642 | 0.642 |
| **paired ΔMSE (llm − coded)** | +0.0000 | −0.0116 | +0.0678 |
| 95% CI (paired bootstrap) | [0, 0] | [−0.056, +0.029] | [−0.089, +0.231] |
| verdict | no difference | no difference | no difference |
| bias | +0.049 | −0.008 | +0.195 |
| **selection agreement vs coded KL→FI** | **100.0%** | n/a (code owns) | **54.2%** |
| mean information regret | 0.0000 | n/a | **0.1302** |
| worst information regret | 0.0000 | n/a | **0.8109** |
| **\|Δθ̂\| vs coded EAP** | n/a (code owns) | **0.101** | **0.243** |
| p95 / max \|Δθ̂\| | n/a | 0.21 / 0.31 | 0.69 / 1.54 |
| direction-invariant violations | 0 | 0 | 0 |
| sub-competencies covered / 8 | 6.95 | 6.95 | 7.50 |
| fallback rate | 0.0% | 2.8% | 0.0% |
| **sessions aborted** | 0 | 0 | **9 / 40** |
| LLM calls / competency | **8.8** | 12.5 | 12.0 |
| tokens / competency | **48,345** | 79,083 | **159,409** |
| wall clock, 40 candidates | **29 min** | 62 min | 118 min |
| composite (roll-up, weights in `score_approach.py`) | **100.0** | 96.7 | 76.1 |

### Ability recovery does not separate the three

Every CI crosses zero. Approach 3's point estimate is worse but n=40 cannot resolve it.
**"Which approach recovers ability best" is not currently the discriminating question** —
and that is itself a finding, because it means the decision rests on procedural fidelity,
robustness and cost rather than on accuracy.

---

## The decisive evidence against Approach 3

### It reports a precision the bank cannot deliver

A matched human-test triple — same competency (Generative AI), same all-correct
high-ability pattern, all three converged:

| | final θ̂ | final SE | questions |
|---|---:|---:|---:|
| Approach 1 | **2.366** | 0.691 | 11 |
| Approach 2 | **2.364** | 0.689 | 12 |
| Approach 3 | 2.040 | **0.510** | 12 |

Approaches 1 and 2 are independent estimators — one runs the coded EAP, the other an
LLM-executed Newton update — and they agree to **0.002**. Approach 3 sits 0.33 away and
claims to be *more* certain.

That SE is not merely optimistic. On this pool, taking the **12 most informative items**
and the **optimistic prior** — a best case no real test can beat — the floor is:

```
SE floor at θ=2.04, high-confidence prior   0.578
SE floor at θ=2.37, high-confidence prior   0.594
approach 3 reported                          0.510
```

**0.510 is below the information-theoretic floor. No sequence of answers to this bank can
produce it.** The reported SE trace across the session is 0.560, 0.550, 0.530, 0.520,
0.510, 0.510 — a fixed −0.010 decrement (sd 0.006), independent of which item was
administered or whether it was answered correctly. It is a counter, not an estimator.

Because this branch also owns the stop decision, the test stops on that number.

### Its SE and its certainty contradict each other

From a second session, the model's own `se` mapped through the coded certainty function:

| q | LLM se | LLM certainty | coded certainty for that se |
|---:|---:|---:|---:|
| 7 | 0.610 | 68.0% | 90.6% |
| 11 | 0.560 | 81.0% | 91.4% |
| 13 | 0.540 | 78.9% | 91.7% |

Every one of those SEs is already below `SE_TARGET`. On its own reported precision the
test should have ended at q=7; it ran to q=13.

### It picks the wrong item about half the time

54.2% agreement with the coded KL→FI choice, mean information regret 0.13, worst 0.81 —
it sometimes administers an item carrying a fifth of the information available.

### It aborts

9 of 40 sessions. Cause breakdown from the logs: **8 are "selected_id missing while
should_stop is false"** — the model says keep going, then does not name a question. 1–2
were gateway timeouts. This is fixable (require `selected_id` whenever `should_stop` is
false and retry), and it is listed as open work below — but it is a model failure, not
infrastructure.

---

## Approach 1 vs Approach 2

Both are sound. The separation is procedural fidelity and cost, not accuracy.

**Approach 1** reproduced the coded KL→FI choice on **353 of 353 steps**, zero
information regret, zero fallbacks, and is **1.6× cheaper in tokens and 2× faster** than
Approach 2.

Read honestly, 100% agreement admits two readings and the measurement cannot separate
them:
- the LLM follows the procedure perfectly; or
- the LLM is not changing any decision the code would not already have made.

If the reason for having an LLM in the selection loop is the *rephrasing* and the
audit-trail narration rather than the choice itself, Approach 1 is the right shape. If it
is meant to improve selection, this measurement says it currently does not.

**Approach 2** is the better experiment about LLM numeracy and the result is positive:
kimi's Newton update lands **0.101** from the coded EAP with **zero** direction
violations, and on your human-test session it matched the coded EAP to **0.022**. Its
costs are a 2.8% fallback rate and visible retries.

One user-visible wrinkle, which is **correct behaviour and still looks broken**: certainty
can fall after a correct answer (71.2% → 64.3% in one session). A correct answer to a
very-hard item at moderate θ is genuinely surprising — P(correct) was 0.313 — so the
posterior widens. Worth a UI note if Approach 2 ships.

---

## Cost and latency

Per competency, metered:

| | calls | tokens | approx wall clock |
|---|---:|---:|---:|
| Approach 1 | 8.8 | 48,345 | ~45 s |
| Approach 2 | 12.5 | 79,083 | ~90 s |
| Approach 3 | 12.0 | 159,409 | ~175 s |

Dollar cost is **not** computed: the gateway refuses `/model/info` to this key, so the
billed rate is unknown and is not guessed. Set `LLM_PRICE_INPUT_PER_1M` and
`LLM_PRICE_OUTPUT_PER_1M` to price a run.

**kimi is a reasoning model.** One CAT step costs ~20 s and ~3,000 output tokens, of
which the answer is ~40. On a 5-competency assessment Approach 1 is ~4 minutes of model
latency and Approach 3 is ~15. This dominates the candidate's experience and is the
single largest product consideration here.

---

## Bank limitations — these bound every approach

Found while debugging convergence; they are not controller problems and no approach fixes
them.

**The confidence stop can never fire for a typical candidate.** Greedily, with every pick
optimal, a low-confidence candidate needs 13 items to reach `SE ≤ 0.65` at θ=0 and the
entire 24-item pool at θ=−2. `MAX_QUESTIONS` is 12. Every session therefore ends on the
stability rule or the cap — which is what every human-test log shows: `converged=True` at
80–82% certainty with SE ≈ 0.69, never at target.

**The pool is too thin at the extremes.** Every competency has exactly 2 items worth
I ≥ 0.25 at θ=+2.5 and 4 at θ=+2.0. A strong candidate exhausts the useful items in about
five questions, then grinds through items worth 0.04–0.15. `validate_bank.py` now gates on
this and **fails the shipped bank** at θ ∈ {−2, 0, +1} in all five competencies.

The lever is **discrimination, not more difficulty**: information goes as a², and the
very_hard items sit at a = 0.67–1.60 with only two above 1.5.

**Not a bug:** a session going very_hard → medium at high θ is correct. Difficulty label
is not information — at θ=+2.31 a medium item with a=1.45 carries I=0.086 while a
very_hard item with a=0.67 carries 0.068. Replaying the coded engine reproduces the same
descent to the same item.

---

## Open items before this is final

1. **Convergence depends on the intake self-rating.** The stability floor gates on
   certainty, which is defined as progress from the starting uncertainty, so stopping
   requires SE ≤ 0.703 from a candidate who claimed confidence and SE ≤ 0.783 from one who
   did not. Same answers, different test length. The confidence rule is clean
   (SE ≤ 0.65 for everyone); the stability floor is not, and it is the rule that fires.
   Fixing it changes stopping on all three branches and invalidates the table above.
2. **Approach 3's `selected_id` contract** — would likely move its numbers materially.
3. **Rebuild the bank** against the completability gate. This is the highest-leverage
   change available and it bounds every approach equally.

Items 1 and 3 should land before any re-run; the scorecard is reproducible in ~30 minutes
per branch.
