# Operations

## Running it

```bash
cd deploy && cp .env.example .env    # fill LITELLM_* and E2B_API_KEY
docker compose up --build
curl localhost:8080/health
```

Without Docker: `./scripts/run_services.sh` brings up all five against a local venv.

```bash
cd backend  && PYTHONPATH=. pytest   # the engine and the study harness
cd services && python -m pytest      # the services, the seams and the contracts
```

## Before a release: what has actually been run

```bash
cd backend  && PYTHONPATH=. pytest        # 691, the engine and the study harness
cd services && python -m pytest           # the seams, the contracts, every service

cd deploy && docker compose build && docker compose up -d && python smoke.py
cd deploy && docker compose --profile db up -d --build && python smoke.py
```

**Run the two smoke tests.** They are the only thing that exercises a container, and each
found a defect no unit test could. The file-backed run caught endpoints that had moved; the
database run caught `GET /banks` timing out on a cold cache, because listing a catalogue was
materialising every bank's bytes out of Postgres.

**Both suites need their declared dependencies.** `pytest-asyncio` and `pytest-env` are in
`backend/requirements.txt` and are not optional — without the first, the concurrency guard
goes untested and 70 tests do not run; without the second, `pytest.ini`'s `env =` block
supplies nothing and the engine reports a config warning.

## First checks when something looks wrong

```bash
# Do the services agree about what they are measuring? Two different fingerprints means
# two services are not running the same assessment — scores computed one way, stopping
# rule assuming another, and nothing failing.
for p in 8080 8081 8082 8083 8084 8085 8765; do curl -s localhost:$p/health | jq -c \
  '{service, engine_config_fingerprint, contract_schema_version}'; done

curl -s localhost:8081/banks | jq                      # which banks, which versions
curl -s localhost:8081/banks/AIE/policy | jq           # why is this edge inert?
curl -s localhost:8080/health | jq .detail             # sessions, dependencies, propagation mode
curl -s localhost:8084/health | jq .detail             # store dir, uploads retained, ingest on?
curl -s localhost:8085/health | jq .detail             # the question budget scopes are checked against
```

Two more, when the symptom involves a bank or a scope:

```bash
# WHERE ARE BANKS COMING FROM? `store_dir` naming a cache directory means Postgres is in
# force; naming the volume means the file store is. Both bank services must agree — one
# reading files while the other writes rows is a bank that never appears.
curl -s localhost:8081/config | jq '.settings.bank_database_url, .extra'
curl -s localhost:8084/config | jq '.settings.bank_database_url, .extra'

# WHY IS THIS SELECTION REFUSED? `reachable: false` names the competency at fault — either
# one no active item measures, or a main requiring more sub-competencies than the budget.
curl -s localhost:8085/scopes -H 'content-type: application/json' \
  -d '{"bank_id":"AIE","selected":["C1.1","C6"]}' | jq '.coverage, .rejected'
```

`GET /config` on any service prints its effective configuration with credentials removed.
It is the first thing to read when a bank list comes back empty — `engine_data_dir` and
`bank_store_dir` are in it.

## Known failures

None. `docs/operations.md` previously listed
`test_session.py::test_bank_can_support_the_configured_precision_target` as a known failure
for "Agentic AI & Orchestration"; it does not fail on this branch, and the note was stale.

## Enabling inference or blocking

**Do not do this without working through the list.** [evidence.md](evidence.md) has the
numbers; the summary is 22.4% wrong inference and 8.7% false blocking on edges that were
*correct by construction*, and a structural floor on blocking that no configuration beats.

### Before you start

- [ ] a session corpus exists — `CAT_SESSION_DUMP_DIR` has been on long enough to accumulate one
- [ ] `scripts/validate_prerequisite_edges.py` has run against it
- [ ] you have read why the offline check **cannot** validate an edge ([evidence.md](evidence.md))
- [ ] you have an experimental design that can: serve the child to candidates who failed the parent, per `C-DAG-01`

### Per edge, in order

1. Run the experiment for that specific edge. Not the edge set — the edge.
2. Set `metadata.validation_status` to `validated` and record `n_parent_failures` and
   `p_pass_child_given_fail_parent` beside it.
3. Set `allow_upward_inference` and/or `allow_downward_blocking` on **that edge only**.
4. Leave the bank policy and the deployment flags **off**. Confirm with
   `show_propagation_policy` that the edge now reads `bank policy: ...` as its blocker —
   that proves levels 2 and 3 are wired as you think.
5. Enable at the bank level. Run in shadow: `GRAPH_SHADOW_MODE=true` records what would
   have happened without acting on it.
6. Read the shadow record. `graph_preview_refuted_nodes` lists beliefs the session went on
   to **disprove** — a non-empty list on an edge you just validated means stop.
7. Only then the deployment flag.

### Rolling back

`GRAPH_UPWARD_INFERENCE_ENABLED=false` / `GRAPH_DESCENDANT_BLOCKING_ENABLED=false` takes
effect at the next process start. It stops **enforcement**, not computation — the audit
mirror keeps recording, which is what you want while diagnosing.

`COMPETENCY_GRAPH_ENABLED=false` disables the layer entirely, **including the graph block in
the report**. Reach for it only if the graph itself is the fault.

Neither corrupts state: a graph failure is caught and the delta discarded, so a graph
problem costs the candidate nothing.

## Uploading a bank

An author uploads **one file: the questions**. The competency graph is derived from them.

```bash
curl -s -X POST 'localhost:8084/uploads/validate?bank_id=NEW' -F file=@bank.json | jq
curl -s -X POST 'localhost:8084/uploads?bank_id=NEW'          -F file=@bank.json | jq
```

Validate first. The receipt carries the **derived graph**, which is the part the author did
not upload and cannot otherwise see, and the same call writes nothing.

**The refusal you will actually hit is `coverage_unreachable`.** Every derived
sub-competency is critical, so a main declaring more of them than `CAT_MAX_QUESTIONS - 2`
can never satisfy the coverage gate. The fix is in the bank file, not in configuration: add
a `competencies` block naming which sub-competencies are critical. See
[bank-schema.md](bank-schema.md) §1. Raising `CAT_MAX_QUESTIONS` also works and is almost
always the wrong lever — it changes every session, not this bank.

`INGEST_API_ENABLED=false` stops uploads without stopping the service. It does **not** stop
`bank-registry`'s own write path, which is gated separately by `ADMIN_API_ENABLED`; there
are two write paths today and turning off one leaves the other open.

## Moving banks into Postgres

Off by default. `BANK_DATABASE_URL` empty keeps the file store, which is what the test suite
runs against.

```bash
cd deploy && docker compose --profile db up --build
```

The checked-in banks are loaded on boot, idempotently, and land with **the same content
hash the file store computes**. That is the thing to verify before believing anything else,
because a mismatch has no other symptom — nothing errors, and every cached parameter set in
the fleet silently belongs to a bank nobody registered:

```bash
curl -s localhost:8081/banks | jq -c '.[] | {bank_id, version, source}'
```

Compare against the same call with `BANK_DATABASE_URL` unset. They must be identical.
`services/tests/test_sql_store_parity.py` asserts exactly this for all five banks and is
the check to run in CI.

To roll back: unset `BANK_DATABASE_URL` and restart. Banks written while the database was
in force stay in the database, and the file store's are unaffected — neither store writes
to the other.

## Adding a bank

See [bank-authoring.md](bank-authoring.md). A new bank is two files and a registry row; no
engine code changes.

## What to watch

| signal | where | means |
|---|---|---|
| `stop_reason` distribution | report | a rise in `question_budget` or `graph_gates_waived` means the coverage requirement is unreachable |
| `graph_gates_waived` | report | measurement converged, the graph refused to certify it. Distinct from running out of questions **on purpose** |
| `graph_preview_refuted_nodes` | report | an authored edge just got disproved |
| `aberrant_responses` | report | responses the posterior did not expect. Report-only; nothing reads it and changes an estimate |
| P90 duration | telemetry | the 90-minute cap is the constraint that binds, not the item budget |
| exposure / Gini | `bank.parity_report()` | with `CAT_EXPOSURE_TOP_K=1` selection is maximally concentrated. **Production must not run at 1** |

## Candidate protection

Decision accuracy sits around 65% on five bands and every absolute quality bar except
reliability and duration is currently failed. Before this is used for a real decision:

- **report a band range**, not a point band — within-one accuracy is ~99%
- state that a level is advisory during any validation phase
- provide a human-review threshold and an appeal route
- do not describe `certainty_pct` as a probability that the level is right. It is a
  monotone remap of the posterior SD; `p_reported_band` is the number people assume they
  are being given

## Security

- Untrusted code runs in **E2B only**, never in-process
- Every model call routes through the LiteLLM proxy; no direct provider calls
- `CAT_SESSION_DUMP_DIR` writes **candidate data**. Off by default; treat as personal data
- Langfuse tracing is off unless both keys are set, and is never load-bearing
