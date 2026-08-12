# `cat_engine/evaluation/invariants/`

INV-01..INV-06 as executable tests — the properties that must hold whatever the arm.

These are separate from `tests/` because they are part of the **study apparatus**: they are
pre-registered claims about the engine that the experiment depends on, and they are cited by
`docs/preregistration/`.

## Files

| File | Responsibility |
|---|---|
| `test_c_shipped_invariants.py` | The shipped-configuration invariants, including posterior isolation — that the graph layer cannot move an estimate under any arm. |

## Why posterior isolation is here rather than only in `tests/`

Every claim in the study rests on it. If the graph could move a posterior, the comparison
between arms would be measuring two different instruments rather than one instrument under
two configurations, and no gate in `gates.py` would mean anything.
