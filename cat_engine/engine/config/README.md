# `cat_engine/engine/config/`

All engine policy, one place.

## Files

| File | Responsibility |
|---|---|
| `settings.py` | Every engine setting: `cat_*` (the MCQ engine's CAT policy), `code_*` (the code engine's), `orchestrator_*` and `graph_*`. A module-level singleton, imported by name at ~100 call sites — which is why `CatConfig` is applied onto it rather than threaded through. |
| `voice_settings.py` | The voice / open-ended half, separate because it configures a different measurement scale and a realtime transport. |
| `paths.py` | Where the engine's data lives. `ENGINE_DATA_DIR` moves the directory without moving the code. Reads the environment **directly** rather than through `Settings`, because it has to resolve before anything that opens a file — including the bank store, built at import. |
| `fingerprint.py` | A twelve-character hash over the settings that change what a candidate is SCORED by. Not urls, not keys, not log levels — a fingerprint that flagged a log level would be ignored within a week. |

## The prefixes are not decoration

`cat_*` and `code_*` measure on different scales, so a standard-error target means different
things to each: 0.55 is a reasonable stop for theta over [-4, 4] and would be unreachable
nonsense for mastery over [0, 1]. Sharing a name would invite exactly that confusion.

## What the fingerprint is for

Two components disagreeing about `CODE_APPROACH` produces a session whose scores were
computed one way and whose stopping rule assumed another: internally consistent, entirely
wrong, and nothing failing. Across seven services that was a risk; inside one process,
`CatConfig` refuses to build a second module whose fingerprint differs.

## `.env` is resolved against the working directory

Not against this package. A library that reads a dotfile out of its own installed location
picks up whatever a wheel happened to ship, ignores the host's configuration, and cannot be
overridden from outside.
