#!/usr/bin/env python3
"""Isolated Gemini Live smoke test (no Streamlit / CAT / LiteLLM).

Usage:
  cd backend && . .venv/bin/activate
  python scripts/smoke_gemini_live.py
  python scripts/smoke_gemini_live.py --hold-seconds 70   # repro keepalive drops
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from google.genai import types  # noqa: E402

from app.config.voice_settings import voice_settings  # noqa: E402
from app.services.voice_live.audio_codec import LIVE_INPUT_RATE  # noqa: E402
from app.services.voice_live.gemini_live import GeminiLiveInterviewer  # noqa: E402


async def collect_one_turn(session, *, timeout_s: float = 45.0) -> dict:
    pcm_chunks: list[bytes] = []
    out_text: list[str] = []
    deadline = time.monotonic() + timeout_s

    async def _recv():
        async for msg in session.receive():
            sc = getattr(msg, "server_content", None)
            if sc is None:
                continue
            ot = getattr(sc, "output_transcription", None)
            if ot and ot.text:
                out_text.append(ot.text)
            model_turn = getattr(sc, "model_turn", None)
            if model_turn and model_turn.parts:
                for part in model_turn.parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        pcm_chunks.append(inline.data)
            if getattr(sc, "turn_complete", False):
                return

    try:
        await asyncio.wait_for(_recv(), timeout=max(1.0, deadline - time.monotonic()))
    except TimeoutError as exc:
        raise TimeoutError(
            f"no turn_complete within {timeout_s}s "
            f"(got {len(pcm_chunks)} audio chunks, text={''.join(out_text)!r})"
        ) from exc

    pcm = b"".join(pcm_chunks)
    return {
        "transcript": " ".join(out_text).strip(),
        "audio_bytes": len(pcm),
        "audio_seconds": round(len(pcm) / (2 * 24000), 2) if pcm else 0.0,
    }


async def run(*, hold_seconds: float, speak: str) -> int:
    interviewer = GeminiLiveInterviewer()
    if not interviewer.configured:
        print("FAIL: GEMINI_API_KEY is empty in backend/.env")
        return 2

    model = interviewer.google_model_name()
    print(f"model={model}")
    print(f"voice={voice_settings.gemini_live_voice}")
    print(f"gating_mode={voice_settings.gating_mode}")
    print("connecting…")

    client = interviewer._client_or_raise()
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=(
            "You are a smoke-test voice. Speak briefly and clearly. "
            "Do not ask questions. Stop after one short sentence."
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice_settings.gemini_live_voice
                )
            )
        ),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
        ),
    )

    t0 = time.monotonic()
    async with client.aio.live.connect(model=model, config=config) as session:
        connected_ms = int((time.monotonic() - t0) * 1000)
        print(f"OK connect in {connected_ms}ms")

        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text=f"Say exactly this aloud, then stop: {speak}")],
            ),
            turn_complete=True,
        )
        print("waiting for audio turn…")
        turn = await collect_one_turn(session, timeout_s=45.0)
        print(
            f"OK turn: audio={turn['audio_seconds']}s "
            f"({turn['audio_bytes']} bytes) transcript={turn['transcript']!r}"
        )
        if turn["audio_bytes"] <= 0:
            print("FAIL: connected but no audio returned")
            return 1

        if hold_seconds > 0:
            print(f"holding session open for {hold_seconds:.0f}s (keepalive repro)…")
            # Send tiny silent PCM periodically so the socket is not idle-only.
            silence = b"\x00\x00" * int(LIVE_INPUT_RATE * 0.02)  # 20ms
            end = time.monotonic() + hold_seconds
            n = 0
            while time.monotonic() < end:
                try:
                    await session.send_realtime_input(
                        audio=types.Blob(
                            data=silence,
                            mime_type=f"audio/pcm;rate={LIVE_INPUT_RATE}",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    alive = time.monotonic() - t0
                    print(f"FAIL keepalive after {alive:.1f}s: {type(exc).__name__}: {exc}")
                    return 1
                n += 1
                await asyncio.sleep(1.0)
            print(f"OK held {hold_seconds:.0f}s ({n} silence frames)")

    print(f"OK closed cleanly after {time.monotonic() - t0:.1f}s")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Smoke-test Gemini Live only")
    p.add_argument(
        "--hold-seconds",
        type=float,
        default=0.0,
        help="Keep the Live WS open this many seconds after the first turn "
        "(use 70 to reproduce ~40–50s keepalive drops)",
    )
    p.add_argument(
        "--speak",
        default="Gemini Live smoke test is working.",
        help="Exact phrase the model should speak",
    )
    args = p.parse_args()
    try:
        return asyncio.run(run(hold_seconds=args.hold_seconds, speak=args.speak))
    except KeyboardInterrupt:
        print("interrupted")
        return 130
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
