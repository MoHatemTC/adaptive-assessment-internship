# Evaluation issues

## EVAL-001 — RESOLVED — constant RNG streams across sessions

- Component: HTTP and Streamlit session selection
- File: `backend/app/main.py:76`, `backend/app/main.py:85`, `streamlit/main.py:321`
- Reproduction: `python -m pytest evaluation/invariants/test_c_shipped_invariants.py -q`
- Expected: omitted seeds create a session-unique entropy source or persist one generator per session.
- Actual after fix: omitted seeds draw entropy and HTTP, Streamlit, and voice sessions retain one generator for their lifetime. Explicit creation seeds remain available for tests/replay.
- Measurement impact: same-ability candidates receive correlated exposure-control windows, increasing overlap and item leakage risk and invalidating INV-10.
- Recommended fix: generate and persist a cryptographically seeded session RNG state; allow an explicit seed only for tests/replay and record it in the manifest.

## EVAL-002 — RESOLVED — shipped stopping is fully conjunctive

- Component: stopping
- File: `backend/app/services/adaptive/convergence.py:217`
- Reproduction: INV-09 in the release-spec test file.
- Expected: normal convergence requires minimum observations, precision, P(band), direct coverage, modality blueprint, and difficulty corroboration.
- Actual after fix: P(band) is enabled and conjunctive by default and vetoes precision/stable-band convergence. INV-09 passes.
- Measurement impact: a narrow posterior near a band boundary can certify a band the model says is uncertain.
- Recommended fix: introduce a versioned V2 conjunctive stop after shadow calibration; do not silently change the existing endpoint.

## EVAL-003 — PARTIALLY RESOLVED — raw posterior uncertainty remains overconfident

- Component: CAT posterior/reporting
- File: `backend/evaluation/runs/codex_baseline/report/results.json`
- Reproduction: run the recorded DGP-2 smoke and analysis commands in `evaluation/README.md`.
- Expected: RMSE/mean SE 0.95-1.10 and 95% interval noncoverage 3-7%.
- Actual after fix: four 100-candidate cohorts produce reported SE-calibration ratios 0.9834-1.0808, all inside the 0.95-1.10 gate. Pooled coverage is 94.75%, and each cohort covers 93.33-97.00%. The measured AIE factors are C1=1.55, C3=1.50, and C6=1.36; the raw posterior remains overconfident.
- Measurement impact: reports and stopping overstate certainty for heterogeneous node ability.
- Recommended fix: calibrate a multidimensional/hierarchical model or a preregistered reporting-only widening factor on independent data, then revalidate stopping separately.

## EVAL-004 — RESOLVED — modality blueprint is a convergence gate

- Component: selection/stopping
- File: `backend/app/services/orchestrator/orchestrator.py`
- Reproduction: fresh DGP-2 smoke; metric `modality_blueprint_compliance=0.96333`.
- Expected: at least one code and one voice response per main in >=99% of units.
- Actual after fix: modality credit is main-specific, attainable minimums veto convergence, and the paired post-fix smoke reaches 300/300 (100%).
- Measurement impact: some reported competencies are certified without the intended modality corroboration.
- Recommended fix: make unmet modality minima an explicit convergence veto and add counterfactual tests for time pressure/bank exhaustion.

## EVAL-005 — RESOLVED — TLS verification restored for live assessment traffic

- Component: Streamlit/live voice transport
- File: `streamlit/main.py:1222`, `streamlit/main.py:1274`, `streamlit/main.py:1284`
- Reproduction: `bandit -r backend/app services streamlit`.
- Expected: certificate verification enabled by default with an explicit, local-only development override.
- Actual after fix: all three live-helper calls use httpx certificate verification defaults; Bandit reports zero high-severity findings.
- Measurement impact: candidate audio, session identifiers, and room responses can be intercepted or altered.
- Recommended fix: use the default trust store or a configured CA bundle; refuse insecure public URLs.

## EVAL-006 — HIGH — required grader, report, and fairness evidence does not exist

- Component: AI grading/product validation
- File: evaluation artifacts
- Reproduction: inspect `backend/evaluation/judged`; no 250+ adjudicated gold set or subgroup dataset exists.
- Expected: QWK, MAE, bias, confidence calibration, report faithfulness, injection invariance, and subgroup metrics on pre-registered data.
- Actual: a four-canary judged artifact exists and passes, but its own report says kappa is not computable and all judged results are descriptive.
- Measurement impact: voice/open scoring, report faithfulness, and fairness cannot support production use.
- Recommended fix: collect/adjudicate the gold set, lock prompts/models, run deterministic statistics first, then the semantic judge suites.

## EVAL-007 — MEDIUM — sub-competency precision is structurally unreachable

- Component: AIE bank
- File: `backend/eval-results/bank_floor.json`
- Reproduction: `python -m evaluation.bank_floor` (existing recorded result).
- Expected: every claimed node can reach the configured precision under realistic supply.
- Actual: 16/33 AIE sub-nodes cannot reach SE 0.55 at theta zero; at least 28 optimally targeted items are needed to close all gaps.
- Measurement impact: node-level precision claims are unavailable even though main-level precision remains reachable.
- Recommended fix: add calibrated items at the named bottlenecks before introducing node posteriors or node-level certification.

## EVAL-008 — MEDIUM — lint gate is not clean; type gate resolved

- Component: repository quality
- File: multiple
- Reproduction: Ruff reports 493 findings; mypy reports 21 backend errors in 13 files.
- Expected: release static gates pass or have a reviewed, scoped baseline.
- Actual after fix: mypy reports zero errors across 113 application and evaluation files. The application and changed boundary-test scope is Ruff-clean when `EXE002` is excluded. Sixty-seven genuine auxiliary-tooling findings remain; the NTFS mount adds 164 false executable-bit findings even though tracked modes are 100644.
- Measurement impact: raises regression risk at modality and reporting boundaries despite green runtime tests.
- Recommended fix: configure module roots, fix correctness-relevant errors first, and baseline non-semantic formatting separately.

## EVAL-009 — MEDIUM — product boundary coverage is inadequate

- Component: HTTP/live voice
- File: coverage report
- Reproduction: `coverage run --source=app,evaluation -m pytest && coverage report -m`.
- Expected: production API, persistence, and live voice failure paths receive direct coverage.
- Actual after fix: 63% under `--source=app,evaluation`; `app/main.py` is 65%, `voice/session.py` 94%, realtime transport 73%, and realtime room 72%. Direct tests cover async CAT flow, candidate payload filtering, stale concurrent submissions, registry capacity/deletion, missing rooms, handshake timeout, writer failure, provider close, opening failure, receive/send failure, and terminal packaging. The Streamlit Live bridge remains 36%, with no real-gateway or load evidence.
- Measurement impact: end-to-end outage, concurrency, and modality fallback behavior remains weakly evidenced.
- Recommended fix: add real-gateway protocol tests, then load and persistence-failure injection.

## EVAL-010 — MEDIUM — release-scale selector and stress evaluation is absent

- Component: evaluation completeness
- File: `backend/evaluation`
- Reproduction: no current artifacts for all DGP-0..7 scenarios or required selector arms.
- Expected: paired 4,000-10,000-candidate release runs, pathological sessions, selector regret/exposure, and full duration tails.
- Actual: this audit produced four 100-candidate DGP/persona smokes and reused historical propagation studies; DGP-0..7 and selector arms remain absent.
- Measurement impact: smaller effects, tails, bank degradation, time heterogeneity, and calibration-error sensitivity are not estimable.
- Recommended fix: complete nightly runs first, then promote the frozen design to release scale after blockers are fixed.

## EVAL-011 — HIGH — exact-band accuracy remains below the harness quality bar

- Component: measurement/reporting
- File: `backend/evaluation/runs/codex_cycle4_fixed/*/report/results.json`
- Reproduction: run the paired post-fix DGP-2 command recorded in `evaluation/README.md`.
- Expected: at least 80% overall exact-band accuracy and at least 70% in every evaluable band under the repository's absolute gates.
- Actual: exact accuracy is 61.00-64.33% across four DGP/persona cohorts; worst-band accuracy is 41.67-50.69%. The product marks every band provisional because the independent certification setting defaults off.
- Measurement impact: uncertainty is substantially better calibrated, but the reported band is still too often wrong for production decisions; no operational decision is certified.
- Recommended fix: investigate multidimensional estimation and bank targeting jointly; do not raise the question cap without rechecking the 90-minute duration gate.

## EVAL-012 — RESOLVED — candidate API exposed code reference solutions

- Component: HTTP CAT code-item payload
- File: `backend/app/main.py`
- Reproduction: inspect `_ui_item` before remediation; `starter_code` was populated from `reference_solution`.
- Expected: the candidate receives only the authored incomplete `starter_code` scaffold.
- Actual after fix: the API reads only `starter_code`; an end-to-end boundary test asserts that `reference_solution` and its contents never appear in the candidate payload.
- Measurement impact: exposing the answer key invalidates every code score and allows trivial perfect submissions.

## EVAL-013 — RESOLVED — analyzer silently selected the wrong persona cohort

- Component: evaluation reproducibility
- File: `backend/evaluation/analyse.py`, `backend/evaluation/run_arm.py`
- Reproduction: place P01, P02, and P09 files for DGP-2 in one cohort directory; the former dict comprehension retained only the last filename.
- Expected: a run is tied to one exact cohort identity and cannot be resumed or analyzed against another.
- Actual after fix: manifests record cohort filename, SHA-256, and persona; resume/output collisions are rejected; analysis rejects duplicate DGP identities and accepts an exact cohort JSON.
- Measurement impact: node-safety truth and persona claims could be silently attributed to the wrong generated population.

## EVAL-014 — RESOLVED — stale queue and scheduler priority broke time/coverage gates

- Component: orchestrator queue and variable scheduler
- File: `backend/app/services/orchestrator/orchestrator.py`
- Reproduction: DGP-2/P02 `sim-01780` retained a 15-minute item selected earlier and ended at 93 minutes; DGP-3/P09 `sim-01840` let a redundant five-minute precision probe consume the clock while a 75-second C6.5 coverage item waited.
- Expected: every queued promise remains affordable at presentation, and a ready hard-coverage move precedes redundant precision.
- Actual after fix: pending items are revalidated against the current clock, invalidated variables bypass posterior-only refill restrictions, and the variable scheduler prioritizes queued items that close required graph coverage. Across 400 rerun sessions, direct coverage and modality compliance are 100% and the >90-minute rate is 0%.
- Measurement impact: removes invalid duration tails and graph-coverage waivers caused by scheduling rather than bank supply.

## EVAL-015 — RESOLVED — clean realtime provider close stranded the WebSocket

- Component: realtime voice room
- File: `backend/app/services/voice_live/realtime_room.py`
- Reproduction: emit a provider `closed` event after captured transcript; the receiver returned while the room stayed live and `events()` waited forever on an empty queue.
- Expected: a clean transport close emits a terminal package and preserves captured candidate speech.
- Actual after fix: the room marks itself closed, finalizes captured turns, and queues `done`; fake-gateway regression coverage verifies the complete terminal path.
- Measurement impact: candidates no longer lose a captured voice answer to an indefinitely loading room.

## EVAL-016 — RESOLVED — realtime failures leaked transports and background tasks

- Component: LiteLLM realtime transport and Live room
- File: `backend/app/services/voice_live/litellm_realtime.py`, `backend/app/services/voice_live/realtime_room.py`
- Reproduction: timeout the handshake, fail the writer/opening prompt, raise from provider receive, or fail audio append.
- Expected: every terminal path closes the provider socket, cancels reader/writer tasks, detaches the session, and emits an explicit terminal event.
- Actual after fix: all named paths close and detach their transport; handshake polling observes the real remaining deadline; writer failures surface as error events; focused fake-provider regressions pass.
- Measurement impact: prevents hung voice attempts, leaked sockets, and answers stranded behind a browser spinner.

## EVAL-017 — RESOLVED — concurrent CAT answers could race the same presentation

- Component: HTTP CAT lifecycle
- File: `backend/app/main.py`
- Reproduction: submit two answers concurrently for one presented item.
- Expected: at most one response advances the posterior; a request that waited behind it cannot be applied to a new same-modality item.
- Actual after fix: a per-session lock serializes grading, the pre-wait item identity is revalidated inside the lock, and the stale request receives HTTP 409.
- Measurement impact: preserves one-response/one-update semantics under duplicate clicks or network retries.

## EVAL-018 — RESOLVED — candidate surfaces exposed answers and internal grading detail

- Component: CAT API and Streamlit UI
- File: `backend/app/main.py`, `streamlit/main.py`
- Reproduction: answer an MCQ/code/open item and inspect `last_graded` or the default Streamlit panels.
- Expected: an active candidate sees an acknowledgement, not answer indices, hidden-test diagnostics, rubric rationale, posterior math, queue contents, or traces.
- Actual after fix: the API returns a minimal receipt with infrastructure flags only. Streamlit defaults to candidate-safe mode; full tester instrumentation requires `STREAMLIT_TESTER_MODE=true`. Regression tests assert protected details and tester panels remain hidden.
- Measurement impact: prevents item-bank harvesting and coaching that changes later responses within the same assessment.

## EVAL-019 — PARTIALLY RESOLVED — process-local state lacked bounds and production identity

- Component: CAT/live API operations
- File: `backend/app/main.py`, `backend/app/services/voice_live/realtime_room.py`
- Reproduction: create abandoned sessions/rooms repeatedly or restart/use multiple workers.
- Expected: bounded resource use, lifecycle cleanup, durable shared state, and authenticated authorization for candidate/assessor data.
- Actual after fix: terminal state expires, the oldest terminal entries are evicted at capacity, active entries are never silently evicted, CAT exposes explicit deletion, active-only capacity fails with 503, and the debug ring is private by default. State remains in-process and the helper has no product identity/auth design.
- Measurement impact: memory exhaustion is bounded, but restart loss, cross-worker inconsistency, and unauthorized use still prevent a production-readiness verdict.
- Recommended fix: select an authenticated product identity model and external transactional session store, then run multi-worker/load/failure tests.

## EVAL-020 — MEDIUM — imported human-test banks are not psychometrically calibrated

- Component: `AIE-JR-V3` and `JAI-600` banks
- File: `backend/app/data/question_bank_AIE_JR_v3.json`, `backend/app/data/question_bank_JAI_2026_600.json`
- Reproduction: `python -m evaluation.bank_check --bank AIE-JR-V3` and repeat with `--bank JAI-600`.
- Expected: empirical `a`, `b`, and `c` parameters capable of meeting the configured SE target within the question cap, plus independent hidden tests for active coding items.
- Actual: both sources omit CAT parameters. Each imported item now records and copies an exact donor from a semantically related prior-bank main with matching modality and authored difficulty. Worst best-case SE at 12 items now passes at 0.4210 for AIE-JR-V3 and 0.3668-0.4209 for JAI-600. Sixty-four JAI-600 code items have public-only suites and are retained but inactive.
- Measurement impact: information supply now supports adaptive human pilots, but transferred parameters do not establish calibration for different item wording; scores and bands still cannot support operational decisions.
- Recommended fix: calibrate parameters on an independent response cohort and author hidden suites for the 64 quarantined code items before activating them or using either bank for score interpretation.
