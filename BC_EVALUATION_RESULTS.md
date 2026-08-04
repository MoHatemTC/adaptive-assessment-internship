# Approach B vs Approach C — evaluation results

**Ran:** 4 August 2026
**Harness:** `backend/evaluation/`, on `eval/bc-graph-augmented` and `eval/bc-voice-code-mcq-streamlit`
**Approach branches were not modified.**

---

## Verdict

**Retain Approach B's measurement core. Do not enable the competency DAG.** On the bank
and configuration these branches ship, enabling the graph costs decision accuracy, adds
questions, and blocks candidates who can do the work. That holds in the arm built to
favour it most.

**And neither approach is fit to certify a level.** Every absolute quality gate fails for
every arm. The binding constraint is not the DAG — it is the reporting scale and the
posterior's calibration, and both are fixable without either architecture.

Three of the seven findings below invalidate parts of the test plan itself, not the
systems under test.

---

## 1. What was run

| Layer | Status |
|---|---|
| 1. Deterministic invariants (INV-01…06) | **Run.** 15/15 pass on C, 12 pass + 3 skip on B |
| 0. Bank reality check | **Run** |
| 0a. Configuration pre-study | **Run**, 400 simulees × 3 mains × 6 configurations |
| 0b. Offline edge validity | **Run**, 1,000 simulees, DGP-1 and DGP-2 |
| 2b. Pilot for discordance ψ | **Run**, n=200 |
| 3. Simulation, 4 arms × 4 DGP arms | **Run**, 960 paired candidates per cell (2,880 candidate-competency units) |
| 2. Grader gold set | **Not run** — needs 250 expert-labelled cases |
| 4. Historical replay | **Not run** — needs real response data; the precondition is measured instead |
| 5. DeepEval / G-Eval | **Not run** — needs a judge model |
| 6. Shadow, 7. Pilot | **Not run** — needs candidates |

Nothing in the un-run layers is estimated or faked.

### Why four arms and not two

Section 6 of the plan freezes the level boundaries, convergence defaults, exposure policy
and question budget so that "only the adaptation architecture should differ". Between
these two branches, seven things on that list differ:

| | Approach B | Approach C |
|---|---|---|
| level boundaries | `round(3 + θ)`, 1.0-wide, clipped | explicit cuts, 1.6-wide |
| stable-band SE ceiling | 0.80 | 0.55 |
| difficulty corroboration | none | required before convergence |
| selection criterion | raw information | information per minute |
| modality blueprint | none | `{"code": 1, "voice": 1}` enforced |
| item budget | 60 | 120 |
| picker utility layer | absent | present |

A two-arm B-vs-C contrast would attribute all of that to the DAG. So the graph branch runs
three configurations of one codebase — `C-off` (master switch off), `C-shipped` (branch
defaults), `C-full` (inference, blocking, filtering, utility, edges forced on) — and
`C-off` vs `C-full` is the only contrast in which the DAG is the sole difference.

The frozen settings live in `backend/evaluation/arms.py` (`FROZEN_ENV` plus each arm's own
flags) and the cohorts are regenerated deterministically from `--seed 42`, so the run
reproduces from the repository alone. Per-arm manifests are written at the end of a
completed run; this run was stopped early (see §10) and produced none, which is the one
reproducibility artefact missing.

---

## 2. The DAG does not pay for itself

**Primary endpoint — exact-level accuracy on a band scale identical across arms, one-sided
95% upper bound on the degradation, non-inferiority margin 2pp.**

| DGP arm | accuracy `C-off` | accuracy `C-full` | degradation | 95% UB | verdict |
|---|---:|---:|---:|---:|---|
| **DGP-0 null** | 0.6917 | 0.6785 | +1.32pp | +2.71pp | **not shown** |
| **DGP-1 matched** | 0.6562 | 0.6278 | +2.85pp | +4.27pp | **not shown** |
| DGP-2 partial | 0.6562 | 0.6538 | +0.24pp | +1.70pp | non-inferior |
| DGP-3 noisy | 0.6462 | 0.6371 | +0.90pp | +2.27pp | **not shown** |

DGP-1 is the arm where the world's prerequisite structure is *exactly* the graph's edges —
the most favourable case that can be constructed. There, enabling the DAG costs 2.85pp of
decision accuracy and the upper bound is more than twice the margin.

**Efficiency — question reduction, target ≥15%:**

| contrast | DGP-0 | DGP-1 | DGP-2 | DGP-3 |
|---|---:|---:|---:|---:|
| `C-off` → `C-full` | −7.4% | −4.3% | −5.0% | −3.9% |
| `C-off` → `C-shipped` | −2.3% | −2.1% | −2.3% | −1.7% |

Negative throughout: the graph makes assessments **longer**. Not "less than 15% shorter" —
the sign is wrong. The mechanism is visible in the stop-reason distribution: the coverage
gate holds competencies open until every required sub-competency has direct evidence, and
question-budget stops rise from 1.7% (`C-off`) to 3.4% (`C-full`).

**The null control behaves.** Under DGP-0, where no prerequisite structure exists, `C-full`
shows no benefit — it is 1.3pp worse and 7.4% longer. Had it shown a gain there, nothing
else in this study would have meant anything.

---

## 3. DAG safety: false blocking fails by 2.5×; wrong inference cannot be measured

Verified against known node truth, so every inference and every block is checked — no
verification questions needed.

| gate | bar | DGP-1 | DGP-2 | DGP-3 | verdict |
|---|---|---:|---:|---:|---|
| C-DAG-04 false blocking | 95% UCB < 2% | **4.99%** | **5.33%** | **5.02%** | **FAIL** |
| C-DAG-03 wrong inference | 95% UCB < 3% | 1 event | 8 events | 1 event | **not estimable** |
| C-DAG-05 missed blocking | descriptive | 34.6% | 44.5% | 37.1% | — |
| C-DAG-11 duplicate evidence | 0 | 0 / 22,536 | 0 / 22,437 | 0 / 22,252 | **PASS** |
| C-DAG-15 over-convergence | UCB < 2% | 0.00% | 0.00% | 0.00% | **PASS** |

**About 5% of blocked nodes belong to candidates who can actually do the work** — 1,071
candidates in DGP-1 alone were denied the chance to demonstrate a skill they have. The
gate is 2%, and this is under a graph whose edges are *correct by construction*. Blocking
error here is not an edge-quality problem; it is a consequence of deciding a block from one
noisy observation of the parent.

**Upward inference fired in 1 session out of 960.** `graph_allow_mcq_single_hit_inference`
is false and code/voice inference needs score ≥ 0.80 *and* confidence ≥ 0.80 simultaneously,
which almost never co-occurs. So C-DAG-01/03 are unmeasurable at this n — exactly the
situation the validation document's A1 predicts, arriving from the opposite direction: the
problem is not that verification is expensive, it is that the event never happens. **The
efficiency mechanism Approach C is built around is, in practice, inert even when fully
enabled.**

**Premature convergence is 8–12% for every arm, against a <2% gate** — including Approach
B (13.4%). Measured as: the competency claimed convergence and its own 95% credible
interval excludes the truth. This is a property of the shared CAT core, not of the DAG.

---

## 4. Neither approach clears an absolute bar

The plan has no absolute quality gate anywhere; every gate in §27 is B-versus-C relative.
Added per the validation document's B3, evaluated per arm with no reference to the other:

| gate | bar | B | C-off | C-shipped | C-full |
|---|---|---:|---:|---:|---:|
| Exact-level accuracy | ≥ 0.80 | 0.644 | 0.656 | 0.634 | 0.628 |
| Accuracy in every band | ≥ 0.70 | 0.213 | 0.155 | 0.174 | 0.161 |
| Marginal reliability | ≥ 0.85 | 0.706 | 0.684 | 0.684 | 0.681 |
| Worst true-θ decile | ≥ 0.65 | 0.458 | 0.292 | 0.313 | 0.295 |
| Decision consistency | ≥ 0.75 | 0.659 | 0.682 | 0.697 | 0.687 |
| SE calibration, RMSE / mean SE | 0.95–1.10 | 1.372 | 1.292 | 1.282 | 1.294 |
| P90 duration | ≤ 90 min | 64.4 ✓ | 48.3 ✓ | 48.6 ✓ | 56.6 ✓ |

*(DGP-1; the other arms differ by under 3pp. Only duration passes.)*

Two of these matter more than the rest:

**SE calibration 1.28–1.37.** The reported standard error understates the actual error by
28–37%. Nominal 95% credible intervals cover 86–88%. The instrument is systematically
overconfident, in every arm — so this is not double counting from the graph, it is the
shared measurement core. A candidate is told a level with more confidence than the evidence
supports.

**Accuracy in the worst band, 0.155–0.213.** The headline 63–67% hides bands the instrument
cannot resolve at all.

If the absolute gates fail for both approaches, the correct output of the study is not
"select B" — it is that neither is ready, and the constraint is the scale, not the DAG.

---

## 5. Approach B's own report overstates its accuracy by 17 points

`exact_level_accuracy` on Approach B's **native** banding is **0.8181**. On the common
scale it is **0.6441**.

B maps θ to a level with `int(clip(round(3 + θ), 1, 5))`. The clipping makes bands 1 and 5
unbounded — every candidate below θ = −1.5 is "Novice" — so the two extreme bands are far
wider than the three interior ones, and accuracy in them is nearly free. The number B would
print on a candidate report is 17 points higher than the number the same estimates earn on
an even scale.

This is the single strongest reason the comparison had to be run on a common scale. It is
also a reporting defect in its own right, independent of the B/C decision.

---

## 6. Phase 0a: the scale is worth more than the architecture

Band count × stopping rule, simulation only, 400 simulees × 3 mains, no graph:

| bands | width | stop rule | exact accuracy | within one | items | Δ accuracy | Δ items |
|---:|---:|---|---:|---:|---:|---:|---:|
| 4 | 2.00 | P(band) ≥ 0.80 | **0.9375** | 1.000 | 6.48 | **+17.1pp** | −0.83 |
| 4 | 2.00 | SE ≤ 0.55 | 0.9308 | 1.000 | 7.31 | +16.4pp | 0.00 |
| 5 | 1.60 | P(band) ≥ 0.80 | 0.7692 | 1.000 | 7.07 | +0.3pp | −0.24 |
| 5 | 1.60 | SE ≤ 0.55 | 0.7667 | 1.000 | 7.31 | — | — |
| 8 | 1.00 | P(band) ≥ 0.80 | 0.3292 | 0.984 | 10.29 | −43.8pp | +2.97 |
| 8 | 1.00 | SE ≤ 0.55 | 0.2917 | 0.980 | 7.31 | −47.5pp | 0.00 |

Moving from an 8-band scale to a 5-band scale is worth **+47.5 points** of decision
accuracy at zero item cost — three times the validation document's estimate and more than
an order of magnitude larger than any DAG effect measured here. Moving to 4 bands buys a
further +16.4pp, also free.

This is not a free lunch: a coarser scale reports less. The honest reading is that the
instrument supports about four or five distinguishable levels at this precision, and an
eight-level scale claims a resolution nobody has. Within-one-level accuracy is 100% at four
and five bands, which is what makes a band *range* the defensible thing to report.

The P(band) stopping rule is slightly better on both axes — more accurate and shorter. It
is also, today, **unreachable**: `convergence.evaluate` implements it, but neither caller
(`variables.evaluate_finalisation`, `adaptive/session.py`) passes `band_probability`, so it
defaults to `None` and the rule never fires. `cat_band_probability_stop_enabled` is live,
documented, and dead.

---

## 7. Phase 0b as specified cannot validate an edge

The architecture ships `scripts/validate_prerequisite_edges.py`, which computes
P(pass child | fail parent) per edge; the validation document proposes this as the Phase 0b
gate that prunes bad edges before the experiment.

Run against the DGP-2 cohort, where 8 of the graph's 30 edges are **deliberately absent
from the world**:

```
enabled by P(pass child | fail parent) : 30 of 30
spurious edges refused                 :  0 of 8
```

Every spurious edge passes. The confound is ability: a candidate who fails C1.1 is a weak
candidate and will probably fail C1.2 too, prerequisite or not. Any two nodes under one
main are correlated through θ, so the conditional is ≈ 0 for every pair in the graph and
the check green-lights it whole.

Conditioning on ability does not rescue it. Regressing the child's score on θ and on
"failed the parent" gives a coefficient of −0.015 for real edges and −0.022 for spurious
ones — indistinguishable, and both far short of any usable bar. Only weak candidates fail
the parent, so there is too little independent variation to attribute an effect to.

**A prerequisite edge cannot be validated from observational response data.** Establishing
one needs the manipulation C-DAG-01 already describes — serve the child to candidates who
failed the parent — which is an experiment, not a calibration matrix. Phase 0b should be
struck from the plan or rewritten as an experimental design.

*(Layer 4's replay precondition was measured while here: 169 of 300 items were ever served,
and a 33-item linear form covers 80% of all administrations. That is more optimistic than
the validation document's 35–40% estimate because exposure control is off — `top_k = 1` was
frozen for reproducibility, which maximally concentrates selection. Production, at
`top_k = 3`, needs more.)*

---

## 8. Approach C's propagation is inert as shipped

All 30 `PREREQUISITE` edges in `competency_graph_AIE.json` carry:

```json
"allow_upward_inference": false,
"allow_downward_blocking": false,
"metadata": {"validation_status": "unvalidated"}
```

On the shipped configuration Approach C's inference and blocking cannot fire at all. The
only live part of the graph layer is the coverage gate, and that is what `C-shipped`
measures: +2.2pp worse level accuracy and 2.1% more questions than `C-off`, with one
benefit — modality blueprint compliance rises from 0.972 to **0.999**, and required
sub-competency coverage from **0.000** to **1.000**.

That last pair is the one genuinely positive result for the graph layer. Without the
coverage gate, *no* competency in the `C-off` arm ends with all its required
sub-competencies directly measured. The gate costs about half a question per competency and
is the only thing enforcing that the assessment covers what it claims to cover.

`C-full` — the arm with edges forced on — is what the architecture describes, and it is the
arm that fails.

---

## 9. Smaller findings

- **The graph master switch does not disable graph reporting.** Every other entry point
  checks `graph_config.graph_enabled()` first; `Orchestrator.summarise` calls `self._graph()`
  unconditionally, so with `COMPETENCY_GRAPH_ENABLED=false` the report still loads the graph
  and computes coverage. The documented contract — "off means the engine behaves as though
  no graph existed" — does not hold for the report.
- **Time-aware selection costs modality breadth.** `C-full` blueprint compliance is 0.714
  against B's 0.979: ranking on information per minute favours MCQ about 6.6× and the graph
  penalties compound it. Only the coverage gate restores breadth (`C-shipped`, 0.999).
- **Held-out prediction is a wash.** DeLong on 29,738 matched held-out responses:
  ΔAUC = +0.0008 (p = 0.60). Brier and log loss also flat. The plan gates on AUC (M-06) and
  does not gate Brier or log loss; on this evidence none of the three discriminates.
- **Information realisation ratio ≈ 1.05** for the C arms and 0.969 for B — selection's
  expected evidence weight is about right, slightly conservative.
- **Approach B's branch ships 9 failing tests**, including its whole `TestFullSession`
  orchestration suite. They break on voice items reaching a grader that expects a
  `GradedVoiceResponse` — fallout from the bank switch to AIE. Pre-existing; not caused by
  this work.
- **`test_bank_can_support_the_configured_precision_target` fails on both branches** for
  "Agentic AI & Orchestration" in the legacy `item_bank.json`. The AI Engineer bank used
  here is fine: worst reachable SE at 12 items is 0.36 against a 0.55 target.

---

## 10. Statistical basis

- **Primary endpoint:** exact-level accuracy on a common band scale, one endpoint, one-sided
  α = 0.05. The plan's 26 untiered gates would fail an acceptable Approach C **73.6%** of the
  time by noise alone; 18 statistical ones, 60.3%.
- **Interval:** BCa bootstrap, 4,000 resamples, resampling candidates within profile family.
  The unit is the candidate, not the candidate-competency row — three mains of one candidate
  are not three independent observations. Tango's score interval is reported alongside as the
  analytic cross-check; McNemar is not used, because it tests equality and the hypothesis is
  bounded difference.
- **Safety rates:** Clopper–Pearson one-sided upper bounds. Deterministic invariants are
  evaluated on counts, with no α.
- **Discordance ψ = 0.153–0.256**, in the range the validation document predicted. At ψ = 0.22
  the plan's 2pp gate needs **3,349 pairs** for 80% power; §31's n=500 could not detect a
  degradation below 3.5pp, and this study's achieved one-sided half-width is 1.3–1.5pp.
- **Achieved n = 960 paired candidates** (2,880 candidate-competency units) per arm per DGP
  arm, against the validation document's 4,000 target. The run was stopped early because the
  decisive contrasts were already outside the margin: `C-off` vs `C-full` under DGP-1 has a
  lower bound above 2pp, and the efficiency result has the wrong sign by 19 points. More
  candidates would tighten intervals that are not close to the decision boundary.
- **Between-arm θ correlation 0.593–0.795** — the pairing is working; a response is a pure
  function of (candidate, item), so the arms differ only in what they asked.

---

## 11. What to do

1. **Fix the reporting scale first.** Four or five bands, not eight, and report a band range
   rather than a point band. This is worth more than either architecture and costs nothing.
   Then re-run everything on top of it.
2. **Fix the SE calibration.** RMSE/SE at 1.26–1.38 means every reported confidence is wrong
   in the same direction, for both approaches. Nothing downstream is trustworthy until this
   is closed.
3. **Keep the coverage gate. Leave inference and blocking off.** The gate is the only part of
   the graph layer that earns its cost. Blocking fails its own gate by 2.5× even with correct
   edges; inference fires once in 960 sessions.
4. **Strike Phase 0b or redesign it as an experiment.** As specified it approves every edge,
   including edges that do not exist.
5. **Wire up the P(band) stopping rule or delete the flag.** It is currently unreachable.
6. **Do not run Layers 2, 5, 6 or 7 yet.** They are expensive and they all sit downstream of
   a measurement core that fails its absolute bars.

---

## Reproducing this

```bash
PYTHON=<python> \
C_BACKEND=<graph-augmented checkout>/backend \
B_BACKEND=<cat-only checkout>/backend \
OUT=eval-results \
./backend/evaluation/run_all.sh 4000 42

python -m evaluation.prestudy    --cohort eval-results/cohorts/cohort_DGP-1_*.json
python -m evaluation.edge_validity --cohort eval-results/cohorts/cohort_DGP-2_*.json --runs eval-results/runs
python -m evaluation.bank_check
pytest tests/test_bc_invariants.py tests/test_evaluation_harness.py
```

`backend/evaluation/README.md` documents the arms, the DGP arms, and the design decisions
behind them.
