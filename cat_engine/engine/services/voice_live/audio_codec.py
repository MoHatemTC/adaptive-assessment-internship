"""PCM / WAV helpers for Gemini Live audio I/O."""

from __future__ import annotations

import io
import wave
from array import array

# Gemini Live input typically expects 16-bit PCM mono @ 16 kHz.
LIVE_INPUT_RATE = 16_000
# Live audio responses are commonly 24 kHz PCM16 mono.
LIVE_OUTPUT_RATE = 24_000


def wav_bytes_to_pcm16(
    audio_bytes: bytes, target_rate: int = LIVE_INPUT_RATE
) -> tuple[bytes, float]:
    """Decode WAV (or raw-ish Streamlit capture) → mono PCM16 at target_rate.

    Returns (pcm_bytes, duration_seconds).
    """
    bio = io.BytesIO(audio_bytes)
    try:
        with wave.open(bio, "rb") as wf:
            nch = wf.getnchannels()
            sw = wf.getsampwidth()
            rate = wf.getframerate()
            nframes = wf.getnframes()
            raw = wf.readframes(nframes)
    except (wave.Error, EOFError):
        # A browser occasionally hands over webm/ogg instead of WAV, and a capture that
        # failed outright hands over nothing at all.
        #
        # EOFError is here because `wave.open` raises THAT rather than `wave.Error` on an
        # empty or truncated payload — so before it was caught, a candidate whose recording
        # came back empty got a bare `EOFError` instead of "record again". Both cases are
        # the same thing to whoever is looking at the screen: audio that cannot be read.
        raise ValueError(
            "unsupported or empty audio — record again (16-bit PCM WAV expected)"
        ) from None

    if sw != 2:
        raise ValueError(f"expected 16-bit PCM WAV, got sample width {sw}")

    samples = array("h")
    samples.frombytes(raw)
    if nch > 1:
        # Downmix to mono
        mono = array("h")
        for i in range(0, len(samples), nch):
            chunk = samples[i : i + nch]
            mono.append(int(sum(chunk) / len(chunk)))
        samples = mono

    if rate != target_rate and rate > 0:
        samples = _resample_linear(samples, rate, target_rate)
        rate = target_rate

    if not samples:
        # A STRUCTURALLY VALID WAV CARRYING NO AUDIO.
        #
        # A capture that failed after the header was written decodes cleanly to zero
        # frames, and returning it would hand the grader an empty transcript — which is
        # scored as a candidate who said nothing rather than as a recording that did not
        # happen. Those are different findings about a person, so this is refused the same
        # way an unreadable payload is.
        raise ValueError(
            "the recording contains no audio — record again (16-bit PCM WAV expected)"
        )

    duration = len(samples) / float(rate) if rate else 0.0
    return samples.tobytes(), duration


def pcm16_to_wav_bytes(pcm: bytes, sample_rate: int = LIVE_OUTPUT_RATE) -> bytes:
    """Wrap raw PCM16 mono as a WAV for st.audio playback."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def _resample_linear(samples: array, src_rate: int, dst_rate: int) -> array:
    if src_rate == dst_rate or not samples:
        return samples
    ratio = dst_rate / float(src_rate)
    out_len = max(int(len(samples) * ratio), 1)
    out = array("h")
    for i in range(out_len):
        src_idx = i / ratio
        left = int(src_idx)
        right = min(left + 1, len(samples) - 1)
        frac = src_idx - left
        val = samples[left] * (1.0 - frac) + samples[right] * frac
        out.append(int(max(-32768, min(32767, val))))
    return out
