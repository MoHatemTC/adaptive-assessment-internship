# `cat_engine/scripts/`

How the shipped banks were built. **Provenance, not runtime** — excluded from the wheel.

A bank in `engine/data/` is a large generated artefact. Without these, "where did this item's
`b` parameter come from" has no answer, and a bank could only ever be edited by hand.

Each takes `--check`, which re-derives the output and confirms nothing drifted, so they are
runnable assertions about the checked-in data rather than one-shot generators.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | Makes this a package so `tests/test_edge_validity.py` can import `validate_prerequisite_edges` and assert against the real thing rather than a copy. |
| `build_aie_bank.py` | Normalise the authored AI Engineer bank into the unified schema. Carries three correctness fixes, including the C6 taxonomy collision between its mcq and code items. |
| `build_unified_bank.py` | Fuse the MCQ and code banks into one multi-modality bank. Idempotent and lossless; the one thing that is not a copy is mapping code difficulty onto the theta scale. |
| `import_human_test_banks.py` | Import the two externally authored AI-engineer banks used for human testing. |
| `validate_prerequisite_edges.py` | Check that every prerequisite edge in a graph is defensible. Imported by the test suite, not just run by hand. |
| `calibrate_selection_constants.py` | Derive measured replacements for the two constants that decide the modality mix, rather than leaving them chosen. |
| `show_propagation_policy.py` | Print what each prerequisite edge is permitted to do and why — so "why is this edge inert?" does not require reading three files and doing the AND in your head. |

## Two scripts are gone

`build_open_bank.py` wrote `voice_rubrics/` and `open_grading_rubric.json`, which the engine
no longer reads — every bank now carries its rubric inline. `simulate_graph_augmented_cat.py`
was a second simulator beside [`evaluation/`](../evaluation/README.md), which has a
preregistration and published results behind it; its own docstring disclaimed it.
