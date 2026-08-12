# `cat_engine/scripts/`

One script. It is here because the engine names it.

`engine/services/orchestrator/selection_calibration.py` reads
`data/selection_calibration.json` and documents this file as what writes it — so removing it
would leave the engine with a code path for a file nothing could produce.

| File | Responsibility |
|---|---|
| `__init__.py` | Package marker, so the module can be imported rather than only executed. |
| `calibrate_selection_constants.py` | Derive measured replacements for the two constants that decide the modality mix — expected seconds per item, and expected evidence weight — from a real session corpus. Both shipped as opinions; between them they set which modality wins a ranking, and therefore how long a session runs and what it is made of. |

Excluded from the wheel: a host installs the module, not the tooling that calibrated it.

## What used to be here

Four bank-authoring scripts (`build_aie_bank`, `build_unified_bank`,
`import_human_test_banks`, `build_open_bank`) and two operator tools
(`show_propagation_policy`, `simulate_graph_augmented_cat`). The banks they built are
shipped in `engine/data/`; the propagation policy is on `catalogue.policy()`; the simulator
was superseded and then removed with the harness. They are in git history if a bank ever
needs rebuilding.

`validate_prerequisite_edges.py` did not go — its pure core moved to
[`cat_engine/validation.py`](../validation.py), because checking whether the shipped graphs
hold up is a question about the engine's data rather than a build step.
