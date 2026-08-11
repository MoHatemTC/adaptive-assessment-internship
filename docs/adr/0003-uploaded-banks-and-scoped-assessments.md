# ADR-0003 — Uploaded banks, derived graphs, and assessments scoped to competencies

**Status:** accepted, implemented.
**Date:** 2026-08-11.
**Supersedes:** nothing. **Amends:** [ADR-0001](0001-service-boundaries.md) §"What is
deliberately NOT split" — there are now seven services, not four.
**Superseded by:** nothing.

---

## Context

Two capabilities were asked for, and neither existed:

1. **Upload a bank.** Banks became content in ADR-0002, but registering one still meant
   POSTing a JSON body containing both the items and a hand-authored competency graph. An
   author has questions. They do not have a graph and are not going to write one.
2. **Assess some competencies rather than a whole bank.** `POST /assessments` accepted
   `target_variables`, but `Orchestrator.begin` checks them against `bank.variables()`,
   which returns **main competencies only**. `["C1", "C3"]` worked; `["C1.1", "C1.4"]` was
   refused as `no bank coverage`. Sub-competencies existed in the graph, in each item's
   `measures`, and in the coverage gate — but never as something a caller could ask for.

Both are read-side and write-side changes to the same thing: what a bank *is* and which
part of one an assessment covers.

---

## Decision

**Two services, both small, both unable to move a posterior.**

| Service | Owns | Port | Egress |
|---|---|---|---|
| `bank-ingest` | one uploaded file → a registered bank, graph derived | 8084 | the bank store |
| `competency-scope` | a selection of competencies → an induced sub-graph and an item allowlist | 8085 | `bank-registry` |

And a third thing that is not a service: **`adaptive-store`**, the bank store backed by
Postgres, installed only into the two images that touch a bank.

---

## The decisions, and what each one prevents

### 1. A scope is applied by decorating two Protocols, not by changing the engine

`Orchestrator` already takes a `UnifiedBankRepository` and a graph service. Both were
already interfaces — that is what let the monolith become services at all. So a scope is
two decorators in the adapter layer:

- **`ScopedBank.shortlist()`** — the single function every candidate pool in `fill_queue`
  comes from. Ranking, exposure control, the picker and the time filter are downstream of
  it and need to know nothing.
- **`scoped_graph_service()`** — the induced sub-graph. `sub_nodes_for_main` enumerates the
  nodes of *the graph it was handed*, so the coverage requirement is correct by
  construction at all four of its call sites, with no argument threaded through any of them.

**No engine module changed.** The alternative — an explicit `required_nodes` parameter —
would have touched four call sites and created a second way to answer "what does this main
require", which is exactly the drift ADR-0002 argues against.

### 2. A sub-competency may never become a target variable

`rollup_outcomes` folds every graded outcome to `variable.split(".")[0]` and **drops it**
when that main is not in the session. A session begun on `["C1.1"]` would grade responses,
propagate them to the graph, and then discard every one before the posterior — running to
the question cap with the standard error exactly where it started. Nothing raises, nothing
logs, and the report looks like a candidate who answered badly.

So a selection **opens mains**, derived from the selected nodes, and the sub-competency part
is expressed as a narrower item pool and a narrower coverage requirement. This is not a
workaround: ability is estimated per main, and a target variable is by definition a thing
that has an estimate.

**Consequence, stated because it is real:** a partially covered main is estimated from a
corner of itself, which is what the coverage gate exists to prevent when it happens by
accident. Legitimate on purpose, so the report carries `partial_mains` and
`retained_weight_by_main` and a partial main must not be compared with a whole-bank one.

### 3. `competency-scope` returns item ids, never items

ADR-0001's second boundary is that parameters are not payloads and that one service serves
items. A scope service returning questions would be a second item-serving path with
different authorisation and a third answer to "what is in this bank".

It is also unnecessary: the orchestrator already holds the whole parameter pool for the bank
version, so an id allowlist costs a set intersection and no hop.

### 4. The selection travels, not a scope id

`scope_id` is a hash of its inputs, and a hash cannot be inverted, so a stateless service
cannot answer `GET /scopes/{id}`. The alternatives are both worse: caching manifests makes a
deterministic, horizontally scalable service stateful and bound to a replica; accepting an
id from a client means applying an item allowlist nobody in that process derived.

So the orchestrator rebuilds the manifest from the selection. A client that previewed the
scope gets the identical `scope_id` back, because the manifest is a pure function of
(bank version, normalised selection).

### 5. The competency graph of an uploaded bank is DERIVED

The author uploads one file: the questions. Nodes come from each item's
`measures[].variable`, titles from its own `sub_competency` label by majority vote, and edge
weights from **item co-measurement**:

```text
sub → main   |items measuring sub| / |items measuring main|   (then normalised per main)
sub → sub    |items measuring both| / |items measuring the source|
```

This is a **modelling decision, not a derivation from first principles**, and it is confined
to one module so replacing it touches one file. Both thresholds are settings.

Two properties are load-bearing. The weights are **un-normalised** and land in [0, 1], which
is what lets a threshold discriminate at all — the authored `CONTRIBUTES_TO` weights sum to
1.0 per main, so no edge on any checked-in bank exceeds `0.1429` and a 0.5 threshold against
those could never fire once. And **only the stronger direction of each pair survives**:
co-measurement shares a numerator both ways, so `A→B` and `B→A` can both clear the
threshold, and two prerequisite edges between one pair is a cycle the validator refuses —
every upload of a multi-measuring bank would have failed for a reason no author could act on.

**Every derived prerequisite edge ships inert**, unvalidated, with inference and blocking
off at the bank level. An edge from co-measurement has strictly less authority than one an
expert authored, and the authored ones were themselves measured wrong 22.4% of the time
against a 3% gate.

### 6. The bank file may declare its own competencies

The one thing questions cannot imply is **which sub-competencies are critical**. With no
other information a derivation must mark them all critical, and a main with more
sub-competencies than `CAT_MAX_QUESTIONS − 2` can then never satisfy the coverage gate.
`question_bank_AIE.json` is such a bank: **the flagship checked-in bank could not be
uploaded through the service built to upload banks.**

So the file may carry an optional `competencies` block — ids, titles, `critical`, and which
mains each serves. **Still one file, and still not a graph: it has no edges.** It is the
author naming the taxonomy they already had to know to label every item, and it carries
exactly the two things an id prefix cannot express: criticality, and shared membership.

`coverage_critical_only` is **derived** from it rather than configured — true exactly when
the bank narrowed the set, because otherwise the flag is inert and the declaration is
decoration.

The block is optional and its absence changes nothing: all critical, membership by prefix.
That is the strict reading and the right default for a file that says nothing — it fails
loudly at upload, where an author can still act, rather than quietly at convergence.

### 7. The store may be Postgres, and the version is still a hash over bytes

`SqlBackedBankStore` subclasses `BankStore` and overrides only **where the bytes come from**.
The version-keyed caches, the bank loader, and `validate()` with all its rules about
coverage, pairing and duplicate ids are inherited. Reimplementing any of it against SQL
would create a second set of answers to questions that already have one, and the drift would
surface as a bank accepted here and refused there.

The original bytes are stored beside the normalised rows and hashed exactly as the file store
hashes them. Rebuilding the hash from rows would produce a different number for the same
bank: every orchestrator cache would invalidate at once, every pinned expectation in the
suite would move, and five banks would quietly become five different banks with the same
names. **The bytes are identity; the rows are the index.**

It ships **behind `BANK_DATABASE_URL`, empty by default.** The file store remains what the
suite runs against and what `docker compose up` starts, until the database path has run
somewhere real.

---

## What is deliberately NOT done

**`bank-registry`'s write path is not retired**, so "one writer" is not yet true. It was
blocked — ingest could not express a curated critical set or a shared node — and decision 6
removed that. What remains is that it cannot express an **authored prerequisite edge**: an
expert's judgement with a strength attached. That is a gap in a feature which ships inert
and which only `scripts/validate_prerequisite_edges.py`, against a real session corpus, may
ever enable. Retiring the endpoint is now a judgement about one API rather than a decision
that deletes a capability, and deleting a public tested endpoint is not a change to make as
a side effect of unblocking one.

**Sessions still live in one orchestrator process.** The seam exists and is documented; the
blocker is a retention-policy decision about candidate response data, not a technical one.
Adding a database for banks does not settle it, and quietly reusing that database for
sessions would settle it by accident.

**Still no authentication anywhere**, and there are now two unauthenticated write paths
rather than one. Carried forward from ADR-0002 rather than introduced here, and recorded
rather than implied.

---

## Consequences

**Good.** No engine module changed for scoping. The contracts change is additive, so
`SCHEMA_VERSION` does not move and every existing client keeps working. `competency-scope`
declares the narrowest engine slice of any service — `app.config` alone. The database is
opt-in, and the parity suite that has to precede using it exists.

**Costs, accepted.**

- `bank-ingest` and `bank-registry` share a volume until the database is switched on. Two
  processes agreeing about a directory is a weaker guarantee than one transaction, which is
  the whole argument for decision 7.
- A derived graph has no expert prerequisite structure, so an uploaded bank's graph is
  flatter than an authored one.
- `RELATION_THRESHOLD` and `EDGE_FLOOR` are read at ingest, so two banks uploaded either
  side of a change have graphs built to different rules and nothing in a report says so.
- `BANK_DATABASE_URL` is read at import, so a test session with it set sends **every**
  bank-service load to Postgres. Correct for a deployment, wrong for a mixed session, so the
  SQL suites run as their own — the same argument `pytest.ini` already makes.

**Defects found while validating this, and fixed.** `BankItemRef` carried no `status`, so
`HttpUnifiedBank` rebuilt every item with the default `"active"` — `JAI-600` retires 64 code
items whose hidden tests are incomplete, and over HTTP selection was ranking them. The
parity test never caught it because it runs bank `DA`, which has no retired items.

---

## Alternatives considered

**Add `required_nodes` to the coverage functions.** Rejected: four call sites, an argument
threaded through the orchestrator, and a second way to answer what a main requires. Handing
the orchestrator a smaller graph answers it once.

**Let `competency-scope` serve the items.** Rejected — see decision 3.

**Cache scope manifests so `GET /scopes/{id}` works.** Rejected — see decision 4.

**Give `bank-ingest` an optional graph part.** Rejected: it contradicts "the author uploads
one file and never a graph", and decision 6 covers what actually blocked an upload.

**Normalise item payloads into columns.** Rejected: the shape is genuinely per-modality and
nothing queries inside it, so it would buy a migration per authored payload change and no
query. `jsonb`.

**An ORM, and Alembic.** Rejected for now: eight tables owned by one service, about a dozen
statements, and a schema already written down once in `schema.sql`. The moment a column has
to change type under live data is the moment to add the tool.

---

## References

- `docs/architecture-proposal.md` — the whole system drawn, both flows, and the phases
- `docs/bank-schema.md` §1 — the `competencies` block, field by field
- `docs/api.md` — the scoped-assessment contract a frontend builds against
- `services/tests/test_competency_scope_service.py` — a scope over every competency reports
  exactly what no scope does
- `services/tests/test_sql_store_parity.py` — the SQL store answers identically to the file
  store, for all five banks
- `services/bank-ingest/service/derive.py` — the derivation, and the one file to change to
  replace it
