# The module

**Status: collapsed.** One folder — `cat_engine/` — that a host project imports. No web
framework, no ports, no containers. The host owns transport; this owns the measurement.

It was seven FastAPI services over a shared engine library. [ADR-0004](adr/0004-from-services-to-a-module.md)
records why that came back together and what the return cost; [ADR-0001](adr/0001-service-boundaries.md)
and [ADR-0002](adr/0002-engine-as-a-library.md) stay as the history of why it went apart.

## Installing it

Two ways, and the module is the same folder in both.

```bash
pip install cat-engine                 # from this repository
pip install cat-engine[sandbox,live]   # grading code, and realtime interviews
```

Or copy `cat_engine/` into the host tree and import it. Everything it needs is inside —
the psychometrics, the banks, the competency graphs, the rubrics and the browser client for
interviews.

The extras are real. The base install has no sandbox client and no realtime SDK, so a host
that only ever asks multiple-choice questions installs neither. Asking for something an
extra provides raises an error naming the extra rather than an `ImportError` naming a
package the host never chose.

## Using it

```python
from cat_engine import AssessmentModule, CatConfig

cat = AssessmentModule(CatConfig(active_bank="AIE"))

state = await cat.begin(intake={"C1": 3}, confidence={"C1": True})
while state.presenting:
    state = await cat.answer(state.session_id, mcq=2)   # or code= / transcript= / audio=
report = state.report
```

`begin`, `answer` and `state` all return the same shape: `presenting` when there is a
question, `report` when there is not. One thing to render, one place to look for "what now".

A synchronous host gets `SyncAssessmentModule`, which is the same surface with `asyncio.run`
around each coroutine and a clear error — rather than a deadlock — if it is called from
inside a running loop.

### The whole surface

| what | how |
|---|---|
| lifecycle | `begin` · `answer` · `state` · `next_item` · `report` · `discard` |
| catalogue | `banks` · `bank` · `items` · `item` · `graph` · `policy` |
| scoping | `scope` · `scope_graph` |
| bank writes | `upload_bank` · `validate_bank` · `delete_bank` |
| grading | `public_tests` · `trial_run` · `transcribe` |
| author, gated | `diagnostics` · `raw_state` |
| interviews | `live.create` · `live.get` · `live.status` · `live.config` |

Errors are one hierarchy. Every exception carries `code` — unchanged from when this was an
HTTP API, so a frontend written against it still branches correctly — and `status_code`,
which is the answer the module already knows and a host would otherwise have to guess:

```python
except CatError as exc:
    return JSONResponse({"detail": exc.detail, "code": exc.code}, exc.status_code)
```

### Interviews

`live-voice` was the one service that genuinely needed a socket, and it is the piece a "no
web framework" module might have been expected to lose. It does not, because the room was
never coupled to the transport:

```python
room = cat.live.create(item_id="q7", question="Walk me through …")
await room.connect()
async for event in room.events():        # interviewer PCM16 @ 24 kHz, and status
    ...
await room.push_pcm(chunk)               # candidate PCM16 @ 16 kHz
result = await room.finish()             # a transcript, never a score
```

The service's WebSocket handler was sixty lines of pump between a browser socket and those
four calls. That pump is transport, so it is the host's — which is also the only place that
knows how its sockets are authenticated, framed and rate-limited.

`cat_engine.live.STATIC_DIR` holds the reference interview page and its audio worklet. The
worklet must be served from the same origin as the page, so a host serves these files rather
than reimplementing them.

**The room does not grade.** A finished room produces a transcript; submitting it as a normal
answer is what scores it. Keeping the two apart is why an audio failure can be reported as an
audio failure rather than as a candidate who said nothing.

## What the host has to provide

| | for what | required? |
|---|---|---|
| LiteLLM gateway | the picking agent, rubric grading, transcription, interviews | yes |
| E2B sandbox | executing candidate code | only to grade `code` items |
| Postgres | banks (`BANK_DATABASE_URL`) | no — the file store is the default |
| Postgres | sessions (`SESSION_DATABASE_URL`) | no — memory is a supported single-process arrangement |
| a directory | `ENGINE_DATA_DIR` for banks on a volume | no — they are packaged |

Two DSNs, deliberately, even when both point at one server. A bank is content that is
published and kept; a session is a record of what a person answered and has a deletion
deadline. One url for both makes the deadline somebody's afterthought.

## Configuration

Three layers: `CatConfig(...)` wins, then the environment, then the defaults. A host that
passes nothing gets what the environment used to give, which is what makes this drop-in for
a deployment already running the services. The environment variable names are unchanged.

```python
cat = AssessmentModule(CatConfig(
    active_bank="AIE",
    litellm_base_url=..., e2b_api_key=...,
    cat_max_questions=12,
))
```

### One measurement policy per process

The engine reads its policy from a module-level singleton, imported by name at a hundred
call sites. `CatConfig` is applied onto it rather than threaded through the engine, and the
cost is stated rather than hidden — but it is narrower than "one configuration per process",
and the difference decides whether a host can run two modules at once:

| | |
|---|---|
| **measurement policy** | SHARED. A second module that disagrees raises `ConfigConflict`. |
| **topology** | PER INSTANCE. Store locations, which surfaces are open, upload ceilings. |

So a host CAN run a candidate-facing module with diagnostics off beside an authoring one
with them on, reading different bank stores — it cannot run two that disagree about the
stopping rule. `tests/test_config_guard.py` holds both halves to that.

A second module whose MEASUREMENT settings differ raises `ConfigConflict`. That is the
failure `engine_config_fingerprint` was written for — a grader running `CODE_APPROACH=C`
beside an orchestrator that believes it is `B` produces a session whose scores were computed
one way and whose stopping rule assumed another: internally consistent, entirely wrong, and
nothing failing. Across seven services that was a risk; inside one process, letting the
second module quietly win would make it a certainty.

Topology — store locations, which surfaces are open, upload ceilings — is per-instance and
not in the fingerprint. Those change where things are read from, not what a number means.

## What still holds after the collapse

**Only a directly observed response may move a posterior.** `InferredSignalDTO` has no
`score` and no `weight`, so the graph cannot hand the loop something it could mistake for
evidence. Enforced by the contract and asserted against both the wire type and the engine
type.

**The candidate boundary is a type.** Lifecycle methods return contract DTOs, which have no
field to put an answer key in. `diagnostics()` and `raw_state()` **raise** when disabled
rather than returning a thinned version, so "is this safe to return" is never a judgement
call at a call site.

**The sandbox is reachable from one place.** Untrusted candidate code runs in E2B, never in
this process — that was never the service boundary providing it. What the boundary DID
provide was the guarantee that no other component could import the sandbox, and packaging
cannot provide that in one process. It is now `tests/test_module_boundaries.py`, which also
asserts that no module file imports a web framework.

**The engine is one implementation.** The 3PL core, the fractional likelihood and the
stopping rule are where two implementations drifting apart is a measurement problem rather
than a maintenance one. The simulation harness that measured it drove the same objects a
host does; it has since been removed from this branch — see [evidence.md](evidence.md).

## Known limits

- **Sessions are bound to one process** unless `SESSION_DATABASE_URL` is set. `AssessmentState`
  is serialisable by construction, and a host with its own storage can implement the
  `SessionStore` Protocol — the seam the service era could not offer.
- **No authentication anywhere**, including a bank write path that can replace the bank a
  live assessment is running against. Carried forward rather than introduced; the host owns
  it now, which is the right place for it. `ADMIN_API_ENABLED=false` and
  `INGEST_API_ENABLED=false` close the write paths.
- **One measurement policy per process**, as above. Topology is per instance.
- The **latency budget** the split had to respect no longer applies: there are no hops. The
  90-minute cap and the per-response budgets in [architecture.md](architecture.md) are
  unchanged, because they were always about grading and selection rather than transport.
