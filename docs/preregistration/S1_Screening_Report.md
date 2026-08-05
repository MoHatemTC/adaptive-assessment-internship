# C-shipped with configurable propagation — S1 screening report

**Pre-registration:** `C_Shipped_Propagation_Test_Plan.md` in this directory, with amendments 13.1–13.3.
**Scope of this pass:** preconditions, configuration surface, Tier-1 invariants, S1 screening at a
calibrated cell size, the blocking-floor observation, and the adversarial and judged lanes.
**Not in this pass:** S2 response surface and S3 confirmation. Deliverable 11 is not produced.

---

## 0. The short version

**H1 is not rescued, and the reason is structural rather than statistical.**

The plan's headline lever is corroboration: require `K` independent strong successes before
inferring a prerequisite, and the wrong-inference rate should fall from 22.4% toward the 3%
gate. §2.2 predicted `K = 3` would reach 1.1% at `r = 0`.

That prediction assumed observations would still exist at `K = 3`. On this bank they do not.
**The AIE competency graph has 30 PREREQUISITE edges over 30 distinct parents, and every parent
has exactly one direct child.** It is a forest of chains. Under the only independence rule that
makes `K` a safety control rather than a decoration — keying on the source sub-competency —
an ancestor needs `K` distinct descendants strongly passed, and at depth 1 it structurally has
one. No session length and no cohort size produces a second.

Measured over 200 sessions at `D = 4`:

| `K` | enforced inferences | verified | wrong | rate |
|---:|---:|---:|---:|---:|
| 1 | 161 | 161 | 31 | **19.3%** |
| 2 | 0 | 0 | 0 | — |
| 3 | 0 | 0 | 0 | — |

The 19.3% at `K = 1` is consistent with run 3's 22.4%, which is the check that the pipeline
measures what it used to measure. The zeros are not a small number; they are the absence of
the quantity.

So `K` has two settings and neither is usable: at 1 it is the rate the study exists to reduce,
and at ≥2 it is inoperative. **§4.3's rule applies — that is unmeasurable, not safe.**

The second finding is the mirror of the first. ADV-5's two probes ask opposite questions and
on this bank both answers make `K` useless:

- **Bank probe** (zero sessions): `max_farmable_K = 2`; 4 of 33 sub-competencies have items
  identical in node, difficulty band and modality. Under `evidence` or `item` independence a
  candidate satisfies `K = 2` on those nodes by answering the same question twice.
- **Graph probe** (zero sessions): `K ≥ 2` is unsatisfiable at depth 1 under `source_node`
  independence.

Corroboration is therefore either farmable or inoperative depending on the keying. There is no
setting on this bank and this graph where it is a working safety control.

**False blocking reproduced the predicted structural floor.** Measured 9/70 = 12.9% (95% UCB
21.4%) on C-full/DGP-2, against the pre-registration's predicted 10.8–14.6% floor and a 2%
gate. §0's decision to give blocking one confirmatory cell rather than a sweep is confirmed by
measurement, not merely by argument.

---

## 1. Preconditions

| # | Status | Evidence |
|---|---|---|
| PRE-1 | **was already fixed** | `orchestrator.py:1073` gates the graph block on `graph_enabled()` |
| PRE-2 | **fixed** | `StopReason` enum; session stops now name themselves on the variables they cut off. Verified: no blank reason in any run |
| PRE-3 | **fixed** | `CAT_EXPOSURE_TOP_K` moved 1 → 3 in `FROZEN_ENV`; `docs/operations.md` forbids 1 in production, and every prior exposure figure was measured there |
| PRE-4 | **fixed** | reliability reported at a stated population SD alongside the design-cohort value |
| PRE-5 | **fixed, flagged** | `cat_band_probability_stop_conjunctive`, OFF in production, ON in the harness |
| PRE-6 | **fixed, flagged** | `cat_interval_widening_*` applied at the reporting boundary only, never to the SE the stopping rule reads |
| PRE-7 | **was already fixed** | stride + seeded shuffle, `run_arm.py:143-147` |
| PRE-8 | **fixed** | manifest written before the loop with `status: running`, rewritten `complete` |

An unplanned precondition emerged. `Orchestrator._minimum_failures_to_block()` resolved the
propagation policy with no bank id, so it fell through to `settings.active_bank` — wrong for any
deployment serving more than one bank, which `app/main.py` explicitly supports. Invisible in the
shipped data because all three banks currently resolve to the deployment floor of 2, which is
why it is now pinned by a test on the LOOKUP rather than on the number.

---

## 2. What the bank can measure at all

Found while repairing a failing test, and it bounds everything else in this report.

**16 of AIE's 33 sub-competencies cannot reach the `cat_se_target` of 0.55 with their entire
item pool administered** to a candidate at θ = 0. For DA it is 5 of 6. Only PY is clean.

Those variables can only ever stop on the question budget, never on precision. **13 of the 16
are in C6 — the main the graph's PREREQUISITE edges mostly connect.** The competency propagation
is meant to help is the one the bank measures worst.

The legacy `item_bank.json` fails the same check on all five of its competencies, uniformly:
best attainable SE 0.598 against a 0.55 target. The prior diagnosis of "one weak pool" was an
artefact of a test that asserted in sorted order and aborted on the first.

---

## 3. Tier-1 invariants

36 assertions across INV-P1..P10 plus two additions. All pass.

Two are load-bearing for the DESIGN rather than for safety: a factorial assumes its factors are
separable and monotone, and both are now checked directly. Monotonicity is asserted
**non-vacuously** — a factor that changes nothing is trivially monotone, so an implementation
ignoring `K` entirely would have passed a containment check.

Verified by mutation: stubbing the K test fails 3 tests, dropping `BLOCKED`/`CONTRADICTED` from
the status guard fails 2, ignoring `enforce_inference` fails 1.

**INV-P1 was restated** (amendment 13.2). "Byte-identical to C-shipped" is unachievable and
asserting it would have pinned a defect — C-shipped as shipped wrote `INFERRED_MASTERED` into
node state even with the deployment switch off, so two fields in one report contradicted each
other. `D = 0` matches C-shipped's *intended* behaviour and deliberately differs from its actual
behaviour.

---

## 4. Screening design

Resolution IV, 2^(7−2) = 32 factorial runs + 4 centre points, over `D, K, C, S, λ, M, E`.
Generators `M = D·K·C` and `E = D·K·S`, chosen so the two categorical factors carry the
generated columns — a categorical factor has no midpoint and no curvature, so aliasing it costs
less than aliasing a continuous one.

The alias structure is published in `design.json`. A Resolution-IV design does not lack
two-factor interactions; it confounds them in a known pattern, and that distinction matters when
an effect comes out large.

Pure error has **3 degrees of freedom**. The 2σ activity rule is a screening heuristic for
choosing which factors deserve a response surface, not a hypothesis test at any α, and it is
labelled as one everywhere it appears.

Balance is asserted rather than assumed: every factor is high in exactly half the runs, all four
combinations of every factor pair appear equally often, and the generated columns equal their
generators. If `D` were high more often when `K` is high, the `D` effect would carry part of the
`K` effect and nothing in the analysis could separate them again.

---

## 5. Calibration

Measured on 50 sessions at each of the four `D × K` corners, sized from the **slowest**:

| quantity | value |
|---|---|
| seconds per session | 0.26 |
| items per session, E[L] | 24.6 |
| level discordance ψ | 0.35 |
| verified inferences per session (`K = 1`) | 0.48 |
| verified inferences per session (`K = 3`) | **0.00** |

Power was computed **before** the run, deliberately: an undecidable gate is a fact about the
budget rather than about propagation, and reporting it afterwards as a wide upper bound beside a
threshold reads as a failure of the thing being measured.

---

## 6. Adversarial lane

| ID | Result |
|---|---|
| ADV-3 | **passes structurally.** No path exists from transcript text to an `EvidenceEvent`; `InferredNodeSignal` carries no score or weight, so it cannot become one |
| ADV-4 | **0 duplicates in 5,140** direct evidence events |
| ADV-5 | **the finding** — see §0. Farmable under weak keying, unsatisfiable under strong |

ADV-5 matters more than it would have before this branch, because `K` counts observations: an
evidence event admitted twice does not merely re-weight an estimate, it manufactures the
independent confirmation the safety rule asks for.

---

## 7. Judged layer (§6)

Built, executed, and **descriptive in full**.

DeepEval 4.1.5 pinned exactly, in an environment separate from the one that runs `pytest`,
judging through the same LiteLLM proxy as the rest of the project. All four canaries correct
with a flip rate of 0.0 across 5 runs: the judge fails a report citing an item that was never
served, fails one presenting an inference as an observation, and passes a properly labelled one.

**Cohen's κ is not computable, and that is permanent without raters.** It requires two rater
populations; repeated draws from one judge are one population sampled repeatedly. What five runs
measure is self-consistency, and a judge can be perfectly self-consistent and consistently
wrong. §6.4's κ ≥ 0.60 bar is therefore unmet *and* unmeasurable, so **no judged number in this
study has authority over a Tier-2 or Tier-3 endpoint.**

G-01 gets a deterministic pre-filter ahead of the judge. Its characteristic failures — a
citation of an unserved item, an unlabelled inference claimed as demonstrated — are set
operations, and a judge that agrees 95% of the time is worse than a set operation that agrees
always.

Five independent locks keep `pytest` from ever making a billed call; four are asserted by tests.

---

## 8. Threats to validity

Everything in §11 of the pre-registration still stands. Three are worth restating because this
pass sharpened them:

- **Everything is simulated.** No real candidate has sat this assessment.
- **Grader confidence is generated by the harness.** P09's whole premise, and the confidence
  gate propagation depends on, rest on a distribution never compared to a real grader. This
  remains blocking for any production decision.
- **The order-free persona surrogates.** P05, P06 and P12 are specified positionally, but
  `plan_for` must stay pure in `(simulee, item)` or the paired design loses the variance
  reduction it depends on. They ship as Bernoulli surrogates preserving the expected count.
  This preserves the marginal effect and **loses the serial correlation between position and
  degradation** — so this study cannot say whether Approach C's re-sequencing moves the warm-up
  window, which is the one question those three personas exist to probe.

And one specific to this pass: **run 3 cannot be re-stratified.** Depth and edge provenance did
not exist before this branch, so no depth- or edge-stratified figure may cite run 3 as its
source (amendment 13.1).

---

## 9. What should happen next

1. **Do not run S2 on `K`.** It has two reachable states on this bank and neither is usable.
   The response surface would trace a curve through one point and a hole.
2. **The corroboration question is a GRAPH-AUTHORING question, not a tuning one.** `K ≥ 2` needs
   ancestors with two or more distinct descendants. The AIE graph has none at depth 1. Either the
   graph gains branching — which is an authoring decision with its own validity burden — or `K`
   is removed from the configuration surface rather than shipped as an inert safety control.
3. **Blocking stays off.** The measured 12.9% false-block rate sits inside the predicted
   structural floor and five to six times the gate, exactly as §0 anticipated.
4. **The bank's measurement floor is the more urgent finding.** 16 of 33 AIE variables cannot
   reach the precision target at any test length, concentrated in the main the graph most
   connects. That bounds every accuracy number this programme will ever produce, and no
   propagation setting improves it.
