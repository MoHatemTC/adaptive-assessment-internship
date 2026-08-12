# `cat_engine/tests/`

~900 deterministic tests. **No billed calls, no infrastructure, no network.** The sandbox and
the model are stubbed everywhere: one costs money and needs egress, the other is not
deterministic, and neither is what these tests are about.

```bash
python -m pytest                                    # everything, file store
BANK_DATABASE_URL=... SESSION_DATABASE_URL=... \
  python -m pytest tests/test_sql_*.py tests/test_session_persistence.py
```

The database-backed files skip themselves without a DSN and **must run as their own
session** — those urls are read when a module is built, so setting them globally sends the
file-store tests through Postgres, where they fail against a store they never meant to use.

## The four that matter most

| File | Why |
|---|---|
| `test_parity_facade_vs_engine.py` | One seeded assessment through `AssessmentModule` and through a raw `Orchestrator`, asserted identical. If this fails, the number to distrust is not the one in the report — it is every number in `docs/evidence.md`. Runs over two banks because `DA` never administers a spoken item. |
| `test_module_boundaries.py` | The sandbox stays one import from the paths that decide what to ask and what to show. A packaging boundary when the grader was its own image; a test now. |
| `test_data_is_reachable.py` | Every file shipped in `engine/data/` is opened by some code path. Written after 960 KB of orphans shipped unnoticed. |
| `test_contract_parity.py` | The wire types that mirror engine types still agree field for field — the duplication is deliberate and policed. |

## By area

| Area | Files |
|---|---|
| **The module surface** | `test_facade.py` (the lifecycle, the candidate boundary, concurrency, scoping), `test_catalogue.py`, `test_grading.py`, `test_ingest.py`, `test_scope.py`, `test_live.py`, `test_propagation_port.py` |
| **Contracts** | `test_contracts.py`, `test_contract_parity.py` |
| **Storage** | `test_bank_store.py`, `test_bank_registry.py`, `test_session_persistence.py`, `test_sql_store_parity.py`, `test_sql_backed_registry.py` |
| **MCQ engine** | `test_irt.py`, `test_selection.py`, `test_session.py`, `test_band_probability.py`, `test_selection_calibration.py`, `test_rephrase_guard.py` — the four things a model-rewritten stem must not do |
| **Code engine** | `test_code_irt.py`, `test_code_scoring.py`, `test_code_selection.py`, `test_code_session.py`, `test_tracks_and_trials.py` |
| **Voice / open** | `test_voice_modality.py`, `test_voice_quotes.py` — the evidence-quote matcher that decides whether a model's claim counts, `test_voice_validation.py` — what the grader may send back and what is refused, `test_voice_language.py`, `test_voice_transcript_confidence.py`, `test_voice_live_turns.py`, `test_realtime_room.py`, `test_litellm_realtime_transport.py`, `test_audio_codec.py` — the PCM/WAV path between a microphone and everything downstream |
| **Competency graph** | `test_competency_graph_propagation.py`, `test_competency_graph_blocking.py`, `test_competency_graph_contradiction.py`, `test_competency_graph_coverage.py`, `test_competency_graph_report.py`, `test_propagation_policy.py`, `test_propagation_factors.py`, `test_propagation_safety_fixes.py`, `test_inference_preview.py`, `test_graph_posterior_isolation.py`, `test_shared_competencies.py`, `test_edge_validity.py` |
| **Orchestration** | `test_orchestration.py`, `test_orchestration_flow.py`, `test_main_competency_flow.py`, `test_multimodality_smoke.py`, `test_time_budget.py` |
| **Config and paths** | `test_engine_paths.py`, `test_llm_boundary.py`, `test_observability.py` |
| **Banks and graphs as data** | `test_imported_human_test_banks.py`, `test_edge_validity.py` — the shipped artefacts hold up, checked through `cat_engine/validation.py` |
| **The documentation itself** | `test_readmes_are_current.py` — every code directory has a README and it still lists what is there |
| **The public surface** | `test_public_surface.py` — every exported name resolves, and importing the package pulls in zero engine modules |
| **Configuration** | `test_config_guard.py` — what two modules in one process may and may not disagree about |
| **Regression locks** | `test_release_blocker_fixes.py` — each was a release-audit finding once, which is the strongest reason to keep them |

## Fixtures

`conftest.py` provides `stub_boundaries` (every code submission compiles and passes; no model
is ever called), `module` (a fresh `AssessmentModule` with the config guard reset around it),
`bank_items`, `orchestrator_for`, and the per-bank profile fixtures.

## Nothing here can spend money

The judged-metric layer that could — five locks and a `deepeval` pin — went with the
simulation harness. What is left stubs the sandbox and the model in `conftest.py`, so an
accidental `pytest` makes no billed call because there is no code path to one.
