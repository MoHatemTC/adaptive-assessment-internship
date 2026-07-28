"""FastAPI entrypoint for realtime Gemini Live interviews."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.services.voice_live.debug_log import clear as clear_live_debug
from app.services.voice_live.debug_log import live_debug, live_debug_exc, snapshot
from app.services.voice_live.realtime_room import RealtimeLiveRoom

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"

app = FastAPI(title="Voice assessment Live interviewer", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateRoomRequest(BaseModel):
    item_id: str = "live_chat"
    question: str = (
        "Greet the human warmly in one short sentence and invite them to talk "
        "about whatever they like. Then listen."
    )
    save_recording: bool = False
    mode: str = "interview"  # interview | chat


class CreateRoomResponse(BaseModel):
    room_id: str
    ws_path: str
    mode: str = "interview"


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/live/debug")
def live_debug_feed(limit: int = 50):
    return {"logs": snapshot(limit=max(1, min(limit, 200)))}


@app.delete("/api/live/debug")
def live_debug_clear():
    clear_live_debug()
    return {"ok": True}


@app.get("/api/live/config")
def live_config():
    from app.config.voice_settings import voice_settings

    # Chat always uses manual markers — server VAD is broken on the Live preview model.
    gating = "manual"
    return {
        "gating_mode": gating,
        "stream_mode": "gated",
        "thinking_pause_ms": voice_settings.thinking_pause_ms,
        "turn_end_silence_ms": voice_settings.turn_end_silence_ms,
        "nudge_silence_ms": voice_settings.nudge_silence_ms,
        "gate_open_db": voice_settings.gate_open_db,
        "gate_close_db": voice_settings.gate_close_db,
        "gate_open_frames": voice_settings.gate_open_frames,
        "gate_close_frames": voice_settings.gate_close_frames,
        "gate_barge_in_extra_db": voice_settings.gate_barge_in_extra_db,
        "model": voice_settings.gemini_live_model,
    }


@app.post("/api/live/rooms", response_model=CreateRoomResponse)
async def create_room(body: CreateRoomRequest):
    mode = body.mode if body.mode in {"interview", "chat"} else "interview"
    room = RealtimeLiveRoom.create(
        item_id=body.item_id,
        question=body.question,
        save_recording=body.save_recording,
        mode=mode,
    )
    live_debug(
        "room_created",
        room_id=room.room_id,
        item_id=body.item_id,
        mode=mode,
        save_recording=body.save_recording,
    )
    return CreateRoomResponse(
        room_id=room.room_id,
        ws_path=f"/ws/live/{room.room_id}",
        mode=mode,
    )


@app.get("/api/live/rooms/{room_id}")
async def get_room(room_id: str):
    room = RealtimeLiveRoom.get(room_id)
    if room is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return {
        "room_id": room.room_id,
        "status": room.state.status,
        "turn_state": room.state.turn_state,
        "item_id": room.state.item_id,
        "mode": room.state.mode,
        "error": room.state.error,
        "package": room.state.package,
        "turns": room.state.turns,
        "transcript": (room.state.result.transcript if room.state.result else ""),
    }


@app.websocket("/ws/live/{room_id}")
async def live_ws(websocket: WebSocket, room_id: str):
    room = RealtimeLiveRoom.get(room_id)
    if room is None:
        # Accept first so the browser gets a JSON error instead of a bare handshake failure.
        await websocket.accept()
        live_debug("ws_room_missing", room_id=room_id)
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "error": f"room {room_id} not found — refresh and Start call again",
                    }
                )
            )
        finally:
            await websocket.close(code=4404)
        return
    await websocket.accept()

    async def pump_out():
        async for event in room.events():
            if event.get("type") == "audio":
                # Binary frame = interviewer PCM16 @ 24 kHz
                await websocket.send_bytes(event["data"])
            else:
                await websocket.send_text(json.dumps(event))
            if event.get("type") in {"done", "error"}:
                break

    import asyncio

    out_task = asyncio.create_task(pump_out())
    try:
        live_debug("ws_accepted", room_id=room_id)
        if room.state.status in {"finished", "error"} and room._closed:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "error": "room already ended — refresh and Start call again",
                    }
                )
            )
            return
        if room._session is None:
            await room.connect()
            live_debug("ws_live_connected", room_id=room_id, item_id=room.state.item_id)
        else:
            live_debug("ws_rejoined", room_id=room_id, status=room.state.status)
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "status",
                        "status": room.state.status,
                        "room_id": room.room_id,
                        "mode": room.state.mode,
                        "turn_state": room.state.turn_state,
                        "turn_config": room.turn_config(),
                    }
                )
            )
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            if "bytes" in message and message["bytes"] is not None:
                await room.push_pcm(message["bytes"])
                continue
            text = message.get("text")
            if not text:
                continue
            payload = json.loads(text)
            kind = payload.get("type")
            if kind == "finish":
                live_debug("ws_finish_requested", room_id=room_id)
                await room.finish()
                live_debug(
                    "ws_finished",
                    room_id=room_id,
                    status=room.state.status,
                    turns=len(room.state.turns),
                    speech_s=round(room.state.candidate_speech_seconds, 2),
                    outcome=(room.state.package or {}).get("outcome_status"),
                    reason=(room.state.package or {}).get("reason_code"),
                    bytes_fwd=room._bytes_forwarded,
                )
                break
            if kind == "activity_start":
                live_debug("ws_activity_start", room_id=room_id, turn=room.state.turn_state)
                await room.activity_start()
            elif kind == "activity_end":
                live_debug("ws_activity_end", room_id=room_id, turn=room.state.turn_state)
                await room.activity_end()
            elif kind == "pause":
                live_debug("ws_pause", room_id=room_id, turn=room.state.turn_state)
                await room.mark_pause()
            elif kind == "resume":
                live_debug("ws_resume", room_id=room_id, turn=room.state.turn_state)
                await room.mark_resume()
            elif kind == "hello":
                # The browser reports its real AudioContext rate. A context rate that
                # is not a multiple of 16 kHz used to produce silently untranscribable
                # audio; log it so that failure is never invisible again.
                live_debug(
                    "ws_hello",
                    room_id=room_id,
                    context_rate=payload.get("context_rate"),
                    step=payload.get("step"),
                )
            elif kind == "calibrated":
                live_debug(
                    "ws_calibrated",
                    room_id=room_id,
                    noise_floor_db=payload.get("noise_floor_db"),
                    p95_db=payload.get("p95_db"),
                    open_db=payload.get("open_db"),
                )
            elif kind == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
            else:
                live_debug("ws_unknown_message", room_id=room_id, kind=kind)
    except WebSocketDisconnect:
        live_debug("ws_client_disconnect", room_id=room_id)
        logger.info("client disconnected room=%s", room_id)
        if room.state.status == "live":
            try:
                await room.finish()
            except Exception:  # noqa: BLE001
                logger.debug("finish on disconnect failed", exc_info=True)
    except Exception as exc:  # noqa: BLE001
        live_debug_exc("ws_failed", exc, room_id=room_id)
        logger.exception("live ws failed room=%s", room_id)
        room.state.status = "error"
        room.state.error = str(exc)
        try:
            await websocket.send_text(json.dumps({"type": "error", "error": str(exc)}))
        except Exception:  # noqa: BLE001
            pass
    finally:
        out_task.cancel()
        try:
            await out_task
        except asyncio.CancelledError:
            pass
        live_debug(
            "ws_closed",
            room_id=room_id,
            status=room.state.status,
            error=room.state.error or None,
        )


@app.get("/")
def index():
    """Free-form human↔human Gemini Live voice chat (WebSocket duplex)."""
    return FileResponse(STATIC_DIR / "chat.html")


@app.get("/interview")
def interview_page():
    """Assessment interview UI (used by Streamlit iframes)."""
    return FileResponse(STATIC_DIR / "interview.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
