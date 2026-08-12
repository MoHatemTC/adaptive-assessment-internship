"""live-voice — realtime interview rooms, and the page that drives one.

WHY THIS IS A SERVICE AND NOT PART OF THE ORCHESTRATOR

ADR-0001 deferred it: "voice live sessions stay in the orchestrator's process for now …
they are stateful websocket bridges, and moving them is a second project." Dissolving
`backend/app/main.py` brought the second project forward, and it turned out to be a lift
rather than a redesign — the room lifecycle is unchanged, because it never touched the
assessment. A room holds audio, turns and a transcript. It holds no posterior, and it
cannot end an assessment.

That is what makes the split safe in the direction that matters: this service can crash,
restart or be scaled independently and the worst a candidate loses is the interview they
were in the middle of, which was already true of a dropped websocket.

WHAT IT DOES NOT DO

It does not grade. A finished room produces a transcript package; turning that into a score
is the grader's job, and the orchestrator carries it there. Keeping the two apart is why an
audio failure can be reported as an audio failure rather than as a candidate who said
nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from adaptive_service import install_error_handlers, operator_router
from adaptive_service.errors import ServiceError
from cat_engine.engine.config.settings import settings as engine_settings
from cat_engine.engine.config.voice_settings import voice_settings
from cat_engine.engine.services import observability
from cat_engine.engine.services.voice_live.debug_log import clear as clear_live_debug
from cat_engine.engine.services.voice_live.debug_log import live_debug, live_debug_exc, snapshot
from cat_engine.engine.services.voice_live.realtime_room import RealtimeLiveRoom
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import settings

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Live rooms need tracing configured in THIS process. It is per-process state, and this
    # is no longer the same process as the one running the assessment.
    observability.configure()
    yield


app = FastAPI(
    title="live-voice",
    version=settings.release,
    summary="Realtime interview rooms for open and spoken items.",
    description=__doc__,
    lifespan=_lifespan,
)
install_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    operator_router(
        settings,
        health_detail=lambda: {
            "live_model": engine_settings.litellm_live_preview_model,
            "debug_api_enabled": settings.live_debug_api_enabled,
        },
    )
)


class CreateRoomRequest(BaseModel):
    item_id: str = "live_chat"
    question: str = (
        "Greet the human warmly in one short sentence and invite them to talk "
        "about whatever they like. Then listen."
    )
    save_recording: bool = False
    mode: str = "interview"  # interview | chat
    #: The assessment this interview belongs to, so a trace groups the realtime span with
    #: the picker and grader spans of the same session. Carried, never interpreted — this
    #: service does not know what an assessment is.
    assessment_session_id: str = ""


class CreateRoomResponse(BaseModel):
    room_id: str
    ws_path: str
    mode: str = "interview"


@app.get("/api/live/config", tags=["live"], summary="Turn-taking and gating parameters")
async def live_config() -> dict:
    # Chat always uses manual markers — server VAD is broken on the Live preview model.
    return {
        "gating_mode": "manual",
        "stream_mode": "gated",
        "thinking_pause_ms": voice_settings.thinking_pause_ms,
        "turn_end_silence_ms": voice_settings.turn_end_silence_ms,
        "nudge_silence_ms": voice_settings.nudge_silence_ms,
        "gate_open_db": voice_settings.gate_open_db,
        "gate_close_db": voice_settings.gate_close_db,
        "gate_open_frames": voice_settings.gate_open_frames,
        "gate_close_frames": voice_settings.gate_close_frames,
        "gate_barge_in_extra_db": voice_settings.gate_barge_in_extra_db,
        "model": engine_settings.litellm_live_preview_model,
        "via": "litellm",
    }


@app.get(
    "/api/live/debug",
    tags=["operator"],
    summary="Recent room events. Off by default — it carries transcripts.",
)
async def live_debug_feed(limit: int = 50) -> dict:
    if not settings.live_debug_api_enabled:
        raise ServiceError(404, "debug_api_disabled", "not found")
    return {"logs": snapshot(limit=max(1, min(limit, 200)))}


@app.delete("/api/live/debug", tags=["operator"], summary="Clear the debug feed")
async def live_debug_clear() -> dict:
    if not settings.live_debug_api_enabled:
        raise ServiceError(404, "debug_api_disabled", "not found")
    clear_live_debug()
    return {"ok": True}


@app.post(
    "/api/live/rooms",
    response_model=CreateRoomResponse,
    tags=["live"],
    summary="Open an interview room",
)
async def create_room(body: CreateRoomRequest) -> CreateRoomResponse:
    mode = body.mode if body.mode in {"interview", "chat"} else "interview"
    try:
        room = RealtimeLiveRoom.create(
            item_id=body.item_id,
            question=body.question,
            save_recording=body.save_recording,
            mode=mode,
            assessment_session_id=body.assessment_session_id,
        )
    except RuntimeError as exc:
        raise ServiceError(503, "live_unavailable", str(exc)) from exc
    live_debug(
        "room_created",
        room_id=room.room_id,
        item_id=body.item_id,
        mode=mode,
        save_recording=body.save_recording,
        assessment_session_id=body.assessment_session_id or None,
    )
    return CreateRoomResponse(
        room_id=room.room_id, ws_path=f"/ws/live/{room.room_id}", mode=mode
    )


@app.get(
    "/api/live/rooms/{room_id}",
    tags=["live"],
    summary="Room status, turns and the finished transcript package",
)
async def get_room(room_id: str) -> dict:
    room = RealtimeLiveRoom.get(room_id)
    if room is None:
        raise ServiceError(404, "room_unknown", f"no room {room_id}")
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
async def live_ws(websocket: WebSocket, room_id: str) -> None:
    """The duplex bridge: candidate PCM up, interviewer PCM down, events as JSON."""
    room = RealtimeLiveRoom.get(room_id)
    if room is None:
        # Accept first, so the browser gets a JSON error rather than a bare handshake
        # failure it can only report as "connection closed".
        await websocket.accept()
        live_debug("ws_room_missing", room_id=room_id)
        try:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "error": f"room {room_id} not found — refresh and start again",
                    }
                )
            )
        finally:
            await websocket.close(code=4404)
        return
    await websocket.accept()

    async def pump_out() -> None:
        async for event in room.events():
            if event.get("type") == "audio":
                # Binary frame = interviewer PCM16 @ 24 kHz
                await websocket.send_bytes(event["data"])
            else:
                await websocket.send_text(json.dumps(event))
            if event.get("type") in {"done", "error"}:
                break

    out_task = asyncio.create_task(pump_out())
    try:
        live_debug("ws_accepted", room_id=room_id)
        if room.state.status in {"finished", "error"} and room._closed:
            await websocket.send_text(
                json.dumps(
                    {
                        "type": "error",
                        "error": "room already ended — refresh and start again",
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
                # The browser reports its real AudioContext rate. A rate that is not a
                # multiple of 16 kHz used to produce silently untranscribable audio; log it
                # so that failure is never invisible again.
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
            except Exception:
                logger.debug("finish on disconnect failed", exc_info=True)
    except Exception as exc:  # noqa: BLE001 - a websocket must report and close, not raise
        live_debug_exc("ws_failed", exc, room_id=room_id)
        logger.exception("live ws failed room=%s", room_id)
        room.state.status = "error"
        room.state.error = str(exc)
        await room._close_transport()
        try:
            await websocket.send_text(json.dumps({"type": "error", "error": str(exc)}))
        except Exception:
            logger.debug("could not send terminal websocket error", exc_info=True)
    finally:
        out_task.cancel()
        try:
            await out_task
        except asyncio.CancelledError:
            pass
        # Close the trace even when the client drops mid-error, or a span stays open
        # forever and the session it belongs to never completes in the dashboard.
        try:
            room._close_live_trace()
        except Exception:
            logger.debug("could not close live trace", exc_info=True)
        live_debug(
            "ws_closed",
            room_id=room_id,
            status=room.state.status,
            error=room.state.error or None,
        )


@app.get("/interview", tags=["pages"], summary="The interview page, embedded in an iframe")
async def interview_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "interview.html")


@app.get("/chat", tags=["pages"], summary="A free-form Live smoke page. Not an assessment.")
async def chat_page() -> FileResponse:
    return FileResponse(STATIC_DIR / "chat.html")


if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
