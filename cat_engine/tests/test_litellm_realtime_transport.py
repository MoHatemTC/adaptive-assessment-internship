"""Protocol and lifecycle contracts for the LiteLLM realtime transport."""

from __future__ import annotations

import asyncio
import json

import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.voice_live import litellm_realtime
from cat_engine.engine.services.voice_live.litellm_realtime import AsyncLiteLLMLiveSession


class _FakeWebSocket:
    def __init__(
        self, incoming: list[dict] | None = None, *, fail_send: bool = False
    ) -> None:
        self.incoming: asyncio.Queue = asyncio.Queue()
        for event in incoming or []:
            self.incoming.put_nowait(json.dumps(event))
        self.sent: list[str] = []
        self.closed = False
        self.fail_send = fail_send

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.incoming.get()

    async def send(self, message: str) -> None:
        if self.fail_send:
            raise OSError("socket write failed")
        self.sent.append(message)

    async def close(self) -> None:
        self.closed = True


def _configured(monkeypatch) -> None:
    monkeypatch.setattr(settings, "litellm_api_key", "test-key")
    monkeypatch.setattr(settings, "litellm_base_url", "https://gateway.example")


@pytest.mark.asyncio
async def test_successful_handshake_sends_configuration_and_cleans_up(
    monkeypatch,
) -> None:
    _configured(monkeypatch)
    websocket = _FakeWebSocket([{"type": "session.created"}])

    async def fake_connect(*_args, **_kwargs):
        return websocket

    monkeypatch.setattr(litellm_realtime.websockets, "connect", fake_connect)
    session = AsyncLiteLLMLiveSession(
        instructions="Interview in English.", connect_timeout=0.2
    )

    await session.connect()
    await asyncio.sleep(0)

    assert session.connected is True
    assert json.loads(websocket.sent[0])["type"] == "session.update"
    assert json.loads(websocket.sent[1])["type"] == "conversation.item.create"

    await session.close()
    assert websocket.closed is True
    assert session.connected is False
    assert all(
        task is None or task.done()
        for task in (session._reader_task, session._writer_task)
    )


@pytest.mark.asyncio
async def test_handshake_timeout_closes_socket_and_tasks_promptly(monkeypatch) -> None:
    _configured(monkeypatch)
    websocket = _FakeWebSocket()

    async def fake_connect(*_args, **_kwargs):
        return websocket

    monkeypatch.setattr(litellm_realtime.websockets, "connect", fake_connect)
    session = AsyncLiteLLMLiveSession(instructions="", connect_timeout=0.02)
    started = asyncio.get_running_loop().time()

    with pytest.raises(RuntimeError, match="Timed out"):
        await session.connect()

    assert asyncio.get_running_loop().time() - started < 0.3
    assert websocket.closed is True
    assert all(
        task is None or task.done()
        for task in (session._reader_task, session._writer_task)
    )


@pytest.mark.asyncio
async def test_writer_failure_is_emitted_instead_of_silently_dying(monkeypatch) -> None:
    _configured(monkeypatch)
    session = AsyncLiteLLMLiveSession(instructions="")
    websocket = _FakeWebSocket(fail_send=True)
    session._ws = websocket
    session._writer_task = asyncio.create_task(session._write_loop())

    await session.send_text("hello")
    event = await asyncio.wait_for(anext(session.events()), timeout=1.0)

    assert event["type"] == "error"
    assert "socket write failed" in event["message"]
    await session.close()
