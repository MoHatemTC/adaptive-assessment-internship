# bank-registry — migration checklist

Items, banks, competency graphs and the propagation policy.

## Owns

- question banks and item parameters
- competency graph files
- the three-level propagation policy
- bank/graph pairing

## Modules that move here

- `backend/app/services/orchestrator/bank.py — UnifiedBankRepository (already a Protocol)`
- `backend/app/services/orchestrator/registry.py — bank/graph pairing and caching`
- `backend/app/services/competency_graph/policy.py — policy resolution`
- `backend/app/services/competency_graph/validator.py — graph loading`

## Planned surface

| method | path | returns |
|---|---|---|
| `GET` | `/banks` | registered banks with item counts and modalities |
| `GET` | `/banks/{bank_id}/items` | BankItemRef list — parameters, never payloads |
| `GET` | `/banks/{bank_id}/items/{item_id}/payload` | the modality payload, for rendering and grading |
| `GET` | `/banks/{bank_id}/graph` | nodes and edges |
| `GET` | `/banks/{bank_id}/policy` | resolved propagation policy, per edge, with reasons |

## Note

Two read paths on purpose: one that ranks (parameters only) and one that renders (payloads). A caller that only selects should never be able to read a question stem.

## Checklist

- [ ] Move the modules above, keeping their tests, into `app/`
- [ ] Replace `backend/` imports of them with a client that speaks `adaptive_contracts`
- [ ] Keep the monolith path working behind a flag until the client is proven in shadow
- [ ] Contract tests: the client and this service agree on every DTO in `adaptive_contracts`
- [ ] Load: the hot path stays inside its budget (see `docs/microservices.md`)
- [ ] Delete the monolith copy and the flag
