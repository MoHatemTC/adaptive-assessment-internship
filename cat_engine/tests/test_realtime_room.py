"""Offline contract tests for the realtime room's transport boundary."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from cat_engine.engine.services.voice_live.realtime_room import RealtimeLiveRoom


class _ScriptedSession:
    def __init__(self, events: list[dict]) -> None:
        self._events = events
        self.sent: list[str] = []
        self.audio: list[bytes] = []
        self.commits = 0
        self.closed = False

    async def events(self):
        for event in self._events:
            yield event

    async def send_text(self, text: str) -> None:
        self.sent.append(text)

    async def append_audio(self, pcm: bytes) -> None:
        self.audio.append(pcm)

    async def commit_audio(self) -> None:
        self.commits += 1

    async def close(self) -> None:
        self.closed = True


class _RaisingEventSession(_ScriptedSession):
    async def events(self):
        raise OSError("provider receive failed")
        yield  # pragma: no cover - preserves the async-generator protocol


@pytest.mark.asyncio
async def test_clean_gateway_close_emits_a_terminal_package() -> None:
    """A normal close must not strand the WebSocket pump on an empty queue."""
    room = RealtimeLiveRoom(item_id="voice_x", question="Explain your design.")
    session = _ScriptedSession(
        [
            {"type": "text", "text": "Explain your design."},
            {
                "type": "user_transcript",
                "text": "I would isolate the boundary and retry idempotently.",
            },
            {"type": "closed"},
        ]
    )
    room._session = cast(Any, session)

    await room._receive_loop()
    terminal = await asyncio.wait_for(anext(room.events()), timeout=1.0)
    while terminal["type"] != "done":
        terminal = await asyncio.wait_for(anext(room.events()), timeout=1.0)

    assert room._closed is True
    assert session.closed is True
    assert room.state.status == "finished"
    assert room.state.result is not None
    assert "retry idempotently" in room.state.result.transcript
    assert terminal["package"]["outcome_status"] == "complete"


@pytest.mark.asyncio
async def test_receive_exception_closes_transport_and_emits_error() -> None:
    room = RealtimeLiveRoom(item_id="voice_x", question="Explain your design.")
    session = _RaisingEventSession([])
    room._session = cast(Any, session)

    await room._receive_loop()
    terminal = await asyncio.wait_for(anext(room.events()), timeout=1.0)

    assert room._closed is True
    assert room._session is None
    assert session.closed is True
    assert room.state.status == "error"
    assert terminal == {"type": "error", "error": "provider receive failed"}


@pytest.mark.asyncio
async def test_audio_is_forwarded_only_during_an_open_candidate_turn() -> None:
    room = RealtimeLiveRoom(item_id="voice_x", question="Explain your design.")
    session = _ScriptedSession([])
    room._session = cast(Any, session)
    pcm = b"\x01\x00" * 160

    await room.push_pcm(pcm)
    assert session.audio == []

    await room.activity_start()
    await room.push_pcm(pcm)
    await room.activity_end()

    assert session.audio == [pcm]
    assert session.commits == 1
    assert room.state.turn_state == "ended"


@pytest.mark.asyncio
async def test_audio_send_failure_closes_transport_and_emits_error() -> None:
    room = RealtimeLiveRoom(item_id="voice_x", question="Explain your design.")
    session = _ScriptedSession([])

    async def fail_append(_pcm: bytes) -> None:
        raise OSError("provider send failed")

    session.append_audio = fail_append
    room._session = cast(Any, session)
    await room.activity_start()

    await room.push_pcm(b"\x01\x00" * 160)
    terminal = await asyncio.wait_for(anext(room.events()), timeout=1.0)
    while terminal["type"] != "error":
        terminal = await asyncio.wait_for(anext(room.events()), timeout=1.0)

    assert room._closed is True
    assert room._session is None
    assert session.closed is True
    assert room.state.status == "error"
    assert terminal == {"type": "error", "error": "provider send failed"}


def test_room_registry_lifecycle_and_mode_normalization() -> None:
    room = RealtimeLiveRoom.create("voice_x", "Question", mode="unsupported")
    try:
        assert RealtimeLiveRoom.get(room.room_id) is room
        assert room.state.mode == "interview"
        assert room.turn_config()["gating_mode"] == "manual"
    finally:
        RealtimeLiveRoom.drop(room.room_id)
    assert RealtimeLiveRoom.get(room.room_id) is None


def test_room_registry_evicts_terminal_rooms_but_never_active_ones(monkeypatch) -> None:
    from cat_engine.engine.config.voice_settings import voice_settings

    RealtimeLiveRoom._ROOMS.clear()
    monkeypatch.setattr(voice_settings, "live_room_max_count", 1)
    old = RealtimeLiveRoom.create("voice_old", "Question")
    old.state.status = "finished"

    replacement = RealtimeLiveRoom.create("voice_new", "Question")
    assert RealtimeLiveRoom.get(old.room_id) is None
    assert RealtimeLiveRoom.get(replacement.room_id) is replacement

    with pytest.raises(RuntimeError, match="capacity"):
        RealtimeLiveRoom.create("voice_overflow", "Question")
    RealtimeLiveRoom._ROOMS.clear()


def test_filler_only_transcript_is_explicitly_unscorable() -> None:
    room = RealtimeLiveRoom(item_id="voice_x", question="Question")
    room.state.turns = [
        {"turn_id": "t0", "role": "interviewer", "text": "Ready."},
        {"turn_id": "t1", "role": "candidate", "text": "la " * 24},
    ]

    result = room._finalize_from_turns()

    assert result.outcome_status == "unscorable"
    assert result.reason_code == "NONSUBSTANTIVE_SPEECH"
    assert all(turn["text"] != "Ready." for turn in result.turns)


@pytest.mark.asyncio
async def test_connect_failure_closes_the_partial_transport(monkeypatch) -> None:
    from cat_engine.engine.config.settings import settings
    from cat_engine.engine.services.voice_live import realtime_room

    created = []

    class FailingSession:
        def __init__(self, **_kwargs) -> None:
            self.closed = False
            created.append(self)

        async def connect(self) -> None:
            raise OSError("gateway refused connection")

        async def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(settings, "litellm_api_key", "test-key")
    monkeypatch.setattr(realtime_room, "AsyncLiteLLMLiveSession", FailingSession)
    monkeypatch.setattr(
        realtime_room.observability, "start_live", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        realtime_room.observability, "end_live", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(realtime_room.observability, "flush", lambda: None)
    room = RealtimeLiveRoom(item_id="voice_x", question="Question")

    with pytest.raises(OSError, match="gateway refused"):
        await room.connect()

    assert room.state.status == "error"
    assert room._closed is True
    assert room._session is None
    assert created[0].closed is True


@pytest.mark.asyncio
async def test_opening_prompt_failure_closes_connected_transport(monkeypatch) -> None:
    from cat_engine.engine.config.settings import settings
    from cat_engine.engine.services.voice_live import realtime_room

    created = []

    class FailingOpenerSession(_ScriptedSession):
        def __init__(self, **_kwargs) -> None:
            super().__init__([])
            created.append(self)

        async def connect(self) -> None:
            return None

        async def send_text(self, _text: str) -> None:
            raise OSError("opening prompt failed")

    monkeypatch.setattr(settings, "litellm_api_key", "test-key")
    monkeypatch.setattr(realtime_room, "AsyncLiteLLMLiveSession", FailingOpenerSession)

    room = RealtimeLiveRoom(item_id="voice_x", question="Question")
    with pytest.raises(OSError, match="opening prompt failed"):
        await room.connect()

    assert room._closed is True
    assert room._session is None
    assert room._receive_task is None
    assert room.state.status == "error"
    assert created[0].closed is True


@pytest.mark.asyncio
async def test_missing_key_leaves_room_terminal_not_rejoinable(monkeypatch) -> None:
    from cat_engine.engine.config.settings import settings

    monkeypatch.setattr(settings, "litellm_api_key", "")
    room = RealtimeLiveRoom(item_id="voice_x", question="Question")

    with pytest.raises(RuntimeError, match="LITELLM_API_KEY"):
        await room.connect()

    assert room.state.status == "error"
    assert room._closed is True
