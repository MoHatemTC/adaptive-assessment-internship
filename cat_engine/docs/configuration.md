# Configuration

Everything is an environment variable, read once at startup into
`cat_engine/engine/config/settings.py`. Put them in a `.env` beside whatever runs the module — it is resolved against the
working directory, not against the package.

> **Import order is load-bearing.** `Settings` is instantiated at import time, so a flag
> set after `app.config.settings` is first imported is read by nothing while looking
> applied. Anything that sets configuration programmatically must do it before that import.

## CAT policy

| variable | default | meaning |
|---|---|---|
| `CAT_SE_TARGET` | `0.55` | posterior SE at which a precision stop may fire |
| `CAT_MAX_QUESTIONS` | `12` | hard ceiling per competency |
| `CAT_PRECISION_MIN_QUESTIONS` | `6` | observation floor before a precision stop |
| `CAT_MIN_QUESTIONS` | `6` | floor before a stable-band stop |
| `CAT_STABLE_WINDOW` | `3` | consecutive items the band must hold |
| `CAT_STABILITY_SE_CEILING` | `0.55` | a stable band below this SE may stop |
| `CAT_DIFFICULTY_CORROBORATION_SLACK` | `0.25` | how near the estimate an item must be to corroborate it |
| `CAT_CONTENT_BALANCE_FLOOR` | `0.80` | information fraction within which coverage decides |
| `CAT_EXPOSURE_TOP_K` | `3` | randomesque exposure control. **1 disables it** — use 1 only for reproducibility runs, never in production |
| `CAT_BAND_PROBABILITY_STOP_ENABLED` | `false` | stop when the reported level is probably right |
| `CAT_BAND_PROBABILITY_TARGET` | `0.80` | the probability that rule requires |

> **`CAT_BAND_PROBABILITY_STOP_ENABLED` is now reachable.** It was declared, documented and
> read by nothing: `convergence.evaluate` implemented the rule but neither caller passed
> `band_probability`. Both callers now do.
>
> It is still **off**, because it is wired as an *early exit* checked **before** the
> precision rule — it fires for ~73% of competencies and ends them before the SE target.
> Measured, that is accuracy-neutral and ~5% shorter with a worse P90 tail. Make it a
> requirement *alongside* precision before enabling it.

## Orchestrator

| variable | default | meaning |
|---|---|---|
| `ORCHESTRATOR_MAX_ITEMS` | `120` | whole-assessment runaway guard, not a budget |
| `ORCHESTRATOR_TIME_LIMIT_MINUTES` | `90` | the constraint that actually binds |
| `ORCHESTRATOR_TIME_RESERVE_SECONDS` | `120` | held back for the report |
| `ORCHESTRATOR_TIME_AWARE_SELECTION_ENABLED` | `true` | rank on information per minute. **Off makes the 90-minute limit arithmetically unreachable** |
| `ORCHESTRATOR_MODALITY_MINIMUMS` | `{"code":1,"voice":1}` | per-competency modality floor, enforced as a constraint |
| `ORCHESTRATOR_SHORTLIST_SIZE` | `5` | items offered to the picking agent |
| `ORCHESTRATOR_MINIMUM_RELATIVE_UTILITY` | `0.75` | below this the picker's choice is overridden |

## Competency graph

| variable | default | meaning |
|---|---|---|
| `COMPETENCY_GRAPH_ENABLED` | `true` | master switch. Off means no graph anywhere, **including the report** |
| `GRAPH_CONVERGENCE_GATE_ENABLED` | `true` | the coverage requirement — the part that earns its cost |
| `GRAPH_COVERAGE_CRITICAL_ONLY` | `false` | overridden per bank by `BankProfile.coverage_critical_only` |
| `GRAPH_UPWARD_INFERENCE_ENABLED` | `false` | see [propagation-policy.md](propagation-policy.md) |
| `GRAPH_DESCENDANT_BLOCKING_ENABLED` | `false` | as above |
| `GRAPH_MINIMUM_FAILURES_TO_BLOCK` | `2` | consistent parent failures before a block. A bank may raise it |
| `GRAPH_FILTERING_ENABLED` | `false` | graph-derived selection penalties |
| `GRAPH_UTILITY_ENABLED` | `false` | contradiction bonus and shared-main gain |
| `GRAPH_EDGE_PREVIEW_ENABLED` | `true` | record what unvalidated edges *would* conclude. Acts on nothing |
| `GRAPH_SHADOW_MODE` | `true` | audit mirrors alongside the enforced projection |

### Propagation thresholds

| variable | default |
|---|---|
| `GRAPH_STRONG_SUCCESS_THRESHOLD` | `0.80` |
| `GRAPH_STRONG_FAILURE_THRESHOLD` | `0.20` |
| `GRAPH_MINIMUM_PROPAGATION_CONFIDENCE` | `0.80` |
| `GRAPH_DOWNWARD_BLOCK_CONFIDENCE` | `0.85` |
| `GRAPH_UPWARD_DECAY` | `0.70` |
| `GRAPH_MINIMUM_INFERRED_WEIGHT` | `0.15` |
| `GRAPH_MAXIMUM_INFERRED_WEIGHT` | `0.60` |
| `GRAPH_MAXIMUM_PROPAGATION_DEPTH` | `4` |

None of these has been calibrated against candidate data. Blocking's floor is higher than
inference's on purpose: inferring mastery a candidate lacks costs one unnecessary question,
blocking a skill they have costs them the assessment.

## Banks

| variable | default | meaning |
|---|---|---|
| `ACTIVE_BANK` | `AIE` | which registered bank a session uses when the caller names none |
| `CAT_SESSION_DUMP_DIR` | `""` | append finished states as JSONL. **Candidate data** — off by default |

## Scoping

`competency-scope`, and the orchestrator's link to it.

| variable | default | meaning |
|---|---|---|
| `COMPETENCY_SCOPE_URL` | `http://competency-scope:8085` | **Empty disables competency-scoped assessments** — `POST /assessments` with a `scope` then returns 503 rather than quietly assessing the whole bank. "You asked for three competencies and got eleven" is not a degraded mode a candidate or a report could detect |
| `SCOPE_TIMEOUT_SECONDS` | `10.0` | a scope is induced once per SESSION, not per response, so this is nowhere near the 150 ms per-response budget |

## Bank ingest

`bank-ingest` only. An author uploads questions and never a graph, so one is derived from
the items — these govern that derivation. See [bank-schema.md](bank-schema.md) for the
optional `competencies` block a bank uses to state what a derivation cannot infer.

| variable | default | meaning |
|---|---|---|
| `INGEST_API_ENABLED` | `true` | false stops uploads without stopping the service — "not taking banks today" rather than "the authoring API is down" |
| `RELATION_THRESHOLD` | `0.5` | a derived sub-to-sub relation at or above this is `PREREQUISITE`, below it `CONTRIBUTES_TO`. Derived prerequisite edges ship **inert regardless** |
| `EDGE_FLOOR` | `0.05` | below this no derived sub-to-sub edge is emitted at all |
| `MAX_UPLOAD_BYTES` | `33554432` | the largest checked-in bank is 1.8 MB; this fails an accidental upload at the door rather than in a parser |
| `MAX_RETAINED_UPLOADS` | `200` | raw bytes are kept so a rejection is reproducible, and an unbounded list of them is a memory leak with a nice name |
| `BANK_DATABASE_URL` | `""` | **Empty keeps the file store**, which is the default and the tested-by-default path. Set on both bank services to resolve banks from Postgres instead; `docker compose --profile db up` does it |

Both thresholds are **modelling decisions, not derivations**. The right values depend on how
heavily a bank's items measure more than one competency, which is a property of the content
rather than of the code — which is also why they are settings and why the derivation is
confined to one module.

## Code and voice

| variable | default | meaning |
|---|---|---|
| `CODE_APPROACH` | `B` | A objective-only, B test-anchored, C model-led |
| `CODE_RUBRIC` | `mid` | `loose` / `mid` / `tight` |
| `CODE_EXECUTION_TIMEOUT_SECONDS` | `30` | |
| `E2B_API_KEY` | — | untrusted code runs in E2B, never in-process |
| `LITELLM_BASE_URL` / `LITELLM_API_KEY` / `LITELLM_MODEL` | — | every model call routes through the proxy |

## Settings that are safe to change, and settings that are not

**Safe:** thresholds, budgets, exposure, rubric strictness.

**Not safe without re-measuring:** `CAT_SE_TARGET`, band cut points, any graph propagation
flag, `ORCHESTRATOR_TIME_AWARE_SELECTION_ENABLED`. Each changes what a reported level
*means*, and the level is used for decisions about people.

**Not safe for a bank already registered:** `RELATION_THRESHOLD` and `EDGE_FLOOR`. They are
read when a bank is ingested, so changing them does not re-derive an existing graph — it
changes what the *next* upload produces. Two banks uploaded either side of a change have
graphs built to different rules, and nothing in a report says so.
