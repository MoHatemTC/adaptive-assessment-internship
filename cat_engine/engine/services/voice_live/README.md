# `.../services/voice_live/`

Realtime interview rooms and their transport.

**Produces a transcript, never a score.** Turning one into a graded outcome is
[`voice/`](../voice/README.md)'s job, reached by submitting the transcript as a normal
answer. Keeping the two apart is why an audio failure can be reported as an audio failure
rather than as a candidate who said nothing.

## The audio path

```
browser mic ──16 kHz PCM16──►  room.push_pcm  ──►  model session
browser spkr ◄─24 kHz PCM16──  room.events()  ◄──
```

The two rates are not interchangeable and neither is negotiated: candidate audio goes up at
16 kHz, interviewer audio comes down at 24 kHz. Swapping them plays audio at the wrong speed
and raises nothing, which is why both constants are asserted in `tests/test_audio_codec.py`
rather than assumed.

A browser picks its own capture rate, so `wav_bytes_to_pcm16` resamples whatever arrives —
8 k, 22.05 k, 44.1 k, 48 k — down to 16 kHz, downmixing stereo by **averaging** rather than
dropping a channel. A candidate on one side of a stereo capture would otherwise be silent.

## Turn-taking is manual, deliberately

Server VAD is broken on the Live preview model, so the browser marks activity start and end
explicitly and the gating thresholds are served from `Live.config()` rather than hardcoded in
the client. A change to turn-taking is then a setting rather than a client release.

## Files

| File | Responsibility |
|---|---|
| `realtime_room.py` | The duplex room: `connect` / `push_pcm` / `events` / `finish`, plus turn state, the transcript ledger and the idle prune. Transport-agnostic — which is why the module can expose it as a Python API and let the host own the socket. |
| `litellm_realtime.py` | The async session over LiteLLM `/v1/realtime`, speaking OpenAI Realtime events. |
| `gemini_live.py` | The conversational interviewer and `LiveInterviewResult`. Imports `google.genai`, which is why the whole package is loaded lazily. |
| `audio_codec.py` | PCM ↔ WAV, resampling and downmix. Pure arithmetic over bytes, and every failure mode in it is silent — wrong rate is a chipmunk, bad downmix is a quieter recording — so it is tested hardest of anything here. |
| `transcribe.py` | Audio to text via LiteLLM. Separate from grading because the two fail differently: a transcription failure is a retry, a grading failure is an unscorable response that must move no estimate. |
| `debug_log.py` | A process-wide ring of room events. Carries candidate transcripts, so it is off unless a deployment asks for it. |
| `__init__.py` | Package marker. |

## Optional at install time

This package needs the `live` extra. A host that never runs an interview never installs a
realtime SDK, and asking for a room without it raises `LiveUnavailable` naming the extra
rather than an `ImportError` naming Google.

## What a room cannot do

It holds audio, turns and a transcript. It holds **no posterior** and it cannot end an
assessment — which is what makes losing one cost a candidate the interview they were in
rather than the session.
