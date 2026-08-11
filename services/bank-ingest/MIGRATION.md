# bank-ingest

**Status: implemented.** New service. It takes over `bank-registry`'s write path.

## What it owns

Turning one uploaded file into a registered bank: parse, **derive the competency graph**,
validate, write.

| method | path | notes |
|---|---|---|
| `POST` | `/uploads?bank_id=` | one file, the questions. 201 with the receipt |
| `POST` | `/uploads/validate?bank_id=` | the identical path with the write omitted |
| `PUT` | `/uploads/{bank_id}` | replace, creating a new version |
| `GET` | `/uploads`, `/uploads/{upload_id}` | what an upload became, and why |
| `DELETE` | `/banks/{bank_id}` | deregister; a shadowed seed reappears |
| `GET` | `/health`, `/config` | the shared operator surface |

## What moved, and what did not

**Moved from `bank-registry`:** the write path. `POST /banks`, `PUT /banks/{id}`,
`POST /banks/validate` and `DELETE /banks/{id}` have equivalents here. The registry keeps
them for now so nothing breaks mid-migration; they retire in the phase that puts the store
behind a database.

**Did NOT move: validation.** `BankStore.validate` is called, not copied. A bank arriving as
an upload clears exactly the bar the five checked-in banks clear — otherwise the engine's own
suite is testing the seeds rather than the system.

**Did NOT move: reading.** This service serves no bank, no item and no graph. One writer,
many readers.

## The one thing that is genuinely new

**The graph is derived, not uploaded.** An author uploads questions and is never asked for a
competency graph, so `derive.py` builds one from the items: nodes from `measures[].variable`,
titles from each item's own `sub_competency` label by majority vote, membership from the id
prefix, and edge weights from item co-measurement. Every derived prerequisite edge ships
inert and unvalidated.

That is a **modelling decision, not a derivation from first principles**, and it is confined
to one module so replacing it — with authored relations in the bank file, or with anything
better — touches one file. Both thresholds are settings.

`backend/scripts/import_human_test_banks.py` already did the coverage-only half of this, and
two checked-in banks (`AIE-JR-V3`, `JAI-600`) ship with graphs built that way.

## Known consequence, recorded rather than discovered

**Every sub-competency of a derived graph is critical**, because nothing in a bank file says
one matters less than another. So a bank whose main declares more sub-competencies than
`CAT_MAX_QUESTIONS - 2` is **refused at upload, naming that main**. `question_bank_AIE.json`
is such a bank: C6 declares sixteen against a cap of twelve. The checked-in `AIE` runs
because its authored graph marks only five of them critical — a distinction no derivation can
make.

## The engine slice it imports

`app.config`, `app.services.orchestrator.bank_store`, `app.services.orchestrator.registry`.
The store and its validation, and nothing else — no schema, no graph module, no selection.
Asserted by `services/tests/test_services.py`.

## Checklist

- [x] one file in, a registered bank out
- [x] both bank shapes the engine's parser accepts
- [x] the graph derived, and shown on the receipt before anything is written
- [x] derived prerequisite edges inert, unvalidated, one direction per pair
- [x] retired items declare no node
- [x] `CONTRIBUTES_TO` mass sums to 1.0 per main, as every authored graph does
- [x] validation reused rather than reimplemented
- [x] a rejection names the item, node or main at fault
- [x] the raw bytes retained, so a rejection is reproducible
- [x] `INGEST_API_ENABLED=false` stops uploads without stopping the service
- [x] an uploaded bank is visible to the registry and scopable with no restart
- [x] port and compose entry agree
