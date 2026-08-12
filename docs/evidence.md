# Evidence

Why the defaults are what they are. Everything here is simulated: **no real candidate has
sat this assessment**, and the un-run layers are listed at the end.

Full study, external to this repository: the B/C evaluation results and architecture
proposal on the
`eval/bc-graph-augmented` branch. **The harness itself is no longer on this branch** — it
was removed when the repository was reduced to the engine, and is recoverable from git
history. The numbers below are what it found, and they are why the shipped defaults are
what they are; reproducing them means checking out the harness again.

## Design

Four data-generating processes — including a **null control** with no prerequisite structure
at all, which is what makes "the graph helped" falsifiable. Five configurations of the
engine. 1,600 paired candidates per cell, **200 per ability stratum**.

The pairing is exact: a response is a pure function of (candidate, item), so two
configurations answering the same question get the same answer and the only difference
between them is what they asked.

## Propagation is unsafe at its own gates

Measured with prerequisite edges **correct by construction** — the most favourable case
that can be built:

| gate | bar | measured |
|---|---|---|
| wrong inference | < 3% | **22.4%** (95% UCB 24.4%) |
| inference precision | ≥ 0.97 | **0.776** |
| false blocking | < 2% | **8.7%** (95% UCB 9.6%) |
| missed blocking | — | 94.9% |
| duplicate evidence | 0 | **0 / 36,657 ✓** |
| over-convergence | < 2% | **0 ✓** |

Inference fires in ~44% of sessions. It is not inert; it is active and unreliable.

### The blocking floor is structural

> **P(descendant truly mastered | parent truly NOT mastered) = 10.8%** when the graph's
> edges exactly match the world, and 14.6% when a quarter of them are wrong.

A block fired on *perfect* knowledge of the parent is wrong that often, because prerequisite
relations are probabilistic — people learn out of order. To pass a 2% gate an edge would
have to bind ≥98% of the time. No amount of evidence about the parent gets there.

Requiring two consistent failures cut blocks from 23.5 to 5.3 per session. Worth keeping,
not a route to the gate.

## An edge cannot be validated from observational data

Against a cohort where 8 of 30 edges were deliberately **absent from the world**:

| estimator | spurious refused | real retained |
|---|---:|---:|
| P(pass child \| fail parent) — as the architecture computes it | **0 / 8** | 22 / 22 |
| regression on ability and "failed the parent" | — | no effect either way |
| within-ability-stratum contrast | 8 / 8 | **0 / 22** |

Real edges span −0.148 to −0.000; spurious −0.099 to −0.008. Completely interleaved.

This is *not* because the world lacks structure — P(child | parent failed) = 0.084 against
0.801 when the parent is mastered. The structure exists in the truth and survives
θ-matching. What does not survive is the trip through one noisy graded response per node.

**Validating an edge needs an experiment, not a calibration matrix.**

## Measurement does not decide between the configurations

Every configuration lands within 0.8pp of every other on exact-level accuracy, and nine of
ten contrasts are non-inferior at a 2pp margin. What separates them:

| | Approach B | graph off | coverage gate on |
|---|---:|---:|---:|
| exact-level accuracy | 0.6117 | 0.6067 | 0.6148 |
| questions | **17.47** | 20.27 | 21.60 |
| duration P50 | 83.3 min | 47.7 min | 49.6 min |
| duration P90 | **95.3 — over the 90-minute cap** | **58.5** | 83.0 |
| modality blueprint | 0.913 | 0.769 | **0.999** |

Ranking on raw information buys expensive code and voice items and **breaches the cap**;
ranking on information-per-minute buys the clock. Without the coverage requirement the
assessment quietly stops being mixed.

## The instrument's real limit is dimensionality

Same likelihood, same item parameters, same prior, same code — only the construct changes:

| | competency is one skill | competency is several |
|---|---:|---:|
| SE calibration RMSE/SE (bar 0.95–1.10) | **1.02–1.09 ✓** | 1.33–1.45 ✗ |
| 95% interval non-coverage (nominal 5%) | **5.2–5.7% ✓** | 13.2–16.9% ✗ |
| exact-level accuracy | 0.797 | 0.698 |

**The posterior is calibrated when a competency really is one skill and 30–40% overconfident
when it is several**, and single-θ modelling costs **9.9pp** of decision accuracy.

That is what a competency graph ought to fix — and this one does not, because inferred
signals carry no score or weight by design, so the graph reaches selection and stopping but
never the estimate. The value in a competency DAG is **multidimensional measurement**, not
prerequisite propagation.

## Scale

8 bands → 5 is worth **+12.4pp** of decision accuracy at zero item cost; 5 → 4 a further
+1.7pp. Within-one-level accuracy is ~100% at four and five bands while exact is ~89%.

**So report a band range.** "Level 3–4" is defensible; "Level 3" is not.

## What is not evidence

Not run: the grader gold set (250 expert-labelled cases), historical replay, DeepEval /
G-Eval, shadow deployment, live pilot. **Fairness has not been measured for any
configuration** — for an instrument assigning levels used in hiring, that is the largest
untouched exposure. Cost and LLM-call counts are absent. Exposure figures were taken at
`CAT_EXPOSURE_TOP_K=1` and do not transfer to production.

Grader confidence is **generated by the simulator** and has never been compared to a real
grader's. Since propagation triggers on confidence ≥ 0.80, the inference *rate* above is
unvalidated — though its *precision* is not, and precision is the problem.
