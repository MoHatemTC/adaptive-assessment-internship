# live-voice — migration record

Realtime interview rooms, and the page that drives one.

**Status: migrated.** This service did not exist in the original plan; it exists because
`backend/app/main.py` was dissolved and its live half had to land somewhere.

## Owns

- realtime rooms and their lifecycle
- the duplex websocket bridge (candidate PCM up, interviewer PCM down)
- turn-taking and gating parameters
- the `/interview` page and its audio worklet

## Surface

| method | path | returns |
|---|---|---|
| `POST` | `/api/live/rooms` | open a room |
| `GET` | `/api/live/rooms/{id}` | status, turns and the finished transcript package |
| `WS` | `/ws/live/{room_id}` | the duplex bridge |
| `GET` | `/api/live/config` | turn-taking and gating parameters |
| `GET` | `/api/live/debug` · `DELETE` | recent room events — **off by default**, it carries transcripts |
| `GET` | `/interview` · `/chat` · `/static/*` | the pages and their assets |

## Why it is a separate service

ADR-0001 deferred this — "stateful websocket bridges … moving them is a second project."
Dissolving the monolith brought the second project forward, and it turned out to be a lift
rather than a redesign, because the room lifecycle never touched the assessment.

**A room holds audio, turns and a transcript. It holds no posterior and it cannot end an
assessment.** So this service can crash, restart or scale independently, and the worst a
candidate loses is the interview they were in the middle of — which was already true of a
dropped websocket.

## What it does not do

It does not grade. A finished room produces a transcript package; turning that into a score
is the grader's job and the orchestrator carries it there. Keeping the two apart is what
lets an audio failure be reported as an audio failure rather than as a candidate who said
nothing.

Its declared engine slice is `app.services.voice_live` — the rooms and their transport —
and explicitly **not** `app.services.voice`, which is the rubric grader.

## Port

8765, not 808x. That is the port the monolith's helper used and `/interview` is embedded by
URL, so keeping it means one fewer thing to change in whatever already points at it.

## Checklist

- [x] Move the rooms, the websocket bridge and the pages out of `backend/app/main.py`
- [x] Own the static assets rather than reading them from the engine package
- [x] Enforce the engine slice this service may import
- [ ] Interview-page smoke over a real browser (has never been automated, before or after)
