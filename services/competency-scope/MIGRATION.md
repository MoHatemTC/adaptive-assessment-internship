# competency-scope

**Status: implemented.** New service; nothing moved out of the monolith to build it.

## What it owns

Turning a selection of competencies into (a) the induced sub-graph, (b) the set of items
that measure it, and (c) whether the result can be assessed within the question budget.

| method | path | notes |
|---|---|---|
| `POST` | `/scopes` | a `ScopeRequest` becomes a `ScopeManifest`. `ETag` is the scope hash |
| `POST` | `/scopes/graph` | the same scope, drawn as mermaid |
| `GET` | `/health`, `/config` | the shared operator surface |

## What did NOT move, and why

**Nothing from the engine.** A scope is applied by wrapping two Protocols the orchestrator
already depends on — `UnifiedBankRepository` and the graph service — so no engine module
needed changing and none was copied here. See `docs/architecture-proposal.md` §2.11.

**The items.** This service returns ids. `bank-registry` serves items through two endpoints
with deliberately different authorisation, and a second item-serving path would give one
caller's authorisation to another. The orchestrator already holds the parameter pool for the
bank version, so an allowlist is a set intersection rather than a fetch.

**Session state.** There is none. The manifest is a pure function of the bank version and
the normalised selection, so `scope_id` is a hash of its own inputs and a client's preview
and the orchestrator's session-start rebuild agree without a shared cache.

## The engine slice it imports

`app.config` only, for `cat_max_questions` — the question budget the reachability check is
against. It touches no schema, no graph module and no orchestrator module: everything it
needs about a bank arrives as a DTO. Asserted by
`services/tests/test_services.py::TestEachServiceImportsOnlyTheEngineSliceItOwns`.

## Checklist

- [x] `POST /scopes` induces the sub-graph, strictly, with dangling prerequisites reported
- [x] the item allowlist excludes retired items
- [x] `CONTRIBUTES_TO` renormalised over retained children, original kept beside it
- [x] mains implied by a shared node are opened and flagged
- [x] reachability against `CAT_MAX_QUESTIONS`, with the offending mains named
- [x] `POST /scopes/graph` renders the scope
- [x] a scope naming every competency reproduces an unscoped session
- [x] port and compose entry agree
