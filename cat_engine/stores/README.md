# `cat_engine/stores/`

Where live assessments and registered banks are kept.

## Two stores, two lifetimes, deliberately not one

A bank is content that is published and kept. A session is a record of what a person
answered and has a deletion deadline. They take **separate DSNs** even when both point at one
server, because one url for both makes the deadline somebody's afterthought.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | The `SessionStore` Protocol, and `open_session_store` — which returns an in-memory store when no DSN is configured and says so in the log. |
| `sessions.py` | `Session` and `InMemorySessionStore`: the live assessments, their locks, their exposure-control generators, capacity and retention. Write-through to a backing store when one is given. |
| `sql/` | The Postgres implementations. Imported lazily at every call site, so a host that never configures a database never installs a driver. |

## `sql/`

| File | Responsibility |
|---|---|
| `__init__.py` | Re-exports `SqlBankStore`, `SqlSessionStore`, `StoredBank`, and the rng helpers. |
| `sql.py` | The bank tables, the content hash, and the all-or-nothing write. |
| `backed.py` | `SqlBackedBankStore` and `install`: point this process's bank store at Postgres, ensuring the schema and seeding the checked-in banks idempotently on their content hash. |
| `sessions.py` | The session table, the compare-and-swap that makes two writers safe, and `SessionConflict`. |
| `seed.py` | Loading the checked-in banks into the database so they land with the same version the file store computes. |
| `schema.sql`, `sessions.sql` | The DDL, kept as SQL rather than generated, because it is read by people deciding on a retention policy. |

## Three arrangements, and the choice is the host's

```
InMemorySessionStore()            one process; a restart loses every assessment mid-answer
InMemorySessionStore(sql_store)   write-through to Postgres; a restart resumes
anything satisfying SessionStore  the host's own, with its own retention policy
```

The third is the one the service era could not offer. Where candidate response data lives,
for how long, and under whose deletion deadline is not this module's decision to make.

**Write-through rather than write-behind**: the thing being persisted is the record of what a
candidate answered, and losing the last write because a process died between the answer and
the flush is the exact failure it exists to prevent.

## Two rules that never bend

An assessment in progress is **never** evicted — capacity pressure and retention only ever
remove sessions that have finished. And a session is bound to the **bank version** it began
under, so replacing a bank cannot change the item pool underneath a live candidate.
