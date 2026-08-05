# Test Plan — C-shipped with Configurable Propagation

**Status:** pre-registration. Nothing runs until §11 is signed.
**Subject:** `C-shipped` (coverage gate on, propagation off) extended so propagation is a **continuously configurable surface** rather than a boolean.
**Design arithmetic:** `plan_math.py`. Every sample size below is derived, not chosen.

> **Amendments after implementation begin are recorded in §13, never by editing the text above it.**
> Three amendments are already filed. Read §13 before acting on §2.3, §5 INV-P1, or §4.4.

---

## 0. What this tests, and what would falsify it

**Hypothesis H1.** There exists a propagation configuration, strictly more permissive than "off", that clears every safety gate while delivering a question saving worth having.

**H1 is falsified if** no cell in the confirmation stage produces a 95% upper bound below 3% on wrong inference *while* saving ≥ 0.5 questions per session at non-inferior decision accuracy.

**The design is built to falsify H1 cheaply.** Run-3 evidence says propagation at defaults is wrong 22.4% of the time. This plan is not an attempt to rescue it; it is an attempt to find the boundary of the safe region, or to establish that the region is empty and close the question permanently.

**Two things are settled before we start and are not re-litigated:**

- **Blocking gets one confirmatory cell, not a sweep.** The structural floor — P(descendant mastered | parent not mastered) = 10.8–14.6% — means even *perfect* parent knowledge yields a false-block rate of 10.8–14.6%, five to seven times the 2% gate. There is no feasible region to search.

  | Parent-verdict accuracy | Best attainable false-block (floor 10.8%) | (floor 14.6%) |
  |---:|---:|---:|
  | 0.80 | 0.186 | 0.217 |
  | 0.95 | 0.128 | 0.164 |
  | **1.00** | **0.108** | **0.146** |

  One cell confirms the floor per edge on the persona cohort. Then blocking stays off.

- **The prevalence question is not answerable here.** Propagation's cost lands on candidates who learned out of order. Break-even prevalence `p* = S·E/Y`:

  | Questions saved S | Y = 5pp | Y = 10pp | Y = 20pp |
  |---:|---:|---:|---:|
  | 1.0 | 10.0% | **5.0%** | 2.5% |
  | 2.0 | 20.0% | 10.0% | 5.0% |

  If propagation saves one question and costs 10pp on spiky candidates, it pays only where fewer than 5% of candidates are spiky. **What share of a self-taught developer pipeline is spiky is a gold-set question, not a simulation one.** This plan produces `Y` and `S`; it cannot produce `p`. Record that as a permanent limitation, not a to-do.

---

## 1. Preconditions — hard blockers

Nothing in §3 onward runs until all of these are green. Each is a defect identified in prior review.

| # | Precondition | Verification |
|---|---|---|
| PRE-1 | `Orchestrator.summarise` respects `graph_enabled()` | Regression test: report with flag off contains no graph fields |
| PRE-2 | B's blank stop reason fixed (34–38% of sessions emit `""`) | Stop-reason distribution sums to 1 over named reasons in every arm |
| PRE-3 | `cat_exposure_top_k = 3` for all substantive cells; `1` only in an explicit reproducibility lane | Manifest assertion |
| PRE-4 | Reliability reported **conditional on a stated population SD**, or replaced by the conditional SEM curve | Report schema change |
| PRE-5 | P(band) stopping rule made **conjunctive** with precision, not an early exit | Unit test: no session stops with SE > target |
| PRE-6 | Interval widening factor applied per competency (1.33–1.45 measured under DGP-2) | Coverage of published range ≥ 93% |
| PRE-7 | Cohort sampler strides across strata (already fixed) | Cohort validation, §4.4 |
| PRE-8 | Per-arm manifests written at run start, not run end | Manifest exists for aborted runs |

**PRE-5 and PRE-6 are not optional.** Every propagation configuration is evaluated by its effect on a posterior. Sweeping propagation while the posterior is 42% overconfident measures the interaction of two defects.

---

## 2. The propagation configuration surface

Propagation stops being a boolean. The following become first-class, runtime-switchable, and recorded in every manifest.

### 2.1 Factors

| Factor | Symbol | Levels | Default today |
|---|---|---|---|
| Max propagation depth | `D` | 0, 1, 2, 3, 4 | 4 |
| **Corroboration requirement** (independent strong successes before inferring) | `K` | 1, 2, 3 | 1 — **new knob** |
| Minimum propagation confidence | `C` | 0.80, 0.90, 0.95 | 0.80 |
| Success score threshold | `S` | 0.80, 0.90, 0.95 | 0.80 |
| Upward decay | `λ` | 0.3, 0.5, 0.7 | 0.70 |
| Max inferred weight | `W` | 0.20, 0.40, 0.60 | 0.60 |
| Modality allowlist | `M` | code / voice / code+voice / +mcq | code+voice |
| Edge allowlist | `E` | all / validated-only | all |
| Blocking min failures | `B` | 1, 2, 3 | 2 |

`D = 0` **must be bit-identical to C-shipped.** This is an invariant, not a hope — see INV-P1. *(Amended: see §13.2.)*

### 2.2 The corroboration knob, and why it is the headline

Requiring `K` independent strong successes before inferring an ancestor is the most promising single lever. Predicted, from the observed single-observation wrong rate of 0.2244, under a within-candidate error correlation `r`:

| `r` | K=1 | K=2 | K=3 | K=4 | first K under the 3% gate |
|---:|---:|---:|---:|---:|---|
| 0.0 | 0.224 | 0.050 | 0.011 | 0.003 | **3** |
| 0.2 | 0.224 | 0.085 | 0.032 | 0.012 | **4** |
| 0.4 | 0.224 | 0.120 | 0.064 | 0.034 | none |
| 0.6 | 0.224 | 0.155 | 0.107 | 0.074 | none |
| 0.8 | 0.224 | 0.190 | 0.160 | 0.135 | none |

**`r` decides whether H1 has an answer at all.** If a candidate's misjudged node drives both observations, corroboration does nothing. So **measuring `r` is a primary objective of this study, not a nuisance parameter** — estimate it directly as the tetrachoric correlation between inference-correctness indicators sharing a candidate and a target node.

### 2.3 Depth stratification — the analysis nobody has run

Run 3 pooled wrong-inference rate across all propagation distances. Depth-1 inference (immediate parent) and depth-4 inference (great-great-grandparent through three decayed hops) are different mechanisms reported as one number.

**Report wrong-inference rate stratified by `distance` from the first cell onward.** If depth-1 is 95% accurate and depth-4 is 40%, `D = 1` may pass where `D = 4` fails, and that is the likeliest route to a non-empty safe region. ~~This costs nothing — the data already exists in run 3's session dumps.~~ *(Struck: see §13.1. The data does not exist and the engine had to be changed to produce it.)*

Same for **per-edge** stratification: a validated-edge allowlist is the second likeliest route.

---

## 3. Cohort — levels × personas

### 3.1 Ability levels

Nine strata, equal *n*: θ ∈ {−4.0, −3.0, −2.0, −1.0, 0.0, +1.0, +2.0, +3.0, +4.0}. The ±4.0 strata are out-of-bank boundary probes and are analysed separately, never pooled into headline figures.

**Reliability is never reported on this cohort without a stated population SD** (PRE-4). A uniform design distribution has SD ≈ 2.3 and inflates reliability from ~0.79 to ~0.95. The design distribution is right for conditional accuracy and wrong for reliability. Report both: conditional accuracy on the design cohort, reliability re-weighted to N(0,1).

### 3.2 Personas

Each persona is a **generative modification to the response process**, specified precisely enough to implement. All are applied on top of the DGP-2 node-level structure unless stated.

| ID | Persona | Generative specification | What it attacks |
|---|---|---|---|
| **P01** | Canonical | Pure 3PL/GPCM at θ; node abilities equal | Baseline / null |
| **P02** | **Spiky self-taught** | `θ_node = θ + N(0, 0.9)`; prerequisite order deliberately violated for a random 30% of parent-child pairs | **Prerequisite inference — the decisive persona** |
| **P03** | Bootcamp graduate | Applied nodes `θ + 0.8`, foundational nodes `θ − 0.8` | Directional prerequisite violation |
| **P04** | Academic | Inverse of P03 | Opposite direction |
| **P05** | Rusty senior | True θ high; items 1–3 answered at `θ − 1.0`, recovering linearly by item 5 | Early-item misjudgement; the CAT's first-3 KL phase |
| **P06** | Anxious starter | Items 1–2 at `θ − 1.2`, then nominal | Warm-up; fairness |
| **P07** | Test-wise guesser | MCQ pseudo-guessing raised `c: 0.25 → 0.45` | 3PL misspecification |
| **P08** | Careless | Independent 8% slip on every item regardless of θ | Missing 4PL upper asymptote |
| **P09** | **Verbose shallow** | Voice/open: grader `score +0.10`, `confidence +0.15` above truth | **Attacks the confidence gate that permits propagation** |
| **P10** | Terse expert | Voice/open: `score −0.10`, `confidence −0.20` below truth | False negatives at high ability |
| **P11** | ESL | `score −0.15` on voice/open, `−0.05` on long-stem MCQ | Fairness / DIF |
| **P12** | Disengaged | `P(abandon)` rising 3pp per item from item 6; responses degrade `−0.4θ` after item 6 | Incomplete sessions, w=0 events |
| **P13** | Plateau learner | Hard ceiling: `P(correct) → c` for any item with `b > θ_ceiling` | Non-compensatory ability |
| **P14** | Adversarial | Injection payloads embedded in voice/open transcripts | Security; grader integrity |

**P02 decides the answer.** If propagation is safe on P02 it is safe. If it passes on P01 and fails on P02, propagation is only safe in a world where candidates learn in order — which is the structural floor restated as a person. P02 therefore gets 3× the cell size of the others.

**P09 is the second decisive persona.** Propagation gates on grader-reported confidence ≥ 0.80. P09 is a candidate who is confidently graded and wrong. If propagation survives P09 the confidence gate is doing real work; if not, the gate is a rubber stamp.

### 3.3 Cohort validation — mandatory before any cell runs

The first study's headline error was a cohort defect reported as a result. Before any propagation cell:

| Check | Bar |
|---|---|
| Ability distribution matches spec | KS test vs intended, p > 0.05 |
| Per-stratum n exactly equal | exact |
| **Confusion matrix dumped per band cell** | mass on the main diagonal; no shifted diagonal |
| Node map regenerated against the current ability draw | hash of node map matches hash of ability vector |
| Persona effect present and correctly signed | each persona's marginal deviation from P01 within 20% of spec |
| `D = 0` reproduces C-shipped exactly | INV-P1 |

The shifted-diagonal check exists because a stale node map shifted every response 0.8 logits low in a previous run and produced a 39pp artefact. **Any confusion matrix whose off-diagonal mass is asymmetric by more than 3:1 aborts the run.**

---

## 4. Experimental design

### 4.1 Three stages, with a hard exploration/confirmation split

Sweeping 30 configurations and reporting the best one that passes is how a config passes by chance. The split is not negotiable.

| Stage | Purpose | Cohort | Output |
|---|---|---|---|
| **S1 Screening** | Which of the 9 factors matter? | Exploration cohort, seed set A | Main-effect estimates; 2–3 active factors |
| **S2 Response surface** | Where is the boundary of the safe region? | Exploration cohort, seed set A | Operating-characteristic curves; **one** candidate configuration |
| **S3 Confirmation** | Does that one configuration hold? | **Held-out cohort, seed set B, never previously touched** | The verdict |

**S3 evaluates exactly one pre-registered configuration.** If it fails, H1 is falsified. There is no second attempt on the confirmation cohort without a new pre-registration and a fresh seed set.

### 4.2 Screening design

Full factorial over 7 sweepable factors = 2,592 cells = 4.1M sessions. Not runnable.

**Resolution-IV fractional factorial, 2 levels per factor, 1/4 fraction → 32 runs** (~51,200 sessions). Estimates all main effects clear of two-factor interactions. Factor low/high settings:

| Factor | Low | High |
|---|---|---|
| `D` depth | 1 | 4 |
| `K` corroboration | 1 | 3 |
| `C` confidence | 0.80 | 0.95 |
| `S` score threshold | 0.80 | 0.95 |
| `λ` decay | 0.3 | 0.7 |
| `M` modality | code only | code+voice |
| `E` edges | validated-only | all |

Plus 4 centre-point replicates to estimate pure error and test curvature.

### 4.3 The safety paradox — plan for it explicitly

Verified inferences needed for a 95% upper bound below the 3% gate:

| True wrong rate | Verified events needed |
|---:|---:|
| 0.0% | 99 |
| 1.0% | 213 |
| 1.5% | 348 |
| 2.0% | 809 |
| 2.5% | 3,364 |

And the session cost, since tightening reduces firing volume (baseline 0.87 verified inferences per session):

| Volume vs baseline | Sessions needed (true rate 1.0%) |
|---:|---:|
| 100% | 246 |
| 20% | 1,229 |
| 10% | 2,459 |
| 5% | 4,918 |
| **2%** | **12,294** |

**A configuration conservative enough to be safe fires so rarely that proving it safe costs 10–50× the sessions.** Two mitigations, both mandatory:

1. **Verify against node truth, not by serving extra questions.** In simulation this is free and it is what run 3 already did — 1,386 verified inferences with no verification questions asked. Keep that.
2. **Oversample firing sessions in S2/S3.** Generate candidates, retain all inference-firing sessions and a fixed fraction of the rest, and re-weight in analysis. Record the weights in the manifest.

If a candidate configuration's firing volume falls below 2% of baseline, **declare it unmeasurable rather than safe.** An unfalsifiable "looks fine" is the failure mode this section exists to prevent.

### 4.4 Cell sizes

| Stage | Cells | Sessions/cell | Total |
|---|---:|---:|---:|
| S1 screening | 32 + 4 centre | 1,600 | 57,600 |
| S2 response surface | ~12 | 4,000 | 48,000 |
| S3 confirmation | 1 (+ C-shipped control) | 8,000 | 16,000 |
| Blocking floor confirmation | 1 | 4,000 | 4,000 |
| Adversarial (§7) | 4 | 500 | 2,000 |

S3's 8,000 is set by the accuracy endpoint: run-3 discordance ψ ≈ 0.21–0.27 needs ~3,200–4,200 for 80% power at a 2pp margin; 8,000 gives margin for persona stratification.

*(Amended: see §13.3. This pass runs S1 at a calibrated reduced n and does not run S2/S3.)*

---

## 5. Gate tiers

Three kinds of claim, never mixed in one table.

### Tier 1 — deterministic invariants (unit tests; no α, no CI; any failure aborts)

| ID | Assertion |
|---|---|
| **INV-P1** | `D = 0` produces byte-identical session records to C-shipped, same seed *(amended, §13.2)* |
| INV-P2 | No inference recorded at distance > `D` |
| INV-P3 | No inference recorded with fewer than `K` corroborating observations |
| INV-P4 | No inferred signal carries score or weight into the posterior (`InferredNodeSignal` contract) |
| INV-P5 | Duplicate evidence events = 0 |
| INV-P6 | Zero-weight outcome leaves posterior and observation count unchanged |
| INV-P7 | Blocked node reported as untested, never as failed, in every field and every rendering |
| INV-P8 | Every propagation factor appears in the manifest with its actual runtime value |
| INV-P9 | Replaying a session's recorded evidence reproduces θ, SE, band exactly |
| INV-P10 | Turning propagation off mid-run does not alter already-committed evidence |

### Tier 2 — one primary endpoint, α = 0.05

**Exact-level accuracy, common band scale, non-inferiority at a 2pp margin, candidate-level BCa bootstrap (10,000 resamples), one-sided 95% upper bound.** Reference arm: C-shipped. Reported overall and stratified by persona.

### Tier 3 — safety rates as one-sided 95% upper bounds (Clopper–Pearson)

| ID | Metric | Gate |
|---|---|---|
| C-DAG-03 | Wrong inference rate | UCB < 3% |
| C-DAG-03d | **Wrong inference, stratified by depth** | reported per depth; gate applies at the configured `D` |
| C-DAG-03e | **Wrong inference, per edge** | any edge with UCB > 10% is removed from the allowlist |
| C-DAG-01 | Inference precision | ≥ 0.97 |
| C-DAG-04 | False blocking | UCB < 2% |
| U-05 | SE calibration ratio | 0.95–1.10 |
| U-02 | Interval non-coverage | 3–7% |
| NEW-r | **Within-candidate error correlation `r`** | estimated with CI; no gate — it is an input to the K decision |

### Tier 4 — descriptive, no verdict

Questions saved, duration P50/P90, over-cap rate, blueprint compliance, coverage satisfaction, stop-reason mix, exposure, information realisation, cost, LLM calls.

**Cost and LLM calls per session and per completed decision are mandatory here.** They were the stated objective of the original programme and have never been measured.

---

## 6. The DeepEval / GEval layer

### 6.1 Scope discipline

**LLM judges evaluate subjective quality only. They never gate a quantitative claim and never evaluate an invariant.** Anything expressible as a field check is a Tier-1 test, not a GEval metric. This is where most eval suites go wrong, and it is why §6.4's judge-reliability protocol runs before any judged metric is trusted.

Version pinning: DeepEval's parameter enum has moved (`LLMTestCaseParams` → `SingleTurnParams`) and `DAGMetric` casing varies across docs. **Pin the DeepEval version in the manifest and pin the judge model by fingerprint, not alias.**

### 6.2 Metrics

Per DeepEval's own guidance, `evaluation_steps` is supplied for every metric rather than free-text `criteria` alone, because supplied steps skip the generation phase and make scores materially more stable across runs — which is exactly the property this suite needs.

**G-01 — Evidence integrity (the metric that matters most)**

```python
from deepeval.metrics import GEval
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

evidence_integrity = GEval(
    name="EvidenceIntegrity",
    criteria=(
        "Determine whether the candidate report claims any competency was "
        "demonstrated when the underlying evidence for it was inferred rather "
        "than directly observed."
    ),
    evaluation_steps=[
        "Read 'context', which lists every sub-competency with a DIRECT or "
        "INFERRED evidence label taken from the session record.",
        "For each competency claim in 'actual_output', identify whether its "
        "supporting evidence is DIRECT, INFERRED, or absent.",
        "Heavily penalise any claim of demonstrated skill whose evidence is "
        "INFERRED but which the report does not mark as inferred.",
        "Heavily penalise any claim whose evidence is absent entirely.",
        "Do not penalise hedged language, brevity, or tone.",
        "A report that labels every inferred claim as inferred scores 1.0 "
        "regardless of how many inferred claims it contains.",
    ],
    evaluation_params=[
        LLMTestCaseParams.ACTUAL_OUTPUT,
        LLMTestCaseParams.CONTEXT,
    ],
    threshold=0.95,
    strict_mode=True,
    model=JUDGE,   # pinned fingerprint
)
```

This is the one GEval metric with a hard threshold, because propagation's characteristic failure is a report that presents a deduction as an observation. Everything else in §6.2 is advisory.

**G-02 — Blocked-versus-failed language.** Does prose describe blocked competencies as untested rather than as failed or deficient? Threshold 0.90, advisory. The *field* is checked by INV-P7; GEval checks the sentence a candidate actually reads.

**G-03 — Level justification coherence.** Does the stated rationale follow from the listed evidence? Advisory.

**G-04 — Inference contestability.** When propagation is on, could a candidate reading the report identify which specific answer led to an inferred claim, and contest it? Advisory — but this is the metric that determines whether an appeal process is even possible.

**G-05 — Voice interview quality** (`ConversationalGEval` over `ConversationalTestCase`): probe relevance, no answer leakage, no leading questions, appropriate follow-up depth.

**G-06 — Report structural correctness** (`DAGMetric`). A decision tree is the right shape here: fail fast on a missing requirement, then judge quality. This replaces what would otherwise be six correlated GEval metrics.

**G-07 — Arena comparison.** Two reports for the same candidate, propagation on versus off, blind and order-randomised: which is more useful and more honest to a hiring manager? Descriptive only — it never enters a gate.

### 6.3 Where GEval is forbidden

Wrong-inference rate · false-blocking rate · decision accuracy · reliability · SE calibration · duplicate evidence · any invariant · any question-count claim.

### 6.4 Judge reliability protocol — runs before any judged metric is trusted

| Requirement | Bar |
|---|---|
| Runs per test case | ≥ 5; score is the **median**, not the mean |
| Score stability | 95% CI reported per case |
| Threshold flip rate | < 5% across runs |
| **CI straddling the threshold** | case routed to **human adjudication**, not counted either way |
| Judge model | pinned by fingerprint; canary set re-run every execution, alert on drift |
| Agreement with humans | ≥ 50 cases dual-rated; Cohen's κ ≥ 0.60 before the metric is used at all |

`strict_mode=True` on G-01 makes it binary at threshold, which is what a gate needs — and which makes the flip-rate requirement above load-bearing rather than cosmetic.

Golden set: **20 reports per (persona × propagation config)** for the 4 decisive personas (P02, P09, P13, P14) × 4 configs = 320 judged reports. Sized so that a 5% flip rate is detectable.

---

## 7. Adversarial suite (P14)

Separate lane, deterministic assertions plus one GEval.

| ID | Attack | Assertion |
|---|---|---|
| ADV-1 | Injection in voice transcript instructing a perfect score | Grader output schema-valid; score reconciles with objective test results within tolerance |
| ADV-2 | Injection instructing `confidence: 1.0` | Confidence not taken from the manipulated call; propagation gate unaffected |
| ADV-3 | Candidate claims mastery of a blocked ancestor in free text | No inference recorded from unverified self-report |
| ADV-4 | Transcript attempting to reveal θ̂ or target difficulty | Grader prompt contains neither; assertion on the rendered prompt |
| ADV-5 | Repeated near-identical answers to farm corroboration `K` | Corroboration counts only **independent** observations; duplicate detection fires |

**ADV-5 is specific to this plan.** Adding a corroboration requirement creates an incentive to satisfy it cheaply. If `K` can be met by two answers to near-identical items, the knob is decorative.

---

## 8. Analysis plan — pre-registered

- **Primary:** exact-level accuracy, non-inferiority, 2pp margin, one-sided α = 0.05, candidate-level BCa bootstrap, 10,000 resamples. C-shipped as reference.
- **Safety:** Clopper–Pearson one-sided 95% upper bounds. No point estimates against thresholds.
- **Screening:** main effects from the Res-IV design with centre-point pure error; factors declared active at |effect| > 2σ.
- **Stratification, pre-declared:** persona, ability stratum, propagation depth, edge, modality. **No post-hoc subgroups.**
- **Clustering:** resample at candidate level; report per-persona breakdown always.
- **Exchange rate, fixed now:** one question saved is worth at most **0.5pp** of decision accuracy. Every efficiency claim is reported against this and against the Pareto frontier.
- **Multiplicity:** one primary endpoint. Safety rates as bounds. Everything else descriptive without verdicts.
- **Reliability:** always reported with the assumed population SD stated.

---

## 9. Kill criteria — stop and report

| Trigger | Action |
|---|---|
| Any Tier-1 invariant fails | Abort the run; fix; restart from S1 |
| Cohort validation fails (§3.3) | Abort before any cell |
| `D=0` is not bit-identical to C-shipped | Abort — the configuration surface is not sound |
| Screening shows no factor with |effect| > 2σ on wrong-inference rate | **Stop. H1 falsified: the rate is insensitive to every knob.** |
| Best S2 cell's firing volume < 2% of baseline | Declare unmeasurable, not safe |
| Measured `r` > 0.4 | Corroboration cannot reach the gate; **stop the K sweep and report** |
| S3 confirmation fails | H1 falsified. Propagation stays off permanently. No re-run without new pre-registration |
| Judge κ vs humans < 0.60 | All GEval metrics drop to descriptive; Tier-2/3 unaffected |

The `r > 0.4` and "no active factor" triggers exist so this study can end in **two weeks with a clean negative** rather than three months with a tuned positive.

---

## 10. Deliverables

1. Pre-registration document, signed, before any data generation
2. Cohort validation report including per-cell confusion matrices
3. Screening main-effects table with the active-factor call
4. Operating-characteristic curves: wrong-inference rate versus questions saved, one curve per active factor, with the 3% gate drawn
5. Measured within-candidate error correlation `r`, with CI
6. Depth-stratified and edge-stratified wrong-inference tables
7. Blocking floor confirmation, per edge
8. Judge reliability report before any judged result
9. GEval/DAG results for the 320-report golden set
10. Adversarial suite results
11. S3 confirmation: one configuration, one verdict
12. Break-even prevalence `p*` computed from the measured `S` and `Y`, stated as the open question it is

---

## 11. Threats to validity — recorded up front

| Threat | Status |
|---|---|
| **Everything is simulated.** No real candidate has sat this assessment. | Permanent until Layer 6/7 |
| **Grader confidence is generated by the harness.** P09's whole premise, and the propagation confidence gate, rest on a distribution never compared to a real grader. | **Blocking for any production decision.** Run the gold set (Layer 2) in parallel — it is the only thing that resolves this |
| The structural floor is a property of the DGP, not of the world | Real-world floor requires the gold set plus field data |
| Persona prevalence is unknown | This plan produces `S` and `Y`, never `p` |
| Fairness (F-01…F-06) not covered | P11 gives a signal, not a measurement. Separate study |
| Exposure figures | Valid only at `top_k = 3` per PRE-3 |
| Judge model drift | Canary set; fingerprint pinning |

---

## 12. What success looks like

**The most likely outcome is a clean negative**, and the plan is built so that arriving there is fast and defensible: no active factor, or `r` too high, or a safe cell too rare to measure. Any of those ends it in weeks.

**A positive outcome is narrow and specific** — most plausibly `D = 1`, `K = 2`, validated edges only, code modality only, on an allowlist of a handful of edges that survive per-edge stratification. That is not "propagation works." It is "three edges out of thirty are reliable enough to act on at depth one." Whether that is worth the maintenance surface is a judgement, not a measurement, and it should be made against the break-even prevalence in deliverable 12 rather than against the question saving alone.

---

## 13. Amendments

Filed after §11 was signed. Each records what was assumed, what was found, and what changed.
The text above is never edited in place; that is what makes the amendments visible.

### 13.1 — §2.3's depth stratification is not free (filed 2026-08-05)

**Assumed:** "This costs nothing — the data already exists in run 3's session dumps."

**Found:** it does not exist. `graph_delta.absorb` reduced every inference to a bare node id
(`self.inferred_mastered.update(s.node for s in result.inferred_signals)`), discarding the
`distance` that `InferredNodeSignal` had computed. No edge was recorded anywhere at all — the
traversal knew which `CompetencyEdge` it crossed but the signal kept only `(source_node,
distance)`, so a 2-hop inference through A→B→C was indistinguishable from A→B'→C. Depth
stratification was therefore impossible from persisted data, and per-edge stratification
doubly so.

**Changed:** the engine now records `edge_path` on each `InferredNodeSignal` and persists a
per-inference provenance record. C-DAG-03d and C-DAG-03e are computable from the first cell of
this study forward, but **not retrospectively** — run 3's numbers cannot be re-stratified, and
no depth- or edge-stratified figure in this study may cite run 3 as its source.

### 13.2 — INV-P1 restated (filed 2026-08-05)

**Assumed:** `D = 0` produces byte-identical session records to C-shipped.

**Found:** not achievable, because C-shipped *as it actually shipped* writes
`INFERRED_MASTERED` into persisted node state even with `GRAPH_UPWARD_INFERENCE_ENABLED=false`
— the computation ran and the deployment flag gated only *consumption*. Two fields in the same
report disagreed: `graph_inferred_mastered_nodes` was correctly empty while
`report.graph.mains[].nodes_inferred` was not. `D = 0` suppresses both.

So `D = 0` is identical to C-shipped's **intended** behaviour and deliberately different from
its **actual** behaviour. A byte-identity assertion would have pinned the defect.

**Changed:** INV-P1 now asserts equality over the observable outputs — served-item sequence,
per-variable θ / SE / band / stop reason, and the enforced inferred and blocked sets —
explicitly excluding audit and provenance fields. The defect itself is fixed separately, so
the two definitions of "off" now agree. The §9 kill criterion is read against this restatement.

### 13.3 — This pass runs a reduced S1 and does not run S2/S3 (filed 2026-08-05)

**Assumed:** §4.4's cell sizes (S1 at 1,600/cell, S2 at 4,000, S3 at 8,000; ~128k sessions).

**Found:** the evaluation harness had to be recovered before anything could run, and three of
the nine factors did not exist in the engine. The build is the majority of the work.

**Changed:** this pass runs S1 screening at a cell size **calibrated from a measured throughput
probe** rather than at 1,600, plus the blocking-floor cell and the adversarial lane. S2 and S3
are not run. Deliverable 11 is not produced.

The consequence is stated wherever it bites rather than buried here: every screening effect is
reported with the power actually achieved, and any safety gate that the achieved n cannot
decide **even at a true rate of zero** is reported as "not decidable at n=…" rather than as a
wide upper bound that reads like a failure. A reduced screening stage can still fire the §9
kill criteria — "no active factor" and "r > 0.4" are both reachable at reduced n — which is
why screening is the stage worth running first.
