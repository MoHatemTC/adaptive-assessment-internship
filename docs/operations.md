# Operations

## Running it

There is nothing to run. The module is imported by a host, and the host is what gets started
— see [module.md](module.md) for the embedding contract.

```bash
pip install -e .                     # or copy cat_engine/ into the host tree
python -m pytest                     # ~900 tests, no billed calls, no infrastructure
```

The database-backed paths skip themselves without a DSN and are run as their own session,
because `BANK_DATABASE_URL` is read when a module is built — set globally it sends every
file-store test through Postgres, where they fail against a store they never meant to use:

```bash
BANK_DATABASE_URL=postgresql://... SESSION_DATABASE_URL=postgresql://... \
  python -m pytest cat_engine/tests/test_sql_backed_registry.py \
                   cat_engine/tests/test_sql_store_parity.py \
                   cat_engine/tests/test_session_persistence.py
```

**The suite needs its declared dependencies.** `pytest-asyncio` and `pytest-env` are in
`requirements.txt` and are not optional — without the first the concurrency guard goes
untested and the async tests do not run; without the second, `pytest.ini`'s `env =` block
supplies nothing and the engine reports a config warning.

## Before a release: what has actually been run

```bash
python -m pytest                                    # everything, file store
BANK_DATABASE_URL=... SESSION_DATABASE_URL=... \
  python -m pytest cat_engine/tests/test_sql_*.py cat_engine/tests/test_session_*.py
```

**Two checks are worth more than the suite, and neither is in it.**

*The embed check.* Install the module into a clean virtualenv with base dependencies only,
in a directory that is not this repository, and run an assessment. Every environment a
developer tests in has every extra installed, which is exactly the environment in which a
missing-optional-dependency defect is invisible — this is how `from cat_engine import
AssessmentModule` was found to require a realtime SDK.

*The parity check.* `tests/test_parity_facade_vs_engine.py` runs one seeded assessment
through `AssessmentModule` and through a raw `Orchestrator` and asserts the two reports are
identical. It is what stops the surface drifting from the engine underneath it, and it runs
over two banks because one of them never administers a spoken item.

The harness check that used to be here — re-run an evaluation cell, diff it against a stored
result — is no longer possible on this branch: the harness and its artefacts were removed
when the repository was reduced to the engine. If a change could plausibly move a measured
number, check the harness out of git history and run it there.

## First checks when something looks wrong

```python
from cat_engine import AssessmentModule, CatConfig
from cat_engine.config import applied_fingerprint

cat = AssessmentModule(CatConfig())

applied_fingerprint()                # what this process is measuring by
[b.model_dump() for b in cat.banks()]  # which banks, which versions, seed or stored
cat.policy("AIE")                    # why is this edge inert?
cat.settings                         # store dirs, which surfaces are open
```

The fingerprint replaces the `/health` comparison across seven services. It cannot differ
between components any more — there is one process — but it can differ between a run and the
run something was measured under, which is the comparison that was always the point.

```python
# WHY IS THIS SELECTION REFUSED? `reachable: False` names the competency at fault — either
# one no active item measures, or a main requiring more sub-competencies than the budget.
manifest = cat.scope(["C1.1", "C6"], bank_id="AIE")
manifest.coverage, manifest.rejected

# WHERE ARE BANKS COMING FROM?
cat.settings.bank_database_url or cat.settings.bank_store_dir or "packaged data"
```

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

```python
receipt = cat.validate_bank("NEW", path.read_bytes())   # writes nothing
receipt = cat.upload_bank("NEW", path.read_bytes())     # writes
receipt.status, receipt.error, receipt.derived_graph
```

Validate first. The receipt carries the **derived graph**, which is the part the author did
not upload and cannot otherwise see, and the same call writes nothing.

**The refusal you will actually hit is `coverage_unreachable`.** Every derived
sub-competency is critical, so a main declaring more of them than `CAT_MAX_QUESTIONS - 2`
can never satisfy the coverage gate. The fix is in the bank file, not in configuration: add
a `competencies` block naming which sub-competencies are critical. See
[bank-schema.md](bank-schema.md) §1. Raising `CAT_MAX_QUESTIONS` also works and is almost
always the wrong lever — it changes every session, not this bank.

`INGEST_API_ENABLED=false` refuses uploads without disabling anything else — reads keep
working. `ADMIN_API_ENABLED=false` closes `delete_bank` the same way. There is one write
path now (`upload_bank`); the second one, `bank-registry`'s, went with the services.

## Moving banks into Postgres

Off by default. `BANK_DATABASE_URL` empty keeps the file store, which is what the test suite
runs against.

```python
cat = AssessmentModule(CatConfig(bank_database_url="postgresql://..."))
```

The checked-in banks are loaded at construction, idempotently, and land with **the same content
hash the file store computes**. That is the thing to verify before believing anything else,
because a mismatch has no other symptom — nothing errors, and every cached parameter set in
the fleet silently belongs to a bank nobody registered:

```python
[(b.bank_id, b.version, b.source) for b in cat.banks()]
```

Compare against the same call with no DSN configured. They must be identical.
`cat_engine/tests/test_sql_store_parity.py` asserts exactly this for all five banks and is
the check to run in CI.

To roll back: drop `bank_database_url` and rebuild the module. Banks written while the
database was in force stay in the database, and the file store's are unaffected — neither
store writes to the other.

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
