# C-shipped adaptive assessment remediation retest

## Executive Summary

The concrete release blockers found in the baseline audit were fixed in production code
and retested across four 100-candidate cohorts: DGP-2/P01, DGP-2/P02, DGP-3/P01, and
DGP-3/P09 (400 candidates, 1,200 candidate-main units). Deterministic invariants pass
11/11, Bandit has zero high-severity findings, and all four reported SE-calibration ratios
are inside 0.95-1.10. Direct coverage and modality compliance are now 100% in every
cohort, and no modeled session exceeds 90 minutes.

The previous immediate `BLOCK` condition is cleared, but the current production
classification is **R&D** because mandatory grader, report-faithfulness, adversarial,
fairness, and release-scale evidence still does not exist. Exact-band accuracy remains
61.00-64.33%, below the harness's 80% absolute-quality bar. Because that evidence
does not justify operational certification, every band now serializes as `provisional`
even when the model's internal stopping rule says it converged.

## Baseline and Change Scope

The bank, graph, cohort, and candidate sample are unchanged. Baseline and post-fix runs use
cohort seed 42, paired sample seed 0, AIE, DGP-2/P01, 100 candidates and 300
candidate-main units. Raw baseline artifacts remain in `runs/codex_baseline`; remediation
artifacts are in `runs/codex_postfix`. The expanded independent-cohort cycle is under
`runs/codex_cycle4_fixed`, with exact cohort filename, SHA-256, and persona in every
manifest.

Production changes:

- persist a unique entropy-backed exposure RNG per HTTP, Streamlit, and voice session;
- make P(band) a real veto on every measurement-convergence branch;
- credit modality only to the main for which an item was presented and gate convergence
  until attainable modality minimums are met;
- calibrate reported AIE uncertainty per measured competency (C1=1.55, C3=1.50,
  C6=1.36), leaving unmeasured/global fallbacks unchanged;
- separate model convergence from operational certification and default certification off;
- stop exposing grader-only `reference_solution` text as candidate `starter_code`;
- bind evaluation output to an exact cohort hash and reject ambiguous persona analysis;
- revalidate queued items against the current clock and prioritize ready items that close
  hard graph-coverage gaps before redundant precision probes;
- terminate realtime rooms cleanly when the gateway closes and preserve captured speech;
- close partial realtime sockets/tasks on handshake, opening-prompt, writer, receive, and
  audio-send failures, with bounded handshake waits;
- serialize CAT answers per session and reject a concurrent stale response with HTTP 409;
- return only a candidate-safe grade receipt and default Streamlit to candidate-safe mode,
  hiding answer keys, grader detail, posterior math, queues, traces, and diagnostics;
- bound process-local CAT/live registries, add explicit CAT deletion, fail closed at active
  capacity, and keep the Live debug ring private unless explicitly enabled;
- use native async FastAPI handlers/tests and non-credentialed wildcard CORS for this
  unauthenticated helper instead of an invalid wildcard-plus-credentials policy;
- restore default TLS certificate verification for live-helper traffic;
- clear all mypy errors in the application and evaluation tooling and mechanically reduce
  correctness-relevant Ruff debt.

## Architecture Review

Graph isolation remains intact: AIE resolves 30 prerequisite edges with zero inference and
zero blocking permissions. Direct evidence remains the only path into a psychometric
likelihood. The RNG is now stateful per assessment rather than reconstructed from a
constant seed. Stopping is still layered, but normal convergence now requires minimum
evidence, precision, P(band), difficulty corroboration, attainable modality minima, and
direct graph coverage.

## Code Findings

- Backend: 672 passed, 1 skipped after the donor-synthesis and deployment regressions.
- Service seam: 67 passed.
- Release invariants: 11 passed.
- mypy: zero errors across 113 application and evaluation files.
- Bandit: zero high, one medium, 21 low; the medium finding is the pre-existing sandbox
  temporary-directory heuristic.
- Dependency audit: no known vulnerabilities in declared requirements.
- Coverage: 63% with `--source=app,evaluation`; `app/main.py` is 65%, the standalone voice
  runner is 94%, realtime transport is 73%, and realtime room logic is 72%.
- Ruff: the application and changed boundary-test scope passes when `EXE002` is excluded.
  Sixty-seven genuine findings remain in auxiliary evaluation/test tooling. The mounted
  NTFS workspace adds 164 false executable-bit findings although tracked modes are 100644.

## Deterministic Invariants

All 11 assertions pass, including the two baseline failures:

1. precision cannot converge when the reported band's probability is below target;
2. omitted session seeds no longer place every candidate on RNG(0).

Zero-weight no-op, binary equivalence, graph-posterior isolation, one-response rollup,
direct coverage, modality configuration, observation floor, budget behavior, and graph
flags continue to pass.

## Bank Health

AIE remains unchanged at 300 valid unique items: 150 MCQ, 75 code, and 75 voice. Main-level
supply supports the shipped blueprint and direct critical coverage. The existing sub-node
constraint remains: 16/33 nodes cannot independently reach SE 0.55 at theta zero, with 28
optimally targeted items required to close all recorded gaps.

Two additive human-test profiles now ship without replacing DA, PY, or AIE. `AIE-JR-V3`
contains 60 active items over C1 (39 MCQ, 12 code, 9 voice). `JAI-600` retains all 600
authored items over C1-C6; 64 public-only code items are quarantined, leaving 536 active
items (300 MCQ, 86 code, 150 voice). Every executable code payload validates, every voice
payload produces a non-empty rubric, and both coverage-only graphs contain no prerequisite
edges. The import is reproducible and records source hashes and transformation provenance.

Neither source carries empirical CAT parameters. Each new item now copies a real prior-bank
triple from an explicit semantic-main, modality, and authored-difficulty stratum, and
records its exact donor. The synthesis restores information supply: bank-floor analysis
reaches SE 0.55 across theta -2..2 within 12 questions (worst best-case SE: 0.4210 for
`AIE-JR-V3` and 0.3668-0.4209 across `JAI-600` mains). It does not calibrate the new item
wording, so these profiles remain suitable for workflow/usability pilots rather than
operational score interpretation or inclusion in the production bank hard gate.

## Psychometric Simulation

Expanded robustness smoke:

| Cohort | Exact band | SE calibration | 95% coverage | Decision consistency | Modality | P90 min |
|---|---:|---:|---:|---:|---:|---:|
| DGP-2/P01 | 61.33% | 0.9834 | 97.00% | 0.7351 | 100% | 84.00 |
| DGP-2/P02 | 64.33% | 1.0808 | 93.33% | 0.7404 | 100% | 84.38 |
| DGP-3/P01 | 61.67% | 1.0636 | 94.33% | 0.7136 | 100% | 83.18 |
| DGP-3/P09 | 61.00% | 1.0729 | 94.33% | 0.7208 | 100% | 83.23 |

Pooled interval coverage is 94.75%, and every scenario is now inside the 93-97% gate. The
per-competency correction is
reporting-only and does not feed back into selection or stopping. Raw posterior band
probabilities are not empirically calibrated: among model-converged units, exact-band
accuracy remains roughly 65%. Operational certification is therefore disabled by default.

## Selector Performance

Mean session length is 26.90-27.52 items across the four cohorts. Model-converged rates are
69.67-76.00%; question-budget stops account for 21.67-27.00% of units. No such result is
operationally certified. P90 remains below 90 minutes in every cohort, and none of the 400
modeled sessions exceeds 90 minutes.

Random/Fisher/KL/CFAT paired selector comparisons, regret, exposure, overlap, and
release-scale duration tails remain `NOT_RUN`.

## Grader Performance

MCQ grading is deterministic. The required 250+ twice-scored adjudicated human corpus for
voice/open/code does not exist, so QWK, MAE, signed bias, and ECE remain `NOT_ESTIMABLE`.

## DeepEval Results

No billed judge run was made. The prior four-canary artifact remains descriptive only and
cannot satisfy grader or report hard gates.

## Agent Picker

Deterministic shortlist containment and fallback tests remain green. Live rank-1
agreement, utility retention, entropy, and stabilized override-rate studies remain
`NOT_RUN`.

## Conversation Quality

The required multi-turn clarification, non-coaching, mixed-language, ASR-corruption, and
anomaly-probe matrix remains `NOT_RUN`.

## Report Faithfulness

Deterministic citation and inferred-label guards remain present, but no representative
full-report corpus has been judged. Faithfulness and critical hallucination gates remain
`NOT_RUN`.

## Fairness

No subgroup dataset was added. Language/accent, accommodation, device, network,
WER-conditioned residual, duration, challenge-balance, and accuracy gaps remain
`NOT_RUN`.

## Adversarial Robustness

Duplicate evidence remains zero in the paired post-fix audit. The candidate-facing code
answer-key leak is fixed and regression-tested. Prompt injection, Unicode,
keyword stuffing, rubric copying, fake output, long-answer, and grade-change scenarios
remain `NOT_RUN`.

## Performance

Modeled P90 duration is 83.18-84.38 minutes. A direct FastAPI test now exercises session
creation, retrieval, validation, persistent RNG use, a complete CAT trajectory, report
serialization, and missing-room WebSocket handling. Fake-gateway tests now cover clean
transport closure, terminal packaging, handshake/opening/writer/receive/send failures, turn
gating, and the voice runner. Concurrent answer tests prove stale submissions cannot double
advance an assessment. Process-local state is bounded and explicitly deletable, but
multi-worker durability, authenticated access, concurrent load, and real-gateway performance
remain untested.

## Hard Gates

Passing with current evidence: deterministic invariants, graph isolation, duplicate
evidence, invalid picker containment, bank schema, modality compliance, reliability, SE
calibration, direct coverage, P90 duration, and critical security. Exact-band quality fails
in every expanded cohort. `hard_gates.json` records this remaining measured failure
separately from the required lanes that have not been run.

Not run or not estimable: human grader agreement/bias, report faithfulness/hallucination,
prompt-injection grade invariance, and fairness. `hard_gates.json` is authoritative.

## Scorecard

Not computed. The mission permits scoring only after every hard gate has explicit evidence;
several required human/AI/product gates remain absent.

## Known Limitations

This remains four 100-candidate smokes, not a 4,000–10,000-candidate release run. No new
gold labels or subgroup data were available. The uncertainty factors were selected from
these simulated cohorts and therefore require locked, independent confirmation. Exact-band
accuracy remains poor despite substantially improved uncertainty reporting and corrected
duration/coverage tails. CAT and Live state is still process-local: worker restarts lose
active sessions and multiple workers do not share state. The public helper also has no
authentication/authorization design; adding one requires product identity and deployment
requirements rather than an inferred code-only change.

## Recommended Next Work

1. Run the frozen DGP-0..7 release suite and paired selector arms with exact cohort hashes.
2. Investigate the 21.67-27.00% question-budget rate and 61.00-64.33% exact-band accuracy before increasing
   the item cap, which would threaten the duration gate.
3. Validate or replace the per-competency reporting correction on locked independent data;
   keep `CAT_BAND_DECISIONS_CERTIFIED=false` until exact accuracy passes.
4. Build the adjudicated grader corpus and complete report, adversarial, conversation,
   and fairness lanes.
5. Add real-gateway, load, and persistence tests and reduce repository lint debt.

## Current Verdicts

```text
CODE VERDICT: PASS for critical blockers; non-critical lint/coverage debt remains
MEASUREMENT VERDICT: FAIL (accuracy and release-scale validation remain insufficient)
AI / AGENT VERDICT: NOT ESTIMABLE
PRODUCT VERDICT: NOT ESTIMABLE

FINAL PRODUCTION CLASSIFICATION: R&D
```

The original immediate `BLOCK` caused by deterministic and security defects is resolved.
This is not authorization to ship or shadow: required quality gates fail or lack evidence.
