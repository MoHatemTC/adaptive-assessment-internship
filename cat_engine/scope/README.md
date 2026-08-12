# `cat_engine/scope/`

A selection of competencies becomes a sub-graph and an item allowlist.

## What this is for

An assessment used to be a whole bank. `begin()` accepts `target_variables`, but the engine
checks those against `bank.variables()`, which returns MAIN competencies only — so
`["C1", "C3"]` worked and `["C1.1", "C1.4"]` was refused as "no bank coverage". Everything
here exists to make the second expressible, **without the engine learning a new concept**.

That is why a scope is two decorators over seams the orchestrator already depended on,
rather than a change to selection: see `bank.py`.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | `build_manifest`: fetch what the bank declares and induce the scope over it. Refuses a bank with no graph rather than silently widening to the whole bank. |
| `scoping.py` | The induction itself — normalising a selection, resolving shared nodes, computing retained weight, and deciding whether the result is assessable within the question budget. The real logic. |
| `bank.py` | `ScopedBank` and `scoped_graph_service`: the two decorators that apply a scope. `shortlist` is filtered; `get` deliberately is not, and propagation keeps the full graph. |
| `render.py` | The induced sub-graph drawn as mermaid, excluded siblings greyed rather than omitted. |

## Three properties worth knowing before offering this in a UI

**It cannot move a posterior.** There is no score, no weight and no theta in a
`ScopeManifest`. A scope changes which questions may be asked and what coverage requires —
both visible in the report — and cannot change any number.

**A selection widens.** Sub-competencies can be shared: selecting one main whole may
necessarily measure something evidencing another, so that other main is opened too and
marked `implied` on the report.

**The manifest is a pure function** of the bank version and the normalised selection, so
`scope_id` is a hash of its own inputs. A host previews a scope, `begin()` rebuilds it, and
the two agree by construction rather than through a shared cache.
