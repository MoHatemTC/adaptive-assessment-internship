"""Realtime interview rooms, as a Python API.

WHY THIS IS A PYTHON API AND NOT A SERVER

`live-voice` was the one service that genuinely needed a socket, and it is the one piece a
"no web framework" module might have been expected to lose. It does not, because the room
was never coupled to the transport. `RealtimeLiveRoom` has always been:

    await room.connect()          open the model session
    await room.push_pcm(chunk)    candidate audio in
    async for event in room.events()   interviewer audio and status out
    result = await room.finish()  the transcript

The service's WebSocket handler was sixty lines of pump between a browser socket and those
four calls. That pump is transport, so it belongs to the host — which is also the only place
that knows how its sockets are authenticated, framed and rate-limited.

A host writes roughly this:

    room = cat.live.create(item_id="q7", question="Walk me through …")
    await room.connect()
    async def out():
        async for event in room.events():
            if event["type"] == "audio":
                await ws.send_bytes(event["data"])      # interviewer PCM16 @ 24 kHz
            else:
                await ws.send_json(event)
    ...
    await room.push_pcm(await ws.receive_bytes())       # candidate PCM16 @ 16 kHz
    result = await room.finish()

THE ROOM DOES NOT GRADE

A finished room produces a transcript package; turning that into a score is the grader's
job, reached by submitting the transcript as a normal answer. Keeping the two apart is why
an audio failure can be reported as an audio failure rather than as a candidate who said
nothing.

THE BROWSER CLIENT SHIPS WITH IT

`STATIC_DIR` holds the reference interview page, its audio worklet and a free-form chat
page. The worklet must be served from the same origin as the page, so a host serves these
files rather than reimplementing them; they are packaged with the module for that reason.
"""

from __future__ import annotations

import logging
from pathlib import Path

from cat_engine.engine.config.settings import settings as engine_settings
from cat_engine.engine.config.voice_settings import voice_settings
from cat_engine.engine.services.voice_live.debug_log import clear as clear_debug
from cat_engine.engine.services.voice_live.debug_log import live_debug, snapshot
from cat_engine.engine.services.voice_live.realtime_room import RealtimeLiveRoom
from cat_engine.errors import CatError

logger = logging.getLogger(__name__)

#: The reference browser client. Serve it from the host, same origin as the page.
STATIC_DIR = Path(__file__).resolve().parent / "static"

__all__ = ["STATIC_DIR", "Live", "RealtimeLiveRoom", "RoomUnknown"]


class RoomUnknown(CatError):
    """No such interview room — it ended, was pruned, or never existed."""

    status_code = 404
    code = "room_unknown"


class LiveUnavailable(CatError):
    """Realtime audio is not configured or the model session could not be opened."""

    status_code = 503
    code = "live_unavailable"


class Live:
    """Interview rooms for one module instance.

    Rooms are process-local and pruned on an idle timeout, exactly as they were in the
    service. A room holds audio, turns and a transcript; it holds no posterior and it
    cannot end an assessment — which is what makes losing one cost a candidate the
    interview they were in rather than the session.
    """

    def __init__(self, settings=None) -> None:
        from cat_engine.settings import ModuleSettings

        self.settings = settings if settings is not None else ModuleSettings()

    def create(
        self,
        item_id: str,
        question: str,
        *,
        assessment_session_id: str = "",
        mode: str = "interview",
        save_recording: bool = False,
    ) -> RealtimeLiveRoom:
        """Open a room. Pass `assessment_session_id` so the trace groups with the session."""
        mode = mode if mode in {"interview", "chat"} else "interview"
        try:
            room = RealtimeLiveRoom.create(
                item_id=item_id,
                question=question,
                save_recording=save_recording,
                mode=mode,
                assessment_session_id=assessment_session_id,
            )
        except RuntimeError as exc:
            raise LiveUnavailable(str(exc)) from exc
        live_debug(
            "room_created",
            room_id=room.room_id,
            item_id=item_id,
            mode=mode,
            save_recording=save_recording,
            assessment_session_id=assessment_session_id or None,
        )
        return room

    @staticmethod
    def get(room_id: str) -> RealtimeLiveRoom:
        room = RealtimeLiveRoom.get(room_id)
        if room is None:
            raise RoomUnknown(f"no room {room_id}")
        return room

    @staticmethod
    def status(room_id: str) -> dict:
        """Status, turns and the finished transcript, without holding the room object."""
        room = Live.get(room_id)
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

    @staticmethod
    def drop(room_id: str) -> None:
        RealtimeLiveRoom.drop(room_id)

    @staticmethod
    def prune(*, now: float | None = None) -> int:
        return RealtimeLiveRoom.prune(now=now)

    @staticmethod
    def config() -> dict:
        """Turn-taking and gating parameters, for a client that drives the socket.

        Chat always uses manual markers — server VAD is broken on the Live preview model.
        """
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

    def debug_feed(self, limit: int = 50) -> list:
        """Recent room events. Off by default — it carries candidate transcripts."""
        if not self.settings.live_debug_api_enabled:
            raise RoomUnknown("the live debug feed is disabled (LIVE_DEBUG_API_ENABLED)")
        return snapshot(limit=max(1, min(limit, 200)))

    def clear_debug_feed(self) -> None:
        if not self.settings.live_debug_api_enabled:
            raise RoomUnknown("the live debug feed is disabled (LIVE_DEBUG_API_ENABLED)")
        clear_debug()
