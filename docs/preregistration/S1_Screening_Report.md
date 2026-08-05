# C-shipped with configurable propagation — S1 screening report

**Pre-registration:** `C_Shipped_Propagation_Test_Plan.md` in this directory, with amendments 13.1–13.3.
**In this pass:** preconditions, the configuration surface, Tier-1 invariants, S1 screening on
two personas at a calibrated cell size, the blocking floor, and the adversarial and judged lanes.
**Not in this pass:** S2 response surface, S3 confirmation, persona P09. Deliverable 11 is not
produced.

---

## 0. The short version

**H1 is falsified, and two independent §9 kill criteria fire.**

> **`r` = 0.88, 95% CI [0.781, 0.953].** The pre-registration says corroboration cannot reach
> the gate when `r > 0.4` and the K sweep should stop. It is not close: the *lower* bound of
> the interval is 0.78. §2.2's own table puts `K = 4` at 13.5% wrong when `r = 0.8`, against a
> 3% gate — so even a graph that could satisfy `K` would not be rescued by it.
>
> **No factor moved the wrong-inference rate.** Twenty-eight of 108 cells produced any
> measurable inference at all; across those the pooled rate is 15.2% / 16.9% / 15.4% for
> P01 / P02 / P09, and the *best* upper bound anywhere in three complete designs is 19.6%
> against a 3% gate. No factor is active on that response, in any persona.

Either trigger alone ends the study. Both fire, and they fire for different reasons — one about
the candidates, one about the configuration surface — which is the strongest form the negative
could take.

Underneath them, four levels of that surface turn out to be unreachable or misnamed on this
bank. Not rare, not underpowered: impossible, and provable from the graph, the item bank and
the configuration alone at zero session cost.

### Finding 1 — `K ≥ 2` cannot be satisfied

The AIE competency graph has **30 PREREQUISITE edges over 30 distinct parents, and every parent
has exactly one direct child.** It is a forest of chains.

Under the only independence rule that makes `K` a safety control rather than a decoration —
keying on the source sub-competency — an ancestor needs `K` distinct descendants strongly
passed. At depth 1 it structurally has one. No session length and no cohort size produces a
second.

Measured, C-full / DGP-2 / P01, 200 sessions at `D = 4`:

| `K` | enforced inferences | verified | wrong | rate |
|---:|---:|---:|---:|---:|
| 1 | 161 | 161 | 31 | **19.3%** |
| 2 | 0 | 0 | 0 | — |
| 3 | 0 | 0 | 0 | — |

The 19.3% at `K = 1` sits alongside run 3's 22.4%, which is the check that the pipeline still
measures what it used to. The zeros are not a small number; they are the absence of the
quantity. **§4.3's rule applies: that is unmeasurable, not safe.**

### Finding 2 — `D > 1` is inert

Every AIE PREREQUISITE edge carries `strength = 0.5`. Inference strength is
`direct_effective × strength^d × modality × decay^d`, and it must clear
`minimum_inferred_weight = 0.15`:

| depth | best case at λ=0.7 | best case at λ=0.3 |
|---:|---:|---:|
| 1 | 0.3500 ✓ | 0.1500 ✓ (exactly on the floor) |
| 2 | 0.1225 ✗ | 0.0225 ✗ |
| 3 | 0.0429 ✗ | 0.0034 ✗ |
| 4 | 0.0150 ✗ | 0.0005 ✗ |

Depth 2 is below the floor **in the best case**, at `direct_effective = 1.0`. So nothing ever
propagates past one hop, and `D = 1` and `D = 4` are the same configuration. Confirmed
empirically: of 193 provenance records from a `D = 4` run, **all 193 are at distance 1.**

This also means the depth-stratified analysis (C-DAG-03d) that amendment 13.1 built the engine
to support has exactly one stratum to report on this bank. The machinery is correct and the
graph gives it nothing to separate.

### Finding 3 — `λ` is an on/off switch, not an attenuation

Because only depth 1 is reachable, λ enters as a single multiplier. At λ = 0.3 the depth-1
strength is 0.1500 — exactly the floor — so any candidate whose `weight × confidence` is below
1.0 falls under it. λ does not tune how far inference reaches; it decides whether inference
happens at all.

### Finding 4 — `C` is a modality filter, not a confidence gate

Instrumenting `is_strong_success` over real sessions, the confidence values that actually reach
the propagation gate take **exactly two values**:

| confidence | share | source |
|---:|---:|---|
| 1.00 | 88% | MCQ and code, which report full confidence |
| 0.90 | 12% | voice/open |

`grader_error_sd` is 0 on DGP-2 — only DGP-3 carries grader noise — so voice confidence is
exactly `0.90` with no variance and the rest is exactly `1.00`.

Nothing lands between. So **no threshold below 0.9 does anything at all**, and the only
threshold that does anything is one above 0.9, which removes voice wholesale. `C = 0.95` is
therefore not a confidence gate being tightened; it is a second modality allowlist, aliased
onto `M`. That is why `C` moved the rate slightly rather than sharply: it deleted 12% of the
evidence rather than filtering the least certain of it.

**This is why P09 cannot be evaluated on DGP-2.** §3.2 defines P09 as a candidate who is
confidently graded and wrong, attacking the gate that permits propagation — but with a
two-point confidence distribution there is no gate to attack. P09's `+0.15` moves 0.90 to 1.00
and clears the same threshold P01 already cleared. It is measurable only on DGP-3, where
grader error has variance. This sharpens §11's standing threat from "grader confidence is
generated by the harness and never compared to a real grader" to something stronger: **on the
DGP this study runs, it is degenerate.**

### Finding 5 — corroboration is farmable *or* inoperative, never useful

ADV-5's two probes ask opposite questions, and on this bank both answers disqualify `K`:

- **Bank probe** (zero sessions): `max_farmable_K = 2`; 4 of 33 sub-competencies have items
  identical in node, difficulty band and modality. Under `evidence` or `item` independence a
  candidate satisfies `K = 2` on those nodes by answering the same question twice.
- **Graph probe** (zero sessions): under `source_node` independence, `K ≥ 2` is unsatisfiable.

So the knob is either gameable or dead. There is no setting on this bank and this graph where
it is a working safety control.

---

## 1. The S1 screening result

**108/108 cells, three complete Resolution-IV designs — P01, P02 and P09 — at DGP-2, C-full,
n = 200 per cell (21,600 sessions).** Equal n by design: the persona cell-size weights exist so
a decisive persona gets the power its own gates need, and they are wrong for a *contrast*,
where unequal precision reads as a difference in the thing being measured.

### 1.1 `r`, the primary objective — and the first kill criterion

| quantity | value |
|---|---|
| tetrachoric `r` | **0.880** (Bonett–Price cross-check 0.871) |
| 95% CI (candidate-clustered bootstrap) | **[0.781, 0.953]** |
| candidates with ≥2 verified inferences | 235 (34 with exactly one) |
| design effect `1 + (m̄−1)·r` | **9.96** |
| §9 kill criterion `r > 0.4` | **FIRES** |

Pooled over all three personas, and it moved the same way each time a harder one was added:

| arms | `r` | 95% CI | width |
|---|---:|---|---:|
| P01 | 0.833 | [0.593, 0.995] | 0.402 |
| P01 + P02 | 0.861 | [0.722, 0.961] | 0.239 |
| P01 + P02 + P09 | **0.880** | **[0.781, 0.953]** | **0.172** |

The estimate rose and the interval narrowed at every step. A result that strengthens as the
evidence broadens is the opposite of one that depends on the sample it was found in.

`r` is the number the plan says decides whether H1 has an answer at all, and it lands at 0.88.
A candidate's inference errors are almost perfectly repeating: when the graph is wrong about
someone once, it is wrong about them again. That is the regime where corroboration does nothing
— §2.2's table puts `K = 4` at 13.5% wrong when `r = 0.8`.

So the `K` finding in §0 and this one are independent and mutually reinforcing. Even if the
graph gained the branching that would make `K ≥ 2` satisfiable, `r = 0.88` says the extra
observations would not be independent enough to help.

The design effect of 9.96 is worth stating separately: any wrong-inference rate computed over
these events has an interval roughly **√9.96 ≈ 3.2× wider** than a naive binomial one. Every
figure in §2 already accounts for this being unaccounted for — they are Clopper–Pearson bounds
that assume independence, and are therefore optimistic.

### 1.2 Which cells produced any evidence at all

**80 of 108 cells are UNMEASURABLE** — 27, 27 and 26 across the three arms — with firing volume
below 2% of the best cell's 0.890 verified inferences per session. Per §4.3 that is *unmeasurable, not
safe*. The pattern is identical in both arms, which is what a structural cause predicts and a
statistical one does not.

| level | cells measurable |
|---|---|
| `K` high (=3) | **0 / 16** |
| `K` centre (=2) | **0 / 4** |
| `K` low (=1) | 9 / 16 |
| `S` high (=0.95), among `K` low | **1 / 8** |
| `S` low (=0.80), among `K` low | 8 / 8 |

Every `K ≥ 2` cell in the design produced nothing, which is §0's Finding 1 reproduced across
the whole factorial rather than in one probe. Raising the success-score threshold to 0.95 has
nearly the same effect: it is a second way to switch propagation off rather than to tune it.

### 1.3 Main effects — and why the activity rule could not run

Across the 9 measurable cells in each arm, the wrong-inference rate spans **13.8%–19.3%**
(P01) and **12.8%–27.3%** (P02), and the **best upper bound anywhere in either design is 20.3%
against a 3% gate**. No factor is active on that response in either persona. `K` reports `n/a`
because it did not vary among cells that fired — which is the analysis refusing to enter a zero
from a cell that never fired, since that zero would drag the effect toward "this factor makes
propagation safer" precisely because it made it inoperative.

On the descriptive responses the effects are real but negligible, and they do not agree between
personas: `D` moves exact-level accuracy by **+0.0017** on P01 and **−0.0017** on P02, and
questions per session by **+0.08 items** (24.11 → 24.19). Accuracy spans 0.648–0.658 on P01 and
sits near 0.611 on P02; questions span 24.09–24.21 and 24.5 respectively. **Nothing in the
propagation surface buys a question.** §0's exchange rate — one question is worth at most 0.5pp
of accuracy — never comes into play, because there is no saving to price. An effect that
reverses sign between personas at a magnitude of 0.0017 is noise being read as structure, which
is the next paragraph's subject.

**A methodological defect, reported rather than worked around.** The centre-point pure-error SD
came out as **exactly 0.0**, so `SE(effect) = 0` and the 2σ rule marked every non-zero effect
"active", including one of +0.0017. The cause is that the harness is deterministic given
`(simulee, item)`, and the four centre points differ only in `M` and `E` — which change nothing
measurable — so the replicates are *identical*, not merely similar. **Centre-point replicates in
a deterministic simulation estimate zero pure error, and the activity rule degenerates.** The
fix is to vary the seed across centre points so they replicate the sampling rather than the
arithmetic. Until then, read the effect sizes and ignore the "active" column.

**This has since been fixed** (`--sample-seed`, and each centre point drawing its own
stratified subsample — measured overlap 15–26 of 200 where it was 200). The fix landed after
both arms had run and deliberately was not applied retroactively: re-running one arm under it
and not the other would have made the two incomparable, which costs more than the degenerate
activity column does. It takes effect for the next factorial.

### 1.4 The three personas — and a correction

§3.2 names two personas as decisive. P02 is the candidate who learned out of order, which is
exactly the case an upward inference gets wrong. P09 is the candidate who is confidently graded
and wrong, attacking the gate that permits propagation. All three arms at n = 200 per cell:

| | P01 (canonical) | P02 (spiky self-taught) | P09 (verbose shallow) |
|---|---:|---:|---:|
| measurable cells | 9 / 36 | 9 / 36 | **10 / 36** |
| verified inferences | 805 | 835 | **1,021** |
| pooled wrong-inference rate | **15.2%** | **16.9%** | **15.4%** |
| worst cell | 19.3% | **27.3%** | 19.7% |
| best cell UCB | 21.4% | 20.3% | **19.6%** |
| false blocking, range across cells | 4.6–8.6% | **7.8–13.2%** | 3.8–9.3% |
| exact-level accuracy | 0.651 | **0.611** | 0.631 |
| questions per session | 24.15 | 24.52 | **23.78** |

**P09 confirms Finding 4 from the data rather than from the code.** Its wrong-inference rate
is 15.4% against P01's 15.2% — indistinguishable. What it moves is the *volume*: 1,021 verified
inferences against 805, a 27% increase, and one extra measurable cell. That is precisely the
signature of a persona whose score inflation makes more observations count as strong successes
while its confidence inflation moves 0.90 to 1.00 — past a threshold that was already cleared.
P09 attacks a gate that has nothing to discriminate, so it lands as a volume effect and not a
safety one. On DGP-3, where grader confidence has variance, it would be a different test.

**A correction to an earlier figure in this programme.** A 40-session probe run while the
harness was being built put P02's wrong-inference rate at 34% against P01's 16%, and that was
quoted as P02 roughly doubling the rate. At n = 200 across a complete design it does not: 16.9%
against 15.2%. The probe was noise, and the earlier figure should not be used.

What P02 *does* do, clearly, is elsewhere:

- **False blocking rises by roughly half** (7.8–13.2% against 4.6–8.6%). That is the finding
  §3.2 predicts, landing on the mechanism that actually denies a candidate the chance to answer
  — and it takes the rate to more than six times the 2% gate.
- **Accuracy falls 4pp** (0.611 against 0.651). A candidate whose node abilities do not sit at
  one level is simply harder to measure, independent of propagation.
- **The worst single cell reaches 27.3%**, against P01's 19.3%. The tail is worse even where the
  pooled figure is close.

So neither decisive persona rescues or worsens the inference verdict — all three fail the 3%
gate by five to six times, and the best upper bound anywhere in 108 cells is 19.6%. What P02
does confirm is that **blocking's** cost lands exactly where the plan said it would, on a
complete design rather than a probe.

### 1.5 PRE-2 confirmed at scale

**Zero blank stop reasons across all 36 cells.** A representative distribution: precision 96.3%,
question budget 2.7%, time budget 1.0% — summing to 1 over named reasons, which is the
verification §1 asks for.

---

## 2. Safety rates as measured

C-full / DGP-2 / P01, 200 sessions, `K = 1`, `D = 4`:

| gate | measured | 95% UCB | threshold | verdict |
|---|---:|---:|---:|---|
| C-DAG-03 wrong inference | 31/161 = 19.3% | 25.1% | < 3% | **fails by ~8×** |
| C-DAG-04 false blocking | 48/589 = 8.2% | 10.3% | < 2% | **fails by ~5×** |
| C-DAG-01 inference precision | 0.807 | — | ≥ 0.97 | **fails** |

False blocking at 8.2% reproduces run 3's 8.7% closely, and sits inside the pre-registration's
predicted 10.8–14.6% structural floor for a graph whose edges are wrong 25% of the time by
construction. §0's decision to give blocking one confirmatory cell rather than a sweep is
confirmed by measurement rather than by argument.

### Per-edge stratification (C-DAG-03e) — the one genuinely new analysis

This did not exist before this branch; the engine discarded the traversal path one line after
computing it (amendment 13.1). It is the first evidence that the pooled rate hides real
structure:

| edge | wrong/verified | rate |
|---|---:|---:|
| C6.9 → C6.10 | 14/33 | **42.4%** |
| C1.3 → C1.4 | 2/9 | 22.2% |
| C6.4 → C6.5 | 6/31 | 19.4% |
| C6.6 → C6.7 | 2/13 | 15.4% |
| C3.4 → C3.5 | 1/8 | 12.5% |

A 3.4× spread across edges, under one pooled figure of 19.3%. This is the shape §12 anticipated
— "three edges out of thirty are reliable enough to act on" — and it is the only remedy that
does not also remove the edges that work. **It is also the analysis a per-edge allowlist would
need, and the allowlist factor `E` now exists to act on it.**

Note the denominators: even the busiest edge has 33 verified events at n = 200. Per-edge
conclusions need the S2/S3 sample sizes, not this one.

---

## 3. Preconditions

| # | Status | Evidence |
|---|---|---|
| PRE-1 | already fixed | `orchestrator.py:1073` gates the graph block on `graph_enabled()` |
| PRE-2 | **fixed** | `StopReason` enum; session stops name themselves on the variables they cut off. No blank reason in any run |
| PRE-3 | **fixed** | `CAT_EXPOSURE_TOP_K` 1 → 3; `docs/operations.md` forbids 1 in production and every prior exposure figure was measured there |
| PRE-4 | **fixed** | reliability reported at a stated population SD beside the design-cohort value |
| PRE-5 | **fixed, flagged** | `cat_band_probability_stop_conjunctive` — OFF in production, ON in the harness |
| PRE-6 | **fixed, flagged** | `cat_interval_widening_*` at the reporting boundary only, never the SE the stopping rule reads |
| PRE-7 | already fixed | stride + seeded shuffle |
| PRE-8 | **fixed** | manifest written before the loop as `status: running`, rewritten `complete` |

One unplanned precondition emerged: `Orchestrator._minimum_failures_to_block()` resolved the
policy with no bank id, falling through to `settings.active_bank` — wrong for any deployment
serving more than one bank, which `app/main.py` explicitly supports. Invisible in shipped data
because all three banks resolve to the deployment floor of 2, which is why it is now pinned by a
test on the lookup rather than on the number.

---

## 4. What the bank can measure at all

Found while repairing a failing test, and it bounds everything above.

**16 of AIE's 33 sub-competencies cannot reach `cat_se_target = 0.55` with their entire item
pool administered** to a candidate at θ = 0. For DA it is 5 of 6; PY is clean. Those variables
can only ever stop on the question budget, never on precision.

**13 of the 16 are in C6** — the main the graph's PREREQUISITE edges mostly connect, and the one
carrying the worst edge in the table above. The competency propagation is meant to help is the
one the bank measures worst.

The legacy `item_bank.json` fails the same check on all five competencies uniformly (best
attainable SE 0.598 against a 0.55 target). The prior diagnosis of "one weak pool" was an
artefact of a test that asserted in sorted order and aborted on the first.

---

## 5. Tier-1 invariants

36 assertions across INV-P1..P10 plus two additions. All pass.

Two are load-bearing for the DESIGN rather than for safety: a factorial assumes its factors are
separable and monotone, and both are now checked directly. Monotonicity is asserted
**non-vacuously**, because a factor that changes nothing is trivially monotone and an
implementation ignoring `K` would otherwise have passed.

Verified by mutation: stubbing the K check fails 3 tests, dropping `BLOCKED`/`CONTRADICTED`
from the status guard fails 2, ignoring `enforce_inference` fails 1.

**INV-P1 was restated** (amendment 13.2). Byte-identity with C-shipped is unachievable and
asserting it would have pinned a defect — C-shipped as shipped wrote `INFERRED_MASTERED` into
node state even with the deployment switch off, so two fields in one report contradicted each
other. `D = 0` matches C-shipped's *intended* behaviour and deliberately differs from its actual
behaviour.

---

## 6. Design and calibration

Resolution IV, 2^(7−2) = 32 factorial + 4 centre points over `D, K, C, S, λ, M, E`. Generators
`M = D·K·C`, `E = D·K·S`, chosen so the categorical factors carry the generated columns. The
alias structure is published in `design.json`: a Resolution-IV design does not lack two-factor
interactions, it confounds them knowably.

Pure error has **3 degrees of freedom**. The 2σ activity rule is a screening heuristic for
choosing which factors deserve a response surface, not a hypothesis test at any α, and it is
labelled as one everywhere it appears.

Calibration, from 50 sessions at each of four `D × K` corners, sized from the slowest:

| quantity | value |
|---|---|
| seconds per session | 0.26 (probe, lightly loaded) |
| items per session, E[L] | 24.6 |
| level discordance ψ | 0.35 |
| verified inferences/session at `K = 1` | 0.48 |
| verified inferences/session at `K = 3` | **0.00** |

The probe underestimates cost: it ran four cells on 24 workers, where the sweep runs 24 busy
ones and contends for memory bandwidth. Sizing from it is optimistic by roughly 2–3×, and a
future probe should saturate the pool rather than sample it.

Power was computed **before** the run, deliberately. An undecidable gate is a fact about the
budget rather than about propagation, and reporting it afterwards as a wide bound beside a
threshold reads as a failure of the thing being measured.

---

## 7. Adversarial lane

| ID | Result |
|---|---|
| ADV-3 | **passes structurally.** No path exists from transcript text to an `EvidenceEvent`; `InferredNodeSignal` carries no score or weight, so it cannot become one |
| ADV-4 | **0 duplicates in 5,140** direct evidence events |
| ADV-5 | **Finding 4** above |

ADV-4 matters more than before this branch: `K` counts observations, so an evidence event
admitted twice would not merely re-weight an estimate — it would manufacture the independent
confirmation the safety rule asks for.

---

## 8. Judged layer (§6)

Built, executed, **descriptive in full**.

DeepEval 4.1.5 pinned exactly, in an environment separate from the one that runs `pytest`,
judging through the same LiteLLM proxy as the rest of the project. All four canaries correct,
flip rate 0.0 across 5 runs: the judge fails a report citing an item that was never served,
fails one presenting an inference as an observation, and passes a properly labelled one.

**Cohen's κ is not computable, and that is permanent without raters.** It needs two rater
populations; repeated draws from one judge are one population sampled repeatedly. Five runs
measure self-consistency, and a judge can be perfectly self-consistent and consistently wrong.
§6.4's κ ≥ 0.60 bar is therefore unmet *and* unmeasurable, so **no judged number in this study
has authority over a Tier-2 or Tier-3 endpoint.**

G-01 gets a deterministic pre-filter ahead of the judge: its characteristic failures are set
operations, and a judge that agrees 95% of the time is worse than a set operation that agrees
always.

Five independent locks keep `pytest` from making a billed call; four are asserted by tests.

---

## 9. Threats to validity

§11 of the pre-registration stands unchanged. Four are worth restating because this pass
sharpened them.

- **Everything is simulated.** No real candidate has sat this assessment.
- **Grader confidence is generated by the harness.** P09's premise, and the confidence gate
  propagation depends on, rest on a distribution never compared to a real grader. Still blocking
  for any production decision.
- **The order-free persona surrogates.** P05, P06 and P12 are specified positionally, but
  `plan_for` must stay pure in `(simulee, item)` or the paired design loses the variance
  reduction it depends on. They ship as Bernoulli surrogates preserving the expected count,
  which preserves the marginal effect and **loses the serial correlation between position and
  degradation**. This study therefore cannot say whether Approach C's re-sequencing moves the
  warm-up window — the one question those three personas exist to probe.
- **Run 3 cannot be re-stratified.** Depth and edge provenance did not exist before this branch,
  so no depth- or edge-stratified figure may cite run 3 as its source (amendment 13.1).
- **The confidence gate is untested.** P09 ran, but Finding 4 shows DGP-2 gives it a two-point
  confidence distribution with nothing to discriminate — so this study measured propagation's
  behaviour under a gate that never rejected anything. DGP-3 is where that becomes a real test.

---

## 10. What was not finished

**The persona axis is complete** — all three of §3.2's named personas ran full designs. An
earlier attempt at the §3.2 cell-size weights was abandoned partway; its partial cells are kept
under `eval-results/sweep_s1_incomplete_P02/` and are **not** analysed, because a partial
persona folded into a complete design breaks the balance the effects depend on.

**Not run: S2, S3, and DGP-3.**

S2 and S3 are recommended against rather than merely skipped — see §11.

DGP-3 is the one that matters and is the honest gap. It is the only arm carrying grader error
(`grader_error_sd = 0.10`), and Finding 4 shows that **P09 is only testable there**: on DGP-2
the confidence distribution is two points, so the persona built to attack the confidence gate
has no gate to attack. This study therefore contains no test of the confidence gate at all.
That is a narrower claim than "P09 was tested and propagation survived it", and it is the
correct one.

The judged layer ran its canary validation only; the 60-report golden set was not judged.

Neither kill criterion depends on any of this. `K >= 2` is unsatisfiable by graph topology and
`D > 1` by arithmetic — both hold for every candidate under every DGP — and `r` rose while its
interval narrowed at each persona added.

---

## 11. What should happen next

0. **Do not run S2 at all.** Two §9 kill criteria fired. The pre-registration is explicit that
   `r > 0.4` stops the K sweep and that no active factor falsifies H1 — both happened, and the
   plan's own design says the correct response is to report and stop, not to look harder.
1. **In particular, do not run S2 on `K` or `D`.** Both have one reachable state on this bank.
   A response surface would trace a curve through a single point.
2. **Corroboration is a graph-authoring question, not a tuning one.** `K ≥ 2` needs ancestors
   with two or more distinct descendants, and the AIE graph has none. Either the graph gains
   branching — an authoring decision carrying its own validity burden — or `K` is removed from
   the configuration surface rather than shipped as an inert safety control.
3. **Depth is bounded by arithmetic, not policy.** With edge strength 0.5 and a 0.15 weight
   floor, nothing reaches depth 2. If multi-hop inference is wanted, that is a decision about
   edge strengths and the floor, and it should be made explicitly rather than discovered as a
   null effect.
4. **Blocking stays off.** 8.2% false blocking against a 2% gate, inside the predicted
   structural floor, exactly as §0 anticipated.
5. **If the question is ever reopened, reopen it on EDGES, not on factors.** The per-edge
   table now exists and shows a 3.4x spread — C6.9 → C6.10 wrong 42% of the time against
   C3.4 → C3.5 at 12.5%. That is the only axis in this study with any signal left in it. It is
   not a recommendation to run S2 now: `r = 0.88` bounds what any edge subset can achieve,
   because it is a statement about candidates rather than about edges. Reopening would need new
   pre-registration, and should be triggered by the graph gaining branching or by gold-set
   evidence that `r` is lower in the world than in this DGP — not by this study.
6. **The one experiment worth running is DGP-3, not S2.** It is cheap — one arm, the harness
   already supports it — and it is the only way to test the confidence gate at all, because
   DGP-2 gives that gate a two-point distribution with nothing to discriminate (Finding 4). It
   would also tell you whether `r` falls when grader error is genuinely noisy rather than
   absent, which is the single assumption the whole negative verdict rests on. If `r` stays
   near 0.88 under grader noise, the question is closed for good.
7. **Fix the centre points before any future factorial.** Deterministic replicates estimate
   zero pure error, which silently disables the activity rule (§1.3). Vary the seed across
   centre points so they replicate the sampling rather than the arithmetic.
8. **The bank's measurement floor is the more urgent finding.** 16 of 33 AIE variables cannot
   reach the precision target at any test length, concentrated in the main the graph most
   connects. That bounds every accuracy number this programme will produce, and no propagation
   setting improves it.
