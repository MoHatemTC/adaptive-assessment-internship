# `cat_engine/live/`

Realtime interview rooms, as a Python API.

## Why this is not a server

`live-voice` was the one service that genuinely needed a socket, and the one piece a "no web
framework" module might have been expected to lose. It does not, because the room was never
coupled to the transport:

```python
room = cat.live.create(item_id, question)
await room.connect()
async for event in room.events():   # interviewer PCM16 @ 24 kHz, and status
    ...
await room.push_pcm(chunk)          # candidate PCM16 @ 16 kHz
result = await room.finish()        # a transcript
```

The service's WebSocket handler was sixty lines of pump between a browser socket and those
four calls. That pump is transport, so it belongs to the host — which is also the only place
that knows how its sockets are authenticated, framed and rate-limited.

## Files

| File | Responsibility |
|---|---|
| `__init__.py` | `Live` — create, get, status, drop, prune, and the gating config a browser reads. `STATIC_DIR` points at the browser client. `realtime_room_class()` imports the room lazily, so the base install does not need a realtime SDK. |
| `static/` | The reference browser client. Served by the host. |

## `static/`

| File | Responsibility |
|---|---|
| `interview.html`, `interview.js` | The interview page, embedded in an iframe. Drives the socket and the microphone. |
| `mic-worklet.js` | The audio worklet. **Must be served from the same origin as the page** — a host that proxies them from different origins gets an interview that opens and records nothing. |
| `chat.html`, `chat.js` | A free-form Live smoke page. Not an assessment. |

## The room does not grade

A finished room produces a transcript package; turning it into a score is the grader's job,
reached by submitting the transcript as a normal answer. Keeping the two apart is why an
audio failure can be reported as an audio failure rather than as a candidate who said
nothing.

A room holds audio, turns and a transcript. It holds **no posterior** and it cannot end an
assessment — which is what makes losing one cost a candidate the interview they were in
rather than the session.
