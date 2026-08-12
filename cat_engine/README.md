# `cat_engine/`

The whole module. A host project imports this folder; everything it needs is inside.

There is no web framework here and no port. The host owns transport, this owns the
measurement — see [docs/module.md](../docs/module.md) for the embedding contract and
[docs/adr/0004](../docs/adr/0004-from-services-to-a-module.md) for why it is one folder.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | The public surface: `AssessmentModule`, `SyncAssessmentModule`, `CatConfig`, and the error types. Re-exports lazily, so importing the package does not drag in numpy and 600 KB of banks before a host has asked for them. |
| `facade.py` | `AssessmentModule` — every method a host calls. The lifecycle, the catalogue, scoping, bank writes, grading primitives and the gated author view. Also `SyncAssessmentModule`, which wraps each coroutine for a host that is not async. |
| `config.py` | `CatConfig`: one flat surface a host configures, routed to three targets — engine measurement policy, module topology, voice settings. Holds the fingerprint guard that refuses a second module with different measurement policy. |
| `settings.py` | `ModuleSettings`: topology and surfaces, as opposed to measurement policy. Where banks live, which write paths are open, upload ceilings. Deliberately **not** in the engine's fingerprint — these change where things are read from, not what a number means. |
| `errors.py` | `CatError` and its subclasses. Every exception carries `code` (unchanged from when this was an HTTP API) and `status_code`, so a host maps them onto responses in one line. |
| `wiring.py` | Builds an `Orchestrator` out of in-process parts, and caches one per `(bank, version, scope)`. This is the file that replaced four HTTP clients; nothing in the engine's loop changed for either arrangement. |
| `catalogue.py` | Reading banks: what is registered, what it measures, what it declares. Two views of an item with two return types — the ranking view has no payload field to leak a question through. |
| `grading.py` | `Grader`: one response to `GradedOutcome[]`, per modality. Owns the only path that reaches the sandbox, plus transcription and trial runs, which grade nothing. |
| `projection.py` | Engine objects to what a host receives. Both item views and the candidate boundary live here together, because the difference between them is easier to keep right when you can see both. |
| `diagnostics.py` | The author view: posteriors, shortlists, the criterion phase, which item the engine would have picked. **Not candidate-safe**; the facade refuses it unless enabled. |
| `validation.py` | Is the shipped DATA sound? Whether a bank can reach its precision target, and whether a graph's prerequisite edges survive contact with a real session corpus. |

## Directories

| Directory | Responsibility |
|---|---|
| [`engine/`](engine/README.md) | The psychometrics. 3PL core, posterior, selection, stopping, the competency graph, and the banks. Everything above is an adapter over this. |
| [`contracts/`](contracts/README.md) | The DTOs a host receives. Deliberately independent of `engine/` — `BankItemRef` omits the payload `BankItem` carries, and that omission is the boundary. |
| [`scope/`](scope/README.md) | A competency selection to a sub-graph and an item allowlist. Cannot move a posterior. |
| [`ingest/`](ingest/README.md) | One uploaded file to a registered bank, with the competency graph derived from the questions. The only write path. |
| [`stores/`](stores/README.md) | Where live assessments and registered banks are kept. Memory by default, Postgres when configured, the host's own when it has one. |
| [`live/`](live/README.md) | Realtime interview rooms as a Python API, plus the browser client for them. The host writes the socket. |
| [`scripts/`](scripts/README.md) | One calibration script the engine names by path. Excluded from the wheel. |
| [`tests/`](tests/README.md) | ~900 deterministic tests. No billed calls, no infrastructure. |

## The one rule everything else rests on

**Only a directly observed response may move a posterior.** The competency graph changes
what is asked and when the test may stop. It never changes what is estimated —
`InferredSignalDTO` has no `score` and no `weight` to carry one.
