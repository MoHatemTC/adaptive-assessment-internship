# `.../services/voice_live/`

Realtime interview rooms and their transport.

**Produces a transcript, never a score.** Turning one into a graded outcome is
[`voice/`](../voice/README.md)'s job, reached by submitting the transcript as a normal
answer — which is why an audio failure can be reported as an audio failure rather than as a
candidate who said nothing.

## Files

| File | Responsibility |
|---|---|
| `realtime_room.py` | The duplex room: browser mic ↔ model ↔ browser speaker. `connect` / `push_pcm` / `events` / `finish` — transport-agnostic, which is why the module can expose it as a Python API and let the host own the socket. |
| `litellm_realtime.py` | The async session over LiteLLM `/v1/realtime`, speaking OpenAI Realtime events. |
| `gemini_live.py` | The conversational interviewer and `LiveInterviewResult`. Imports `google.genai`, which is why the whole package is loaded lazily — see [`live/`](../../../live/README.md). |
| `audio_codec.py` | PCM / WAV helpers for the audio path. |
| `transcribe.py` | Audio transcription via LiteLLM. Separate from grading because the two fail differently: a transcription failure is a retry, a grading failure is an unscorable response. |
| `debug_log.py` | A process-wide ring of room events. Carries candidate transcripts, so it is off unless a deployment asks for it. |

## Optional at install time

This package needs the `live` extra. A host that never runs an interview never installs a
realtime SDK, and asking for a room without it raises an error naming the extra rather than
an `ImportError` naming Google.
