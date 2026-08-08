"""FastAPI entrypoint for realtime Gemini Live interviews."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from functools import cache
from pathlib import Path

import numpy as np
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config.settings import settings
from app.config.voice_settings import voice_settings
from app.schemas.orchestration import AssessmentState, BankItem, GradedResponse
from app.services import observability
from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository
from app.services.orchestrator import registry, session_dump
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator
from app.services.voice.evaluator import evaluate as evaluate_voice
from app.services.voice.evaluator import package_from_text
from app.services.voice_live.debug_log import clear as clear_live_debug
from app.services.voice_live.debug_log import live_debug, live_debug_exc, snapshot
from app.services.voice_live.realtime_room import RealtimeLiveRoom
from app.services.voice_live.transcribe import transcribe_audio_bytes

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parents[1] / "static"


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Live rooms need Langfuse configured in this process (separate from Streamlit).
    observability.configure()
    yield


app = FastAPI(
    title="Voice assessment Live interviewer",
    version="0.1.0",
    lifespan=_lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    # This helper has no cookie-authenticated API. Wildcard origins plus credentials is
    # invalid browser CORS policy and risks reflecting ambient credentials if auth is
    # added later. Public non-credentialed access is the actual contract.
    allow_credentials=False,
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
    # CAT assessment id from Streamlit — groups gemini-live with picker/grader traces.
    assessment_session_id: str = ""


class CreateRoomResponse(BaseModel):
    room_id: str
    ws_path: str
    mode: str = "interview"


class CreateCatSessionRequest(BaseModel):
    # Which registered bank to assess against. None uses `settings.active_bank`.
    bank_id: str | None = None
    target_variables: list[str] | None = None
    intake: dict[str, int] | None = None
    confidence: dict[str, bool] | None = None
    use_llm: bool = True
    # Explicit seeds are for deterministic tests/replay. Omission draws OS entropy.
    seed: int | None = None


class CatAnswerRequest(BaseModel):
    type: str
    chosen_index: int | None = None
    code: str | None = None
    transcript: str | None = None
    use_llm: bool = True
    # Retained for wire compatibility; selection uses the generator persisted at create.
    seed: int | None = None


_cat_code_engine = CodeAdaptiveSession(JsonQuestionRepository())
# The bank id travels with the session state, not with the request: a client that created
# a session against one bank must not be able to answer it against another, and re-sending
# the id on every call would make that possible by omission.
_cat_sessions: dict[str, tuple[str, AssessmentState]] = {}
_cat_rngs: dict[str, np.random.Generator] = {}
_cat_locks: dict[str, asyncio.Lock] = {}
_finished_sessions: set[str] = set()
_cat_last_access: dict[str, float] = {}
_cat_finished_at: dict[str, float] = {}


@cache
def _orchestrator_for(bank_id: str) -> Orchestrator:
    """One orchestrator per bank, holding that bank's graph and coverage policy."""
    return Orchestrator(
        registry.get_bank(bank_id),
        GraderAgent(code_engine=_cat_code_engine),
        graph=registry.get_graph_service(bank_id),
        coverage_critical_only=registry.profile(bank_id).coverage_critical_only,
        bank_id=bank_id,
    )


def _session(session_id: str) -> tuple[str, Orchestrator, AssessmentState]:
    _prune_cat_sessions()
    entry = _cat_sessions.get(session_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="session not found")
    bank_id, state = entry
    _cat_last_access[session_id] = time.time()
    return bank_id, _orchestrator_for(bank_id), state


def _drop_cat_session(session_id: str) -> bool:
    existed = _cat_sessions.pop(session_id, None) is not None
    _cat_rngs.pop(session_id, None)
    _cat_locks.pop(session_id, None)
    _finished_sessions.discard(session_id)
    _cat_last_access.pop(session_id, None)
    _cat_finished_at.pop(session_id, None)
    return existed


def _prune_cat_sessions(*, now: float | None = None, reserve_slot: bool = False) -> int:
    """Bound process-local state without ever evicting an assessment in progress."""
    now = time.time() if now is None else now
    expired = [
        session_id
        for session_id, finished_at in _cat_finished_at.items()
        if now - finished_at >= settings.cat_session_retention_seconds
    ]
    removed = sum(_drop_cat_session(session_id) for session_id in expired)

    finished = sorted(
        _cat_finished_at,
        key=lambda session_id: _cat_last_access.get(session_id, 0.0),
    )

    def over_capacity() -> bool:
        count = len(_cat_sessions)
        maximum = settings.cat_max_retained_sessions
        return count >= maximum if reserve_slot else count > maximum

    while over_capacity() and finished:
        removed += _drop_cat_session(finished.pop(0))
    return removed


def _session_rng(session_id: str) -> np.random.Generator:
    """The one exposure-control stream owned by this assessment session."""
    rng = _cat_rngs.get(session_id)
    if rng is None:
        # Backward-compatible recovery for an in-memory session created before a hot
        # reload introduced persisted RNGs. Never fall back to a constant seed.
        rng = np.random.default_rng()
        _cat_rngs[session_id] = rng
    return rng


def _ui_item(item: BankItem) -> dict:
    payload = item.payload or {}
    base = {
        "item_id": item.item_id,
        "modality": item.modality,
        "competency": item.competency,
        "sub_competency": item.sub_competency,
        "estimated_time_seconds": item.expected_seconds,
    }
    if item.modality == "mcq":
        base.update(
            {
                "stem": payload.get("stem") or payload.get("question") or "",
                "options": payload.get("options") or [],
            }
        )
    elif item.modality == "code":
        base.update(
            {
                "prompt": payload.get("prompt") or payload.get("question") or "",
                "language": payload.get("language", "python"),
                "function_name": payload.get("function_name", "solve"),
                # Candidate-facing payload. `reference_solution` is grader-only and must
                # never cross this boundary; doing so turns every code item into an answer
                # key. Banks may provide a deliberately incomplete starter scaffold.
                "starter_code": payload.get("starter_code", ""),
            }
        )
    elif item.modality in ("open", "voice"):
        base.update(
            {
                "question": payload.get("question") or payload.get("prompt") or "",
                "answer_format": payload.get("answer_format") or "spoken",
                # Voice is answered by speaking; open may be typed. The client needs to
                # know which without inspecting the payload.
                "spoken": item.modality == "voice",
            }
        )
    return base


def _cat_state_response(
    state: AssessmentState,
    orchestrator: Orchestrator,
    bank_id: str,
    *,
    stop_reason: str = "",
    last_graded: dict | None = None,
) -> dict:
    stop, reason = orchestrator.should_stop(state)
    reason = stop_reason or reason
    presenting = None
    pair = orchestrator.next_item(state)
    if pair is not None:
        item, candidate = pair
        presenting = {
            "variable": candidate.variable,
            "criterion": candidate.criterion,
            "item": _ui_item(item),
        }
    report = orchestrator.summarise(state, reason).model_dump() if stop else None
    if stop and state.session_id not in _finished_sessions:
        # Once per session: a poll of GET /sessions/{id} must not append it again.
        _finished_sessions.add(state.session_id)
        _cat_finished_at[state.session_id] = time.time()
        session_dump.record_session(state, bank_id, stop_reason=reason)
    return {
        "session_id": state.session_id,
        "bank_id": bank_id,
        "stop": stop,
        "stop_reason": reason if stop else "",
        "items_administered": state.items_administered,
        "open_variables": state.open_variables,
        "presenting": presenting,
        "last_graded": last_graded,
        "report": report,
    }


def _candidate_grade_receipt(graded: GradedResponse) -> dict:
    """Minimal acknowledgement safe to return while the assessment is still active.

    ``GradedResponse.detail`` is an internal audit record. For MCQ it contains the correct
    answer index; for code it can contain hidden-test diagnostics. Returning it after each
    answer lets a client harvest the bank and lets grading feedback coach later responses,
    changing the construct mid-session. Detailed evidence remains in state/session dumps
    and the final assessor-facing tooling, not this candidate boundary.
    """
    return {
        "item_id": graded.item_id,
        "modality": graded.modality,
        "accepted": True,
        "flags": [
            flag for flag in graded.flags if str(flag).startswith("INFRASTRUCTURE_")
        ],
    }


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/api/live/debug")
async def live_debug_feed(limit: int = 50):
    if not voice_settings.live_debug_api_enabled:
        raise HTTPException(status_code=404, detail="not found")
    return {"logs": snapshot(limit=max(1, min(limit, 200)))}


@app.delete("/api/live/debug")
async def live_debug_clear():
    if not voice_settings.live_debug_api_enabled:
        raise HTTPException(status_code=404, detail="not found")
    clear_live_debug()
    return {"ok": True}


@app.get("/api/live/config")
async def live_config():
    from app.config.settings import settings
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
        "model": settings.litellm_live_preview_model,
        "via": "litellm",
    }


@app.get("/api/banks")
async def list_banks():
    """Registered banks, so a client discovers them instead of hardcoding one."""
    return {"active": settings.active_bank, "banks": registry.describe()}


@app.post("/api/cat/sessions")
async def create_cat_session(body: CreateCatSessionRequest):
    try:
        bank_id = registry.resolve_bank_id(body.bank_id)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    orchestrator = _orchestrator_for(bank_id)
    targets = body.target_variables or registry.get_bank(bank_id).variables()
    try:
        state = orchestrator.begin(
            targets,
            intake=body.intake or {},
            confidence=body.confidence or {},
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    rng = np.random.default_rng(body.seed)
    state = await orchestrator.fill_queue(
        state,
        use_llm=body.use_llm,
        rng=rng,
    )
    state = orchestrator.ensure_presenting(state)
    _prune_cat_sessions(reserve_slot=True)
    if len(_cat_sessions) >= settings.cat_max_retained_sessions:
        raise HTTPException(
            status_code=503,
            detail="assessment session capacity reached; retry after a session finishes",
        )
    _cat_sessions[state.session_id] = (bank_id, state)
    _cat_rngs[state.session_id] = rng
    _cat_locks[state.session_id] = asyncio.Lock()
    _cat_last_access[state.session_id] = time.time()
    return _cat_state_response(state, orchestrator, bank_id)


@app.get("/api/cat/sessions/{session_id}")
async def get_cat_session(session_id: str):
    bank_id, orchestrator, state = _session(session_id)
    state = orchestrator.ensure_presenting(state)
    _cat_sessions[session_id] = (bank_id, state)
    return _cat_state_response(state, orchestrator, bank_id)


@app.delete("/api/cat/sessions/{session_id}")
async def delete_cat_session(session_id: str):
    lock = _cat_locks.get(session_id)
    if lock is None:
        raise HTTPException(status_code=404, detail="session not found")
    async with lock:
        if not _drop_cat_session(session_id):
            raise HTTPException(status_code=404, detail="session not found")
    return {"ok": True}


@app.post("/api/cat/sessions/{session_id}/answer")
async def submit_cat_answer(
    session_id: str,
    request: Request,
    type_form: str | None = Form(default=None, alias="type"),
    chosen_index_form: int | None = Form(default=None, alias="chosen_index"),
    code_form: str | None = Form(default=None, alias="code"),
    transcript_form: str | None = Form(default=None, alias="transcript"),
    use_llm_form: bool | None = Form(default=None, alias="use_llm"),
    seed_form: int | None = Form(default=None, alias="seed"),
    audio: UploadFile | None = File(default=None),  # noqa: B008 - FastAPI dependency marker
):
    # Capture which item this request is answering BEFORE it waits. Two concurrent POSTs
    # can otherwise both read the same state, grade the same presentation, then race to
    # overwrite the session. Serializing alone is insufficient: the second request would
    # wake up and apply its old answer to the next item whenever the modality matched.
    _bank_id, snapshot_orchestrator, snapshot = _session(session_id)
    snapshot = snapshot_orchestrator.ensure_presenting(snapshot)
    snapshot_pair = snapshot_orchestrator.next_item(snapshot)
    expected_item_id = snapshot_pair[0].item_id if snapshot_pair is not None else None

    lock = _cat_locks.setdefault(session_id, asyncio.Lock())
    async with lock:
        _current_bank, current_orchestrator, current = _session(session_id)
        current = current_orchestrator.ensure_presenting(current)
        current_pair = current_orchestrator.next_item(current)
        current_item_id = current_pair[0].item_id if current_pair is not None else None
        if current_item_id != expected_item_id:
            raise HTTPException(
                status_code=409,
                detail="stale answer: the presented item changed while this request waited",
            )
        return await _submit_cat_answer_unlocked(
            session_id=session_id,
            request=request,
            type_form=type_form,
            chosen_index_form=chosen_index_form,
            code_form=code_form,
            transcript_form=transcript_form,
            use_llm_form=use_llm_form,
            seed_form=seed_form,
            audio=audio,
        )


async def _submit_cat_answer_unlocked(
    *,
    session_id: str,
    request: Request,
    type_form: str | None,
    chosen_index_form: int | None,
    code_form: str | None,
    transcript_form: str | None,
    use_llm_form: bool | None,
    seed_form: int | None,
    audio: UploadFile | None,
):
    bank_id, orchestrator, state = _session(session_id)

    state = orchestrator.ensure_presenting(state)
    pair = orchestrator.next_item(state)
    if pair is None:
        _cat_sessions[session_id] = (bank_id, state)
        return _cat_state_response(state, orchestrator, bank_id)
    item, _candidate = pair

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        body = CatAnswerRequest.model_validate(await request.json())
        answer_type = body.type
        chosen_index = body.chosen_index
        code = body.code
        transcript = body.transcript
        use_llm = body.use_llm
        seed = body.seed
    else:
        answer_type = type_form or item.modality
        chosen_index = chosen_index_form
        code = code_form
        transcript = transcript_form
        use_llm = True if use_llm_form is None else use_llm_form
        seed = seed_form

    if seed is not None:
        logger.warning(
            "answer-level seed ignored for session %s; seed the session at creation",
            session_id,
        )

    if answer_type != item.modality:
        raise HTTPException(
            status_code=400,
            detail=f"answer type mismatch: expected {item.modality}, got {answer_type}",
        )

    if item.modality == "mcq":
        if chosen_index is None:
            raise HTTPException(
                status_code=400, detail="chosen_index is required for mcq"
            )
        new_state, graded = orchestrator.record_response(state, item, chosen_index)
    elif item.modality == "code":
        if not code:
            raise HTTPException(
                status_code=400, detail="code is required for code modality"
            )
        new_state, graded = orchestrator.record_response(state, item, code)
    elif item.modality in ("open", "voice"):
        transcript_text = (transcript or "").strip()
        filename = "answer.wav"
        if audio is not None:
            audio_bytes = await audio.read()
            if not audio_bytes:
                raise HTTPException(status_code=400, detail="empty audio payload")
            filename = audio.filename or filename
            transcript_text = transcribe_audio_bytes(
                audio_bytes, filename=filename
            ).strip()
        if not transcript_text:
            raise HTTPException(
                status_code=400,
                detail=f"transcript or audio file is required for {item.modality} modality",
            )
        package = package_from_text(item.item_id, transcript_text)
        graded_voice = await evaluate_voice(item, package, use_llm=use_llm)
        new_state, graded = orchestrator.record_response(state, item, graded_voice)
    else:  # pragma: no cover
        raise HTTPException(
            status_code=400, detail=f"unsupported modality {item.modality}"
        )

    new_state = await orchestrator.after_response(
        new_state,
        item,
        use_llm=use_llm,
        rng=_session_rng(session_id),
    )
    new_state = orchestrator.ensure_presenting(new_state)
    _cat_sessions[session_id] = (bank_id, new_state)
    return _cat_state_response(
        new_state,
        orchestrator,
        bank_id,
        last_graded=_candidate_grade_receipt(graded),
    )


@app.post("/api/live/rooms", response_model=CreateRoomResponse)
async def create_room(body: CreateRoomRequest):
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
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    live_debug(
        "room_created",
        room_id=room.room_id,
        item_id=body.item_id,
        mode=mode,
        save_recording=body.save_recording,
        assessment_session_id=body.assessment_session_id or None,
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
                live_debug(
                    "ws_activity_start", room_id=room_id, turn=room.state.turn_state
                )
                await room.activity_start()
            elif kind == "activity_end":
                live_debug(
                    "ws_activity_end", room_id=room_id, turn=room.state.turn_state
                )
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
            except Exception:
                logger.debug("finish on disconnect failed", exc_info=True)
    except Exception as exc:
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
        # Ensure a Live span is closed even when the client drops mid-error.
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


@app.get("/")
async def index():
    """Live helper service status — the full CAT UI is Streamlit, not this page."""
    from app.config.settings import settings
    from app.config.voice_settings import voice_settings

    return {
        "service": "live-interviewer-helper",
        "ok": True,
        "note": (
            "This FastAPI process only hosts Live WebSocket rooms + /interview iframe. "
            "Run the full adaptive engine with: streamlit run streamlit/main.py"
        ),
        "streamlit": "streamlit run streamlit/main.py",
        "interview_iframe": "/interview",
        "health": "/health",
        "litellm_model": settings.litellm_model,
        "litellm_live_preview_model": settings.litellm_live_preview_model,
        "live_server": voice_settings.live_server_base,
        "live_public": voice_settings.live_public_base,
    }


@app.get("/chat")
async def chat_page():
    """Optional free-form Live chat smoke page (not the CAT engine)."""
    return FileResponse(STATIC_DIR / "chat.html")


@app.get("/interview")
async def interview_page():
    """Assessment interview UI (embedded by Streamlit for open/voice items)."""
    return FileResponse(STATIC_DIR / "interview.html")


@app.get("/cat")
async def cat_page():
    """Legacy static CAT page — prefer Streamlit for the full engine."""
    return FileResponse(STATIC_DIR / "cat.html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
