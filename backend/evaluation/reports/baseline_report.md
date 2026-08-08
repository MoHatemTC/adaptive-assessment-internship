# C-shipped adaptive assessment evaluation — pre-fix baseline

> Historical snapshot. Superseded by `final_report.md` and the post-fix artifacts under
> `runs/codex_postfix/`; the top-level hard-gate and scorecard JSON files now describe the
> remediation retest.

## Executive Summary

Final decision: **BLOCK**.

The deterministic measurement core is thoughtfully isolated and extensively tested, but the
release contract fails two strict invariants, the realistic DGP-2 smoke shows materially
overconfident uncertainty and sub-target modality compliance, and required human-grader,
fairness, report-faithfulness, adversarial, and release-scale selector evidence is absent.
Unconditional TLS verification bypass on the live-helper path is an additional release blocker.

## Baseline

Commit `0f86002f15378daef6dab7a233f5ccec0d0e4810`, active bank AIE, cohort seed 42 and paired
sample seed 0. Bank and graph
hashes are frozen in `evaluation/manifest.json`. The actual production defaults match the
mission's C-shipped graph and selection baseline. The only noteworthy harness difference is
that it sets the inactive `cat_band_probability_stop_conjunctive` flag true while the
band-probability stop remains off; production has both false.

## Architecture Review

Question loading, deterministic admissibility, time-aware ranking, optional LLM selection,
modality grading, generic posterior update, direct graph coverage, finalization, and reporting
are separated cleanly. Graph deductions cannot carry psychometric score/weight, and AIE's
resolved bank policy independently keeps all prerequisite inference/blocking inert.

The architecture's main release gaps are stateful randomness and stop composition. RNG state is
not persisted per session, and normal convergence is assembled from checks at different layers
rather than one auditable conjunctive decision.

## Code Findings

Backend: 606 passed, one skipped. Service seam: 67 passed. Dependency audit: no known
vulnerabilities. Coverage: 56% overall, with strong measurement-core coverage and weak/zero
coverage on important HTTP and realtime product boundaries.

Static gates are not clean: Ruff 493 findings, mypy 21 backend errors, Bandit 3 high/1
medium/25 low. The release-blocking security finding is unconditional `verify=False` for live
voice helper requests.

## Deterministic Invariants

Nine release-spec categories pass: zero-weight measurement no-op, binary equivalence, graph
posterior isolation, one-response/one-observation rollup, direct-only coverage, configured
modality floor, six-observation precision floor, twelve-question non-convergent budget stop,
and the intended graph flags.

Two fail:

1. INV-09: precision convergence fires with `P(band)=0`; production does not implement the
   full conjunctive V2 stop.
2. INV-10: omitted HTTP seeds and every Streamlit session start at zero, producing identical
   exposure-control streams across sessions.

Under the mission's decision logic, either deterministic failure prevents shipment.

## Bank Health

AIE has 300 unique valid items (150 MCQ/75 code/75 voice). Main-level modality, coverage, and
precision supply are sufficient. Direct critical coverage reached 100% in the smoke.

At the sub-node level, 16/33 nodes cannot reach SE 0.55 at theta zero with their complete
sub-pools. At least 28 optimally placed items are needed to close the recorded gaps. This is a
content/calibration bottleneck for node-level claims, not a failure of the current main-level
bank loader.

## Psychometric Simulation

Fresh C-shipped DGP-2/P01 smoke: 100 candidates, 300 candidate-main units.

| Metric | Value | Verdict |
|---|---:|---|
| Ability RMSE | 0.6914 | descriptive |
| Signed/absolute bias | signed bias not emitted; MAE 0.5775 | incomplete |
| Marginal reliability | 0.9474 | PASS |
| SE calibration | 1.3245 | FAIL |
| 95% interval noncoverage | 13.0% | FAIL |
| Exact band accuracy | 61.67% | descriptive |
| Within-one-band accuracy | 100% | descriptive |
| Worst true-ability decile | 30.0% | poor tail |
| Decision consistency | 0.6916 | below repository's 0.75 bar |

The posterior is too narrow under the primary realistic multidimensional DGP. Reliability does
not rescue uncertainty calibration; the two metrics answer different questions.

## Selector Performance

The current selector is adaptive (KL for the first three observations, posterior-expected
Fisher thereafter) and ranks expected evidence-weighted information per minute. The smoke
realized information ratio was 1.0524, direct coverage 100%, mean/median questions 23.66/24,
and P50/P90 duration 53.54/83.55 minutes.

Modality compliance was 96.33%, failing the 99% gate. Exposure, pool utilization, overlap,
top-item concentration, regret, and paired Random/Fisher/KL/CFAT comparisons were not run.

## Grader Performance

MCQ grading is deterministic. Code has hidden tests and bounded LLM contribution in code, but
no mutation score was produced. Voice/open lacks the required 250+ twice-scored adjudicated
human corpus, so exact agreement, +/-1 agreement, QWK, MAE, signed bias, and ECE are
`NOT_ESTIMABLE`.

## DeepEval Results

No billed DeepEval run was made in this audit. A prior pinned DeepEval 4.1.5 artifact has four
canaries, all correctly classified with zero flip rate, but explicitly reports Cohen's kappa as
not computable and treats all judged results as descriptive. It is not evidence for the required
grader or report hard gates.

## Agent Picker

Deterministic assertions and code inspection support a zero invalid-pick guarantee: the model
only sees a shortlist, invented ids fall back, repeats are excluded before ranking, and picks
below the relative utility floor are overridden. Live rank-1 agreement, mean/P10 utility
retention, entropy, and stabilized override rate are `NOT_RUN`.

## Conversation Quality

Clarification/non-coaching, anomaly probes, mixed-language behavior, ASR corruption, and the
required multi-turn scenario matrix are `NOT_RUN`. Existing voice unit tests do not substitute
for judged conversational trajectories.

## Report Faithfulness

The graph report code preserves direct/inferred labels and deterministic prefilters catch
fabricated item citations and unlabelled inferred claims. A representative full report corpus
was not evaluated for faithfulness, hallucination, relevance, bias, and toxicity; hard gates are
`NOT_RUN`.

## Fairness

No release-grade subgroup dataset or analysis was available. WER-conditioned grading residual,
language/accent, accommodation, device, network, duration, challenge balance, and accuracy gaps
are `NOT_RUN`. Absence of evidence is not evidence of parity.

## Adversarial Robustness

Existing deterministic graph tests report zero duplicate evidence in 2,495 events and verify
that self-report cannot become graph evidence. The required prompt-injection, Unicode,
keyword-stuffing, rubric-copying, fake-output, long-answer, and grade-change matrix across
voice/open/code was not run, so the injection grade-change gate is `NOT_RUN`.

## Performance

The modeled P90 duration is 83.55 minutes and passes the 90-minute gate; 7% of smoke sessions
exceeded 90 modeled minutes, showing a tail still worth investigating. The deterministic harness
runs quickly, but no concurrent API/load, LLM outage latency, persistence durability, or realtime
voice performance test was run.

## Hard Gates

Passed: graph isolation, duplicate evidence, invalid picker containment, bank schema, direct
coverage, reliability, and P90 duration.

Failed: deterministic invariants, modality compliance, SE calibration, interval coverage, and
security. Human grader, report, fairness, full adversarial, and several agent gates are not run or
not estimable. `hard_gates.json` is authoritative for individual statuses.

## Scorecard

Not computed. The mission permits weighted scoring only after every hard gate; multiple gates
failed. `scorecard.json` contains raw metrics and null area scores.

## Known Limitations

This audit ran a 100-candidate smoke only for DGP-2/P01. It did not execute DGP-0/1/3-7 at
nightly or release scale, mutation testing, load testing, the full selector benchmark, human
gold scoring, fairness analysis, or billed semantic evaluation. Historical propagation studies
are reused only as contextual safety evidence and are not treated as current selector results.

## Recommended Fixes

1. Persist a unique RNG per session and keep explicit seeding test/replay-only.
2. Design and shadow a versioned conjunctive stop; calibrate it before changing production.
3. Fix uncertainty calibration under DGP-2 and independently revalidate interval reporting and
   stopping.
4. Make modality minima a convergence veto and rerun time-pressure/bank-exhaustion scenarios.
5. Restore TLS verification and cover the public live-helper deployment.
6. Build the adjudicated grader corpus and complete report, adversarial, conversation, and
   fairness lanes.
7. Close sub-node bank gaps, then run paired nightly and release-scale DGP/selector studies.

## Final Verdict

```text
CODE VERDICT: FAIL
MEASUREMENT VERDICT: FAIL
AI / AGENT VERDICT: FAIL (required evidence is not estimable)
PRODUCT VERDICT: FAIL

BLOCK
```

The system should not be exposed to real assessment decisions until the deterministic and
security blockers are fixed. After that, the appropriate next state is R&D evaluation, not
immediate shadow or production deployment, because uncertainty, modality, grader, fairness,
and adversarial gates still require evidence.
