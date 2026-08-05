# C-shipped with configurable propagation — S1 screening report

**Pre-registration:** `C_Shipped_Propagation_Test_Plan.md` in this directory, with amendments 13.1–13.3.
**In this pass:** preconditions, the configuration surface, Tier-1 invariants, S1 screening at a
calibrated cell size, the blocking floor, and the adversarial and judged lanes.
**Not in this pass:** S2 response surface, S3 confirmation. Deliverable 11 is not produced.

---

## 0. The short version

**H1 is not rescued, and the reason is structural rather than statistical.**

The plan treats the propagation surface as nine factors with reachable levels, and asks which
of them move the wrong-inference rate. Three of those levels turn out to be unreachable on this
bank — not rare, not underpowered, but impossible — and each is provable from the graph and the
configuration alone, at zero session cost.

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

### Finding 4 — corroboration is farmable *or* inoperative, never useful

ADV-5's two probes ask opposite questions, and on this bank both answers disqualify `K`:

- **Bank probe** (zero sessions): `max_farmable_K = 2`; 4 of 33 sub-competencies have items
  identical in node, difficulty band and modality. Under `evidence` or `item` independence a
  candidate satisfies `K = 2` on those nodes by answering the same question twice.
- **Graph probe** (zero sessions): under `source_node` independence, `K ≥ 2` is unsatisfiable.

So the knob is either gameable or dead. There is no setting on this bank and this graph where
it is a working safety control.

---

## 1. Safety rates as measured

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

## 2. Preconditions

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

## 3. What the bank can measure at all

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

## 4. Tier-1 invariants

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

## 5. Design and calibration

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

## 6. Adversarial lane

| ID | Result |
|---|---|
| ADV-3 | **passes structurally.** No path exists from transcript text to an `EvidenceEvent`; `InferredNodeSignal` carries no score or weight, so it cannot become one |
| ADV-4 | **0 duplicates in 5,140** direct evidence events |
| ADV-5 | **Finding 4** above |

ADV-4 matters more than before this branch: `K` counts observations, so an evidence event
admitted twice would not merely re-weight an estimate — it would manufacture the independent
confirmation the safety rule asks for.

---

## 7. Judged layer (§6)

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

## 8. Threats to validity

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

---

## 9. What should happen next

1. **Do not run S2 on `K` or `D`.** Both have one reachable state on this bank. A response
   surface would trace a curve through a single point.
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
5. **The per-edge table is the only route to a non-empty safe region**, and it now exists.
   C6.9 → C6.10 is wrong 42% of the time while C3.4 → C3.5 is wrong 12.5%. Sizing an S2 that can
   separate edges — rather than factors, which are inert — is the useful next experiment.
6. **The bank's measurement floor is the more urgent finding.** 16 of 33 AIE variables cannot
   reach the precision target at any test length, concentrated in the main the graph most
   connects. That bounds every accuracy number this programme will produce, and no propagation
   setting improves it.
