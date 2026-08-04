# competency-graph — migration checklist

Node state, propagation, coverage. Never a posterior.

## Owns

- per-session node state and the evidence ledger
- upward inference and descendant blocking
- contradiction detection
- the coverage requirement

## Modules that move here

- `backend/app/services/competency_graph/propagation.py`
- `backend/app/services/competency_graph/state.py`
- `backend/app/services/competency_graph/coverage.py`
- `backend/app/services/orchestrator/graph_delta.py — the transaction boundary`

## Planned surface

| method | path | returns |
|---|---|---|
| `POST` | `/sessions/{session_id}/evidence` | apply one direct evidence event, return the delta |
| `GET` | `/sessions/{session_id}/state` | node states, blocked, inferred, contradicted |
| `GET` | `/sessions/{session_id}/coverage/{main}` | unmeasured required nodes |

## Note

Returns InferredSignalDTO, which has no score and no weight. A compromised graph service can change which question is asked next; it cannot move a candidate's estimate.

## Checklist

- [ ] Move the modules above, keeping their tests, into `app/`
- [ ] Replace `backend/` imports of them with a client that speaks `adaptive_contracts`
- [ ] Keep the monolith path working behind a flag until the client is proven in shadow
- [ ] Contract tests: the client and this service agree on every DTO in `adaptive_contracts`
- [ ] Load: the hot path stays inside its budget (see `docs/microservices.md`)
- [ ] Delete the monolith copy and the flag
