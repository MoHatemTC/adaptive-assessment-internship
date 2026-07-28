#!/usr/bin/env python3
"""Traced Gemini Live smoke: API speak + duplex WS with fake candidate PCM.

Prints a timeline and dumps /api/live/debug. No Streamlit / CAT.

  cd voice_assessment && . .venv/bin/activate
  python scripts/traced_live_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
import sys
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config.voice_settings import voice_settings  # noqa: E402

BASE = voice_settings.live_server_base
WS_BASE = f"ws://{voice_settings.live_server_host}:{voice_settings.live_server_port}"


def tone_pcm(seconds: float = 1.2, freq: float = 440.0, rate: int = 16000) -> bytes:
    """Audible tone so Gemini input transcription has something to work with."""
    n = int(rate * seconds)
    samples = []
    for i in range(n):
        # Soft 440 Hz tone (not silence) — quieter than full-scale to avoid clipping.
        v = int(8000 * math.sin(2 * math.pi * freq * (i / rate)))
        samples.append(struct.pack("<h", v))
    return b"".join(samples)


async def api_speak_smoke() -> dict:
    from scripts.smoke_gemini_live import run as smoke_run

    code = await smoke_run(hold_seconds=0.0, speak="Traced Gemini Live smoke is working.")
    return {"ok": code == 0, "exit": code}


async def duplex_traced() -> dict:
    question = (
        "Smoke duplex: greet in one short sentence and ask for the candidate's name, "
        "then wait."
    )
    async with httpx.AsyncClient(timeout=30.0) as client:
        await client.delete(f"{BASE}/api/live/debug")
        health = (await client.get(f"{BASE}/health")).json()
        created = (
            await client.post(
                f"{BASE}/api/live/rooms",
                json={
                    "item_id": "traced_smoke",
                    "question": question,
                    "save_recording": False,
                },
            )
        ).json()
        room_id = created["room_id"]

    events: list[dict] = []
    t0 = time.monotonic()

    def mark(event: str, **fields):
        events.append({"t": round(time.monotonic() - t0, 2), "event": event, **fields})

    mark("room_created", room_id=room_id)

    uri = f"{WS_BASE}/ws/live/{room_id}"
    pcm = tone_pcm(1.5)
    chunk = 640  # 20ms @ 16k PCM16

    async with websockets.connect(uri, max_size=8_000_000) as ws:
        mark("ws_open")
        # Wait for interviewer to finish speaking (turn_state listening after audio)
        deadline = time.monotonic() + 30
        got_interviewer = False
        interviewer_done = False
        while time.monotonic() < deadline and not interviewer_done:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
            except TimeoutError:
                if got_interviewer:
                    # No more events — treat as done speaking
                    interviewer_done = True
                continue
            if isinstance(msg, bytes):
                mark("audio_chunk", bytes=len(msg))
                got_interviewer = True
                continue
            data = json.loads(msg)
            mark(
                "server",
                type=data.get("type"),
                **{
                    k: (
                        data[k][:120]
                        if isinstance(data[k], str) and len(data[k]) > 120
                        else data[k]
                    )
                    for k in data
                    if k not in {"type", "data", "turn_config", "package"}
                },
            )
            if data.get("type") == "transcript" and data.get("role") == "interviewer":
                got_interviewer = True
            if data.get("type") == "turn_state" and data.get("turn_state") == "listening" and got_interviewer:
                interviewer_done = True
            if data.get("type") == "interviewer_speaking" and data.get("active") is False and got_interviewer:
                interviewer_done = True
            if data.get("type") == "error":
                break

        await asyncio.sleep(0.4)
        await ws.send(json.dumps({"type": "activity_start"}))
        mark("activity_start_sent")
        for i in range(0, len(pcm), chunk):
            await ws.send(pcm[i : i + chunk])
            await asyncio.sleep(0.02)
        mark("pcm_sent", bytes=len(pcm))
        await ws.send(json.dumps({"type": "activity_end"}))
        mark("activity_end_sent")

        # Collect a short window for candidate transcript / interviewer reply
        end = time.monotonic() + 8
        while time.monotonic() < end:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                continue
            if isinstance(msg, bytes):
                mark("audio_chunk", bytes=len(msg))
                continue
            data = json.loads(msg)
            mark(
                "server",
                type=data.get("type"),
                **{
                    k: (data[k][:120] if isinstance(data[k], str) and len(data[k]) > 120 else data[k])
                    for k in data
                    if k not in {"type", "data", "turn_config", "package"}
                },
            )
            if data.get("type") in {"done", "error"}:
                break

        await ws.send(json.dumps({"type": "finish"}))
        mark("finish_sent")
        # Prefer HTTP poll — finish() closes Gemini transport and can outlive WS reads.
        async with httpx.AsyncClient(timeout=15.0) as client:
            for _ in range(40):
                room = (await client.get(f"{BASE}/api/live/rooms/{room_id}")).json()
                mark("poll", status=room.get("status"), error=room.get("error") or None)
                if room.get("status") in {"finished", "error"}:
                    break
                await asyncio.sleep(0.25)
            debug = (await client.get(f"{BASE}/api/live/debug", params={"limit": 80})).json()

    return {
        "room_id": room_id,
        "client_events": events,
        "room": room,
        "live_debug": debug.get("logs") or [],
        "health": health,
    }


async def main() -> int:
    print("=== health ===")
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            h = (await client.get(f"{BASE}/health")).json()
            print(json.dumps(h))
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL live server: {exc}")
            return 2

    print("\n=== 1) API speak smoke ===")
    # Import and run inline to avoid subprocess path issues
    from google.genai import types
    from app.services.voice_live.gemini_live import GeminiLiveInterviewer

    interviewer = GeminiLiveInterviewer()
    model = interviewer.google_model_name()
    client = interviewer._client_or_raise()
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction="Speak one short sentence then stop.",
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
    pcm_n = 0
    text_parts: list[str] = []
    async with client.aio.live.connect(model=model, config=config) as session:
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text="Say exactly: Traced smoke is working.")],
            ),
            turn_complete=True,
        )

        async def _recv():
            nonlocal pcm_n
            async for msg in session.receive():
                sc = getattr(msg, "server_content", None)
                if sc is None:
                    continue
                ot = getattr(sc, "output_transcription", None)
                if ot and ot.text:
                    text_parts.append(ot.text)
                mt = getattr(sc, "model_turn", None)
                if mt and mt.parts:
                    for part in mt.parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and inline.data:
                            pcm_n += len(inline.data)
                if getattr(sc, "turn_complete", False):
                    return

        await asyncio.wait_for(_recv(), timeout=45)
    api = {
        "ok": pcm_n > 0,
        "model": model,
        "audio_bytes": pcm_n,
        "transcript": " ".join(text_parts).strip(),
    }
    print(json.dumps(api, indent=2))
    if not api["ok"]:
        return 1

    print("\n=== 2) Duplex traced smoke (WS + tone PCM) ===")
    result = await duplex_traced()
    print("--- client timeline ---")
    for e in result["client_events"]:
        print(json.dumps(e, default=str))
    print("--- room ---")
    print(
        json.dumps(
            {
                "room_id": result["room"].get("room_id"),
                "status": result["room"].get("status"),
                "error": result["room"].get("error"),
                "turn_state": result["room"].get("turn_state"),
                "turns": result["room"].get("turns"),
                "package": result["room"].get("package"),
                "transcript": result["room"].get("transcript"),
            },
            indent=2,
            default=str,
        )
    )
    print("--- live_debug ---")
    for row in result["live_debug"]:
        print(json.dumps(row, default=str))

    status = result["room"].get("status")
    ok = status == "finished" and not result["room"].get("error")
    print(f"\n=== RESULT: {'PASS' if ok else 'FAIL'} (status={status}) ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
