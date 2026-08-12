"""PCM and WAV handling for the interview audio path.

WHY THIS IS WORTH TESTING WHEN THE REALTIME BRIDGE IS NOT

The bridge needs a live model and real audio, so a test of it would be a test of a mock.
This is different: it is pure arithmetic over bytes, it runs in microseconds, and it sits
between a candidate's microphone and everything downstream. Every failure mode here is
silent — wrong sample rate is a chipmunk, a bad downmix is a quieter recording, an off-by-one
in resampling is a click — and none of them raises. The candidate's answer still gets
graded; it is just graded on damaged audio.

THE TWO RATES ARE NOT INTERCHANGEABLE

Candidate audio goes UP at 16 kHz and interviewer audio comes DOWN at 24 kHz. Getting them
the wrong way round produces audio that plays at the wrong speed rather than failing, which
is why both constants are asserted rather than assumed.

Untested before this file, at 17% coverage.
"""

from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from cat_engine.engine.services.voice_live.audio_codec import (
    LIVE_INPUT_RATE,
    LIVE_OUTPUT_RATE,
    pcm16_to_wav_bytes,
    wav_bytes_to_pcm16,
)


def tone(seconds: float, rate: int, channels: int = 1, hz: float = 440.0) -> bytes:
    """A real WAV, built rather than fixtured, so the rate and channel count are known."""
    frames = int(seconds * rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        for n in range(frames):
            sample = int(20_000 * math.sin(2 * math.pi * hz * n / rate))
            wf.writeframes(struct.pack("<h", sample) * channels)
    return buf.getvalue()


def samples_of(pcm: bytes) -> list[int]:
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm))


class TestTheRatesAreTheOnesTheBridgeUses:
    def test_up_is_16k_and_down_is_24k(self):
        """Asserted rather than assumed: swapping them plays audio at the wrong speed and
        raises nothing."""
        assert LIVE_INPUT_RATE == 16_000
        assert LIVE_OUTPUT_RATE == 24_000


class TestDecoding:
    def test_a_wav_at_the_target_rate_round_trips_untouched(self):
        pcm, duration = wav_bytes_to_pcm16(tone(0.5, LIVE_INPUT_RATE))
        assert len(samples_of(pcm)) == int(0.5 * LIVE_INPUT_RATE)
        assert duration == pytest.approx(0.5, abs=0.01)

    def test_duration_is_reported_in_seconds_at_the_TARGET_rate(self):
        """The number the turn-taking logic reads. Computing it against the SOURCE rate
        would make every resampled turn the wrong length, and silence detection with it."""
        _pcm, duration = wav_bytes_to_pcm16(tone(1.0, 48_000))
        assert duration == pytest.approx(1.0, abs=0.02)

    @pytest.mark.parametrize("source_rate", [8_000, 22_050, 44_100, 48_000])
    def test_any_common_capture_rate_resamples_to_the_target(self, source_rate):
        """A browser picks its own rate. Every one of these must land at 16 kHz, because
        the model session is opened at that rate and does not negotiate."""
        pcm, duration = wav_bytes_to_pcm16(tone(0.25, source_rate))
        assert len(samples_of(pcm)) == pytest.approx(0.25 * LIVE_INPUT_RATE, rel=0.02)
        assert duration == pytest.approx(0.25, abs=0.02)

    def test_stereo_is_downmixed_to_mono_by_averaging(self):
        """Not by dropping a channel: a candidate on one side of a stereo capture would
        otherwise be silent."""
        pcm, _ = wav_bytes_to_pcm16(tone(0.1, LIVE_INPUT_RATE, channels=2))
        assert len(samples_of(pcm)) == int(0.1 * LIVE_INPUT_RATE)
        assert any(s != 0 for s in samples_of(pcm)), "the downmix produced silence"

    def test_a_resampled_signal_keeps_its_amplitude(self):
        """Linear interpolation must not attenuate. A quieter recording reads as a quieter
        candidate, and the gate thresholds are in dB."""
        pcm, _ = wav_bytes_to_pcm16(tone(0.2, 48_000))
        assert max(abs(s) for s in samples_of(pcm)) > 15_000

    def test_an_explicit_target_rate_is_honoured(self):
        pcm, _ = wav_bytes_to_pcm16(tone(0.2, 16_000), target_rate=8_000)
        assert len(samples_of(pcm)) == pytest.approx(0.2 * 8_000, rel=0.02)


class TestDecodingRefusesWhatItCannotHandle:
    def test_a_non_wav_payload_raises_something_a_caller_can_act_on(self):
        """The browser occasionally hands over webm. The message has to say "record again"
        rather than surfacing a `wave.Error`, because the person who sees it is a
        candidate."""
        with pytest.raises(ValueError) as caught:
            wav_bytes_to_pcm16(b"\x1aE\xdf\xa3 this is webm, not wav")
        assert "wav" in str(caught.value).lower()

    def test_empty_bytes_raise_rather_than_returning_silence(self):
        """Returning empty PCM would be scored as a candidate who said nothing."""
        with pytest.raises(ValueError):
            wav_bytes_to_pcm16(b"")

    def test_a_structurally_valid_wav_with_no_audio_is_refused(self):
        """The second wav bug, and the subtler of the two.

        A capture that failed after the header was written decodes cleanly to zero frames.
        Returning it handed the grader an empty transcript, which is scored as a candidate
        who SAID NOTHING rather than as a recording that did not happen — two different
        findings about a person, and only one of them is true.
        """
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(LIVE_INPUT_RATE)
        with pytest.raises(ValueError) as caught:
            wav_bytes_to_pcm16(buf.getvalue())
        assert "no audio" in str(caught.value)

    def test_a_very_short_capture_survives_as_audio_rather_than_being_refused(self):
        """Where the "too short" judgement belongs, checked so nobody adds it twice.

        The resampler keeps at least one sample, so a three-frame capture at 48 kHz decodes
        to 62 microseconds of audio rather than to nothing. That is deliberate: the codec
        answers "is this readable", and only genuinely empty audio is not.

        HOW SHORT IS TOO SHORT is a different question with a configured answer —
        `voice_settings` carries the silence and speech-duration thresholds, and the room
        applies them against `candidate_speech_seconds`. A second floor here would be a
        second policy for one decision, and the two would disagree the first time either
        moved.
        """
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(48_000)
            wf.writeframes(struct.pack("<h", 0) * 3)
        pcm, duration = wav_bytes_to_pcm16(buf.getvalue())
        assert pcm, "a short capture was refused; the floor belongs to turn-taking"
        assert 0 < duration < 0.01

    def test_eight_bit_audio_is_refused_by_name(self):
        """Reading 8-bit samples as 16-bit produces noise at the right length — audio that
        looks valid all the way to the transcriber."""
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(1)
            wf.setframerate(LIVE_INPUT_RATE)
            wf.writeframes(b"\x80" * 1000)
        with pytest.raises(ValueError) as caught:
            wav_bytes_to_pcm16(buf.getvalue())
        assert "16-bit" in str(caught.value)


class TestEncoding:
    def test_it_produces_a_wav_a_standard_reader_accepts(self):
        pcm = struct.pack("<1000h", *[1000] * 1000)
        with wave.open(io.BytesIO(pcm16_to_wav_bytes(pcm)), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == LIVE_OUTPUT_RATE
            assert wf.getnframes() == 1000

    def test_the_default_rate_is_the_INTERVIEWER_rate(self):
        """This wraps audio coming DOWN from the model, so 24 kHz is the correct default;
        16 kHz here would play the interviewer slowly."""
        with wave.open(io.BytesIO(pcm16_to_wav_bytes(b"\x00\x00" * 100)), "rb") as wf:
            assert wf.getframerate() == LIVE_OUTPUT_RATE

    def test_encode_then_decode_preserves_the_samples(self):
        """The round trip the debug path takes when a recording is saved and replayed."""
        original = [0, 5000, -5000, 32767, -32768, 123]
        pcm = struct.pack(f"<{len(original)}h", *original)
        wav = pcm16_to_wav_bytes(pcm, sample_rate=LIVE_INPUT_RATE)
        decoded, _ = wav_bytes_to_pcm16(wav, target_rate=LIVE_INPUT_RATE)
        assert samples_of(decoded) == original

    def test_empty_pcm_produces_a_valid_empty_wav(self):
        """A room that ended before the interviewer spoke. Still a playable file."""
        with wave.open(io.BytesIO(pcm16_to_wav_bytes(b"")), "rb") as wf:
            assert wf.getnframes() == 0
