# Proposed architecture

**Date:** 4 August 2026
**Evidence:** the corrected Phase 0a/0b analyses and the representative-sample re-run in §6.
`BC_EVALUATION_RESULTS.md` records the first run; **§6 supersedes its absolute figures**,
which were computed on the weakest third of the cohort.
**Branches:** `eval/bc-graph-augmented`, `eval/bc-voice-code-mcq-streamlit`.

---

## The proposal in one table

| Layer | Take from | Why |
|---|---|---|
| CAT posterior, fractional likelihood, EAP on a grid | shared core | identical in both approaches; not the decision |
| Explicit, equal-width band cuts | Approach C | B's clipped rounding inflated its own accuracy by 17pp |
| **Four or five bands, reported as a range** | new | +12.4pp for 8→5, and within-one accuracy is 100% |
| **P(band) ≥ 0.80 as an ADDED requirement** | shared core, needs a change | as an early exit it is accuracy-neutral and 5% shorter, but §6.5 shows a worse P90 tail |
| Coverage requirement over sub-competencies | Approach C | without it, **no** competency ends fully covered |
| Modality blueprint, enforced as a constraint | Approach C, fixed | preemption had collapsed breadth to 0.714 |
| Information-per-minute ranking | Approach C | 90-minute cap is otherwise unreachable |
| Difficulty corroboration before convergence | Approach C | prevents a narrow posterior from easy items |
| **Prerequisite inference** | neither | fires in 44% of sessions and is **wrong 23.3%** of the time, against a 3% gate |
| **Prerequisite blocking** | neither | 8.2% false blocking, and a **14.6% structural floor** no evidence can beat |
| **Graph utility penalties/bonuses** | neither | collapsed modality breadth, bought nothing |

Net: **Approach C's codebase, with the graph reduced to a coverage bookkeeper, on a coarser
scale reported as a range.** The stopping rule that was written and never called turns out
to be wired the wrong way round; §6 says what to change before enabling it.

The graph is not deleted. It keeps the node-level state, the coverage requirement, the
shared-node rollup and the report's direct/inferred distinction. What it stops doing is
*acting on prerequisite edges* — inferring mastery, blocking descendants, and reweighting
selection.

---

## 1. Why not simply "retain Approach B"

The first results document said "retain Approach B's measurement core," and the review was
right that this contradicts its own table — but the table it contradicted was itself
computed on the weakest third of the cohort (see §6). On a representative sample the two
are indistinguishable on measurement:

| DGP-2 | B | C-off |
|---|---:|---:|
| exact-level accuracy | 0.6087 | 0.6099 |
| non-inferiority, B as reference | — | **non-inferior**, UB +1.50pp |

So the choice is not a measurement choice. It is made on three things that are not close:

- **The clock.** B's P90 is 95.1 minutes against `C-off`'s 58.3. **B breaches the
  90-minute cap; no C arm does.** B ranks on raw information, which buys expensive code and
  voice items; C ranks on information per minute.
- **The blueprint.** B reaches 0.912 modality compliance without enforcing anything; every
  C arm with the coverage gate reaches 0.999.
- **The scale.** B's clipped rounding overstated its own accuracy by 17 points until this
  work replaced it, and the replacement came from C.

B does ask **15% fewer questions**, which is real and is the one axis on which it wins.
Against a 90-minute cap it loses on the axis that binds.

**One caveat the review raised and I can now close.** B won the worst-true-θ-decile metric
(0.458 vs 0.292), and the review asked whether that was B's unbounded tail bands showing up
again. It was not: `absolute_metrics` computes the decile on the **common** scale for every
arm, and deciles are taken on **true** θ, which is a property of the cohort and not of any
arm's reporting rule. B's tail advantage is real, and it comes from B ranking on raw
information — which selects the expensive code and voice items that carry the most
information about extreme candidates. That is worth preserving, and §5 says how.

---

## 2. What the graph earned, and what it did not

**Earned — the coverage requirement.** With the gate off, the share of competencies ending
with every required sub-competency directly measured is **0.000**. With it on, **1.000**,
for about half a question per competency. The information-greedy selector never covers the
content the report claims to cover, and nothing else in either approach notices.

The review is right that this is a *blueprint* result rather than a *graph* result: a
weighted-deviation or shadow-test content-balanced selector delivers the same guarantee
without a DAG. But the graph is where the requirement is already declared — which
sub-competencies a main has, which are critical — so the cheap path is to keep that
declaration and drop the propagation. Replacing it with a standalone blueprint is a
follow-up, not a prerequisite, and §7 lists it.

**Not earned — inference, and the reason is not the one first reported.** The first run
said inference fired once in 960 sessions and concluded it was inert. That was the sampling
bias: inference needs a *strong success*, and the weakest third of a cohort almost never
produces one.

On a representative sample it fires in **372 of 845 sessions (44%)** and is **wrong 23.3%
of the time** (95% UCB 26.1%) against a gate of 3%. Inference precision is 0.767 against a
0.97 bar. The mechanism is not dead; it is active and unreliable.

The review's H5 caveat still applies to the *trigger rate*: grader confidence is generated
by the harness and has never been compared to a real grader's. But it now cuts the other
way. If real graders are more confident than the simulator, inference fires **more** often,
not less — and each firing is wrong about a quarter of the time. The gold set (§7) settles
the rate; it cannot rescue the precision.

**Not earned — blocking.** On a representative sample, **8.2%** of blocked nodes belonged to
candidates who could do the work (95% UCB 9.4%), against a 2% gate. Missed blocking is
94.8%: the mechanism also almost never fires when it should. Unsafe and ineffective at once.

**The fix works and the gate is still unreachable — for a reason that is structural.**

Requiring two consistent failures was implemented (`graph_minimum_failures_to_block`,
default 2) on the reasoning that a block inherits the per-observation error rate, so
squaring it should land inside the gate. It did what it was designed to do: blocks fell
from 23.5 per session to 5.3, a 4.4× reduction in spurious blocking from noisy single
observations.

The false-blocking *rate* went up, to 6.8%. That is not a regression — it is the fix
working. With one failure, many blocks fired off misjudged parents, and a misjudged parent
is usually a weak candidate whose descendants they genuinely cannot do, so those blocks
counted as correct. Making the parent verdict reliable removed those and left the blocks
that are actually about the edge.

And the edge is where the floor is:

| DGP arm | P(descendant truly mastered \| parent truly NOT mastered) |
|---|---:|
| DGP-1 — graph edges exactly match the world | **10.8%** (n = 253,440) |
| DGP-2 — a quarter of the edges wrong | **14.6%** (n = 241,067) |

**A block fired on perfect knowledge of the parent is wrong 10.8% of the time.** No amount
of evidence about the parent can beat that, because the prerequisite relation is itself
probabilistic — candidates learn out of order. To pass a 2% gate an edge would have to bind
at least 98% of the time, which is a claim about the world, not about the engine.

So the 2% gate is not a bar blocking currently fails; it is a bar blocking cannot meet under
any realistic prerequisite. That moves "leave blocking off" from an implementation verdict
to a design one. The two-failure rule is still worth keeping — 4.4× fewer blocks for free —
but it is a mitigation, not a route to the gate.

---

## 3. Why the scale change comes first

Corrected Phase 0a, on ability drawn uniformly with no node heterogeneity — the
configuration in which band count is the only thing varying:

| bands | width | stop rule | exact accuracy | within-one | items |
|---:|---:|---|---:|---:|---:|
| 4 | 2.00 | P(band) ≥ 0.80 | **0.9228** | 1.000 | 7.68 |
| 4 | 2.00 | SE ≤ 0.55 | 0.9044 | 1.000 | 6.51 |
| 5 | 1.60 | P(band) ≥ 0.80 | 0.9083 | 1.000 | 8.12 |
| 5 | 1.60 | SE ≤ 0.55 | 0.8878 | 1.000 | 6.51 |
| 8 | 1.00 | P(band) ≥ 0.80 | 0.8156 | 0.999 | 11.01 |
| 8 | 1.00 | SE ≤ 0.55 | 0.7633 | 0.998 | 6.51 |

**8 → 5 bands is +12.4pp at zero item cost. 5 → 4 is a further +1.7pp.**

The first results document reported +47.5pp for the same change. That was wrong, and the
review caught it. The decomposition:

| configuration | 8→5 |
|---|---:|
| clean — uniform ability, no node heterogeneity | **+12.4pp** |
| + ability at eight fixed strata (cut-alignment confound) | +23.7pp |
| + node-level heterogeneity | +39.4pp |
| as originally published | +47.5pp |

Two harness bugs of mine compounded. The cohort places true ability at strata sitting 0.40
from every 5-band cut but 0.20 from every 8-band cut, two of them exactly on one — so the
8-band arm was maximally penalised by where the truth happened to sit. And the first
uniform re-draw kept a node map generated against the *old* ability, shifting every response
0.8 logits low; the confusion matrix showed 44–63% of candidates reported one band low
against 0.6–1.3% reported high, which is a bias, not imprecision. Both are fixed, the
confusion matrix is now dumped per cell, and the corrected +12.4pp sits close to the
validation document's independent +14.8pp estimate and the review's +15.1pp recomputation.

**The recommendation survives the correction and the reason changes.** It is no longer "band
count dwarfs everything." It is: at this precision the instrument supports four or five
distinguishable levels; within-one-level accuracy is 100% at both; so **report a band range,
not a point band.** "Level 3–4" is defensible at 100%; "Level 3" is defensible at 89%.

---

## 4. The finding that argues for a graph — and against this one

Isolating node-level heterogeneity at fixed cut alignment:

| configuration | 5-band exact accuracy |
|---|---:|
| responses from the 3PL alone (well-specified) | 0.7972 |
| responses varying by node mastery within a competency | **0.6983** |

**Summarising a multi-node competency with one θ costs 9.9pp.** That is larger than the
5→4 band change, comparable to two-thirds of the 8→5 change, and it is the single largest
modelling error in the instrument.

This is exactly what a competency graph ought to fix — and the implemented graph does not
fix it. It never models node-level ability. `InferredNodeSignal` carries no score and no
weight by design (correctly: a deduction is not a second response), so the graph reaches
selection and stopping but never the posterior. It changes *what is asked* and *when to
stop*, not *what is estimated*.

So the honest reading is: the architecture bet on the wrong graph mechanism. The value in a
competency DAG is multidimensional measurement — a posterior per node, rolled up to a main
— not prerequisite propagation. That is a larger change than this evaluation can justify on
its own, and §7 puts it in the research column rather than the roadmap.

---

## 5. The proposed configuration, concretely

```bash
# scale
CAT_BAND_COUNT=5                        # explicit equal-width cuts, report a RANGE
CAT_BAND_PROBABILITY_STOP_ENABLED=false # reachable now. On a representative sample it is
                                        # accuracy-neutral and ~5% shorter, but it is wired
                                        # as an EARLY EXIT checked before precision and it
                                        # fires for 73% of competencies. Make it a
                                        # REQUIREMENT alongside precision, measure the P90
                                        # tail, then enable (see section 6.5).
CAT_BAND_PROBABILITY_TARGET=0.80

# graph: bookkeeping only
COMPETENCY_GRAPH_ENABLED=true
GRAPH_CONVERGENCE_GATE_ENABLED=true     # the coverage requirement — the one thing it earned
GRAPH_UPWARD_INFERENCE_ENABLED=false
GRAPH_DESCENDANT_BLOCKING_ENABLED=false
GRAPH_FILTERING_ENABLED=false
GRAPH_UTILITY_ENABLED=false
GRAPH_EDGE_PREVIEW_ENABLED=true         # record what edges WOULD conclude; act on none

# selection: keep C's, with the blueprint no longer preemptable
ORCHESTRATOR_TIME_AWARE_SELECTION_ENABLED=true
ORCHESTRATOR_MODALITY_MINIMUMS='{"code": 1, "voice": 1}'
CAT_EXPOSURE_TOP_K=3                    # production value; 1 only for reproducibility runs
```

Every prerequisite edge stays `allow_upward_inference: false`,
`allow_downward_blocking: false`, `validation_status: unvalidated` — which is what they ship
as today. §6 of the architecture document describes inference and blocking as live; that
section should be marked "not shipped" rather than left to imply otherwise.

**Not yet fixed, and load-bearing:** the SE calibration ratio sits at 1.28–1.37 in every
arm. Reported confidence is wrong in the same direction for every approach, and the P(band)
stopping rule *reads the posterior*, so an overconfident posterior makes it stop early. §7
puts this first for that reason.

---

## 6. Re-run on a representative sample — what it overturns

Everything before this section, in this document and in `BC_EVALUATION_RESULTS.md`, was
computed on a **biased subsample**. `build_cohort` emits stratum 0's simulees, then stratum
1's, and so on; `--limit N` took the first N. At `--limit 1200` of a 4,000-simulee cohort
that is strata 0–2 of 8 — **the weakest third of the ability range**, reported as if it
were the population.

The sampler now strides across strata and shuffles deterministically, so any prefix is
representative. Re-run on that basis: 5 arms, **1,600 paired candidates per cell, exactly
200 per ability stratum**.

**Three findings do not survive, and one gets much worse.**

### 6.1 The absolute gates do not all fail

| gate | bar | DGP-0 (unidimensional) | DGP-2 (multi-node) |
|---|---|---|---|
| Marginal reliability | ≥ 0.85 | **0.922–0.929 PASS** | **0.942–0.950 PASS** |
| SE calibration RMSE/SE | 0.95–1.10 | **1.02–1.09 PASS** | 1.33–1.45 FAIL |
| Interval non-coverage | 3–7% | **5.2–5.7% PASS** | 13.2–16.9% FAIL |
| Exact-level accuracy | ≥ 0.80 | 0.705–0.718 FAIL | 0.607–0.615 FAIL |
| Worst band | ≥ 0.70 | 0.41–0.58 FAIL | 0.45–0.48 FAIL |
| P90 duration | ≤ 90 min | 59.5–98.1 — **B fails at 98.1** | 58.5–95.3 — **B fails at 95.3** |

Marginal reliability of 0.68 in the first run was a **restriction-of-range artefact** —
reliability is a ratio of true-score variance to observed variance, and a cohort spanning
only the bottom third of ability has almost no true-score variance to detect. On a
representative cohort the instrument is reliable enough to certify.

### 6.2 The calibration failure is the dimensionality assumption — identified, not guessed

This is the most useful result in the exercise. The review listed four candidate causes for
the SE calibration failure and proposed an experiment. The design already contained a
cleaner one:

| | DGP-0: no node structure | DGP-2: node-level mastery |
|---|---:|---:|
| SE calibration ratio | **1.02–1.09** | 1.33–1.45 |
| 95% interval non-coverage | **5.2–5.7%** | 13.2–16.9% |

**The posterior is correctly calibrated when a competency really is one skill, and 30–40%
overconfident when it is several.** Same likelihood, same item parameters, same prior, same
grid, same code — only the construct changes. That rules out the fractional likelihood,
inflated `a` parameters, grid truncation and prior width in one contrast, and it agrees
with §4's finding that single-θ modelling costs 9.9pp of decision accuracy.

So the fix is not a recalibration constant. It is to model the sub-competencies — which is
the graph's real opportunity and not the one the architecture took.

### 6.3 Inference is not inert. It fires often and it is badly wrong

The first run reported "upward inference fired in 1 session out of 960" and concluded the
mechanism was effectively dead. That was entirely the sampling bias: inference needs a
*strong success*, and the weakest third of candidates almost never produce one.

On a representative cohort, `C-full` under DGP-2:

| | |
|---|---:|
| inferences verified against node truth | **1,386** |
| **wrong inference rate (C-DAG-03, gate < 3%)** | **22.4%, 95% UCB 24.4%** |
| inference precision (C-DAG-01, gate ≥ 97%) | **0.776** |

**Nearly one inference in four is wrong**, against a gate of one in thirty-three. The
conclusion — do not enable inference — is unchanged, and the reason is now much stronger:
not "it never fires" but "it fires in 44% of sessions and is wrong 23% of the time".

### 6.4 Blocking, on the same basis

| | DGP-2, representative |
|---|---:|
| false blocking (gate < 2%) | **8.7%, 95% UCB 9.6%** (262 / 3,006) |
| missed blocking | 94.9% (10,325 / 10,885) |
| duplicate evidence | **0 / 36,657 PASS** |
| over-convergence | **0 PASS** |

Still failing by roughly 4×, and §2's structural floor stands: a block fired on perfect
knowledge of the parent is wrong 14.6% of the time under DGP-2. Missed blocking at 94.8%
says the mechanism also almost never fires when it should — it is both unsafe and ineffective.

### 6.5 The measurement comparison is a wash

| DGP-2 | B | C-off | C-shipped | C-hybrid | C-full |
|---|---:|---:|---:|---:|---:|
| exact-level accuracy | 0.6117 | 0.6067 | **0.6148** | 0.6135 | 0.6102 |
| questions per candidate | **17.47** | 20.27 | 21.60 | 20.47 | 21.81 |
| duration P50 (min) | 83.3 | 47.7 | 49.6 | **48.1** | 51.3 |
| duration P90 (min) | **95.3 — over cap** | **58.5** | 83.0 | 83.0 | 83.0 |
| modality blueprint | 0.9129 | 0.7685 | **0.9992** | **0.9992** | 0.9988 |
| marginal reliability | 0.9420 | 0.9496 | **0.9499** | 0.9462 | 0.9497 |

**Every arm is within 0.8pp of every other, and nine of the ten contrasts are non-inferior
at the 2pp margin** (the tenth, `C-off` vs `C-hybrid` under DGP-0, has an upper bound of
+2.38pp). The first run's "the DAG costs 2.85pp" was the biased sample. Measurement does not
decide between these architectures — which is what the scorecard concluded independently
from the earlier numbers, and it was right for a reason it could not have known.

What does separate them:

- **B asks 15% fewer questions and takes 37% longer.** Ranking on raw information buys
  expensive code and voice items; ranking on information-per-minute buys the clock. B's P90
  of 95.1 minutes **breaches the 90-minute cap**; every C arm stays inside it.
- **The blueprint separates them by 23 points.** `C-off` at 0.771 against 0.999 for every
  arm with the coverage gate on. Without it the assessment quietly stops being mixed.
- **`C-hybrid` is the shortest arm with the coverage gate on** — 20.47 questions against
  `C-shipped`'s 21.60, at the same accuracy (0.6135 vs 0.6148, non-inferior). The P(band)
  early exit costs nothing the 2pp margin can detect. That is weaker than §3's predicted
  +2.1pp but no longer an argument against it.
- **The coverage gate has a long P90 tail.** Every arm running it sits at 83.0 minutes
  against `C-off`'s 58.5 — inside the 90-minute cap, but with little room. Worth watching
  before the cap is tightened or a fourth competency is added.

## 7. Order of work

1. **The SE calibration cause is identified — act on it, do not re-diagnose it.** §6.2
   shows the posterior is calibrated (ratio 1.03–1.08, non-coverage 5.2–5.7%) when a
   competency is genuinely one skill, and 30–40% overconfident (1.31–1.43, 13.5–16.8%) when
   it is several. Same likelihood, same parameters, same prior — only the construct changes.
   That rules out the fractional likelihood, inflated `a`, grid truncation and prior width
   together. The remedy is to model the sub-competencies, or to widen reported intervals by
   a measured factor per competency until that exists. **The interim is honest reporting: a
   band RANGE, not a point band.**
2. **Run the grader gold set (Layer 2).** It is upstream of every score, weight and
   confidence in the system, it produces the confidence ECE that Approach C's whole
   propagation design gates on and that has never been measured, and it settles whether the
   simulator's confidence distribution — on which the inference-inert finding rests — is
   anywhere near a real grader's.
3. **Ship the scale change.** Configuration plus a report change, measured, and independent
   of 1 and 2. Report a band RANGE: within-one accuracy is 99% and exact is 70%.
4. **Make the P(band) rule conjunctive and re-measure.** Two lines in
   `convergence.evaluate`: require it alongside the precision target instead of before it.
   As an early exit it is accuracy-neutral and shorter on average but has a worse P90 tail;
   as an added requirement it is strictly stricter than today's stop. Either way the
   decision needs the tail measured, not just the mean.
5. **Add cost and LLM-call telemetry.** The plan's stated objective includes minimising
   runtime and LLM cost, and neither appears anywhere in the evaluation. It is a logging
   change, not a study.
6. **Replace the coverage gate with a standalone content-balanced selector**, then re-measure.
   If it reproduces the 0.000 → 1.000 coverage result, the graph has no remaining
   justification and the decision becomes clean rather than conditional.
7. **Research, not roadmap: a posterior per node.** §4 shows 9.9pp sitting in the
   single-θ-per-competency assumption. That is the graph's real opportunity and it is a
   different system from the one measured here.

## What is still not evidence

Layers 2, 5, 6 and 7 have not run. Fairness (F-01…F-06) has not been measured for any arm —
for an instrument assigning levels used in hiring, that is the largest untouched exposure.
Cost and LLM calls are absent. The exposure and bank-coverage figures were taken at
`top_k = 1` and do not transfer to production. Every number here is simulated: no real
candidate has sat this assessment.
