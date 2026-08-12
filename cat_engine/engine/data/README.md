# `cat_engine/engine/data/`

The checked-in banks, their competency graphs, and the code rubrics.

**This directory ships in every wheel.** `tests/test_data_is_reachable.py` asserts that every
file here is named by a `BankProfile` or a module constant — a file nothing opens is a file
nobody can explain, and 960 KB of orphaned rubrics shipped for months before that test
existed.

A deployment that wants banks on a volume sets `ENGINE_DATA_DIR` rather than rebuilding.

## The five registered banks

Each is a pair. The pairing is not optional: the coverage gate asks the graph which
sub-competencies a main requires and the bank which items measure them, so a mismatched pair
reports every required node unmeasured and vetoes convergence for the whole session.

| Bank | Items | Graph | Mains |
|---|---|---|---|
| `DA` | `question_bank.json` | `competency_graph.json` | DA — Prepare and Analyze Data |
| `PY` | `question_bank_PY_20260803_083616.json` | `competency_graph_PY_20260803_083616.json` | PY — Python Engineering |
| `AIE` | `question_bank_AIE.json` | `competency_graph_AIE.json` | C1, C3, C6 — AI Engineer |
| `AIE-JR-V3` | `question_bank_AIE_JR_v3.json` | `competency_graph_AIE_JR_v3.json` | C1 — Junior AI Engineer |
| `JAI-600` | `question_bank_JAI_2026_600.json` | `competency_graph_JAI_2026_600.json` | C1–C6 |

Registered in `services/orchestrator/bank_store.py::_seed_profiles`, which is written down
rather than discovered because each row carries a claim the file cannot make about itself —
which mains it measures, and whether full coverage is reachable within the question budget.

## The rest

| File | Responsibility |
|---|---|
| `item_bank.json` | The legacy MCQ-only bank. The default of `adaptive.bank.JsonItemRepository`, still exercised by the MCQ engine's own suite. |
| `code_bank.json` | The code-question bank. The default of `code_adaptive.bank.JsonQuestionRepository`, which the live grader constructs. |
| `rubrics/loose.json`, `mid.json`, `tight.json` | Code grading strictness. `CODE_RUBRIC` selects one; it only ever reaches the model, and the deterministic path never reads it. |

## Banks written at runtime

`BANK_STORE_DIR` (default `data/banks/`, gitignored) is where uploaded banks land. A stored
bank shadows a checked-in one of the same id, and deleting it restores the seed.
