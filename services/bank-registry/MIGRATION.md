# bank-registry — migration record

Items, banks, competency graphs and the propagation policy.

**Status: migrated.** Routes are real; there is no stub left.

## Owns

- question banks and item parameters
- competency graph files
- the three-level propagation policy
- bank/graph pairing
- **registering a bank posted by another service** (new)

## Where the code lives

The engine is installed as a library (`adaptive-engine`), not vendored here. One
implementation of the psychometrics, several adapters over it — see
[ADR-0002](../../docs/adr/0002-engine-as-a-library.md). This service's slice is enforced
by `services/tests/test_services.py`, which parses every import against a declared list:

- `app.services.orchestrator.bank_store` — the two-layer store and its validation
- `app.services.orchestrator.registry` — profiles, versions, caching
- `app.services.orchestrator.bank` — `JsonUnifiedBank`, parity
- `app.services.competency_graph` — graph loading, policy resolution

## Surface

| method | path | returns |
|---|---|---|
| `GET` | `/banks` | registered banks with version, item counts and modalities |
| `GET` | `/banks/{bank_id}` | one bank's profile and version (ETag) |
| `GET` | `/banks/{bank_id}/items` | `BankItemRef[]` — parameters, **never** payloads |
| `GET` | `/banks/{bank_id}/items/{item_id}` | the full item, for rendering and grading |
| `GET` | `/banks/{bank_id}/graph` | nodes and edges |
| `GET` | `/banks/{bank_id}/policy` | resolved propagation policy, per edge, with reasons |
| `GET` | `/banks/{bank_id}/coverage` | item counts per main and modality |
| `GET` | `/banks/{bank_id}/parity` | information-parity diagnostic |
| `POST` | `/banks/validate` | check a bank, write nothing |
| `POST` | `/banks` | register a new bank (409 if the id exists) |
| `PUT` | `/banks/{bank_id}` | replace a bank, creating a new version |
| `DELETE` | `/banks/{bank_id}` | deregister a posted bank; a seed underneath reappears |

## Two read paths, on purpose

A caller that only ranks should never be able to read a question stem. That is a property
of which endpoint it may call, not of what it chooses to look at — the orchestrator uses
`/items` for every decision and `/items/{id}` only for the item it is about to present.

## The write path

Banks used to be a dict in a Python file. They are content, so another service registers
one now. Everything a posted bank has to satisfy is checked **before** it is written: item
parameter bounds, duplicate ids, bank/graph pairing, measured variables having nodes,
required nodes having items, coverage reachable within the question budget. Those are the
same invariants `backend/tests/test_bank_registry.py` asserts of the five checked-in banks
— enforced at the door, because otherwise that suite tests the seeds rather than the system.

**No authentication.** The monolith had none and this carries that forward; it is recorded
as a gap in [ADR-0002](../../docs/adr/0002-engine-as-a-library.md) and `docs/operations.md`
rather than implied. `ADMIN_API_ENABLED=false` turns the write path off without turning off
the service.

## Storage

Two layers. `ENGINE_DATA_DIR` holds the five checked-in banks, read-only. `BANK_STORE_DIR`
holds what has been posted, and shadows a seed of the same id. Deleting the shadow restores
the seed, which is what makes an accidental overwrite recoverable without a redeploy.

## Versioning

Every bank carries a content hash, served as an `ETag`. The orchestrator caches item
parameters against it and an assessment pins the version it began under, so replacing a
bank changes what the next assessment sees and nothing about one already running.

## Checklist

- [x] Serve the read surface over the engine's bank and graph modules
- [x] Add the write path, with validation before any write
- [x] Contract tests: the service and `adaptive_contracts` agree on every DTO
- [x] Enforce the engine slice this service may import
- [x] Point the orchestrator at it through `adaptive_clients`
- [x] Load: p90 24 ms per response against a 150 ms budget (`deploy/latency.py`)
