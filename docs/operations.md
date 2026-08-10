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

## First checks when something looks wrong

```bash
# Do the services agree about what they are measuring? Two different fingerprints means
# two services are not running the same assessment — scores computed one way, stopping
# rule assuming another, and nothing failing.
for p in 8080 8081 8082 8083 8765; do curl -s localhost:$p/health | jq -c \
  '{service, engine_config_fingerprint, contract_schema_version}'; done

curl -s localhost:8081/banks | jq                      # which banks, which versions
curl -s localhost:8081/banks/AIE/policy | jq           # why is this edge inert?
curl -s localhost:8080/health | jq .detail             # sessions, dependencies, propagation mode
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
