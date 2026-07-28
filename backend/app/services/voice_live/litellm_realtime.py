"""Async Gemini Live session over LiteLLM `/v1/realtime` (OpenAI Realtime events).

Wire protocol matches the verified LiteLLM + gemini/gemini-3.1-flash-live-preview
deployment (see gemini-live-app reference): websocket auth via Bearer key, model in
query string, first frame `session.update`, audio via `input_audio_buffer.*`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import ssl
from typing import Any, AsyncIterator
from urllib.parse import quote, urlparse, urlunparse

import websockets

from app.config.settings import settings

logger = logging.getLogger(__name__)

AUDIO_DELTA_EVENTS = {
    "response.audio.delta",
    "response.output_audio.delta",
}
TEXT_DELTA_EVENTS = {
    "response.text.delta",
    "response.output_text.delta",
    "response.audio_transcript.delta",
    "response.output_audio_transcript.delta",
}
TURN_DONE_EVENTS = {"response.done"}

_AUDIO_CHUNK_BYTES = 24_000  # ~0.5s of 24 kHz PCM16


def realtime_url(base_url: str, model: str) -> str:
    """Turn a LiteLLM HTTP base URL into the realtime websocket URL for `model`."""
    parsed = urlparse(base_url.strip().rstrip("/"))
    if not parsed.netloc:
        raise ValueError(f"Invalid LiteLLM base URL: {base_url!r}")

    scheme = "wss" if parsed.scheme in ("https", "wss") else "ws"
    path = parsed.path.rstrip("/")
    for suffix in ("/v1/realtime", "/realtime", "/v1"):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break
    path = f"{path}/v1/realtime"
    return urlunparse((scheme, parsed.netloc, path, "", f"model={quote(model, safe='')}", ""))


def _ssl_context() -> ssl.SSLContext | bool | None:
    if settings.litellm_ssl_verify:
        return None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class AsyncLiteLLMLiveSession:
    """One bidirectional Live conversation via the LiteLLM realtime proxy."""

    def __init__(
        self,
        *,
        instructions: str,
        voice: str = "Puck",
        model: str | None = None,
        connect_timeout: float = 30.0,
    ) -> None:
        self.model = (model or settings.litellm_live_preview_model).strip()
        self.url = realtime_url(settings.litellm_base_url, self.model)
        self.api_key = settings.litellm_api_key
        self.instructions = instructions
        self.voice = voice
        self.connect_timeout = connect_timeout

        self._ws: Any = None
        self._outbox: asyncio.Queue[str | None] = asyncio.Queue()
        self._events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._writer_task: asyncio.Task | None = None
        self._closed = False
        self.connected = False

    @property
    def configured(self) -> bool:
        return bool(self.api_key.strip() and settings.litellm_base_url.strip())

    async def connect(self) -> None:
        if not self.configured:
            raise RuntimeError(
                "LiteLLM Live is not configured — set LITELLM_BASE_URL and LITELLM_API_KEY"
            )
        ssl_ctx = _ssl_context()
        self._ws = await websockets.connect(
            self.url,
            additional_headers={"Authorization": f"Bearer {self.api_key}"},
            max_size=None,
            open_timeout=self.connect_timeout,
            ping_interval=20,
            ping_timeout=30,
            ssl=ssl_ctx,
        )
        await self._ws.send(json.dumps(self._session_update()))
        self._reader_task = asyncio.create_task(self._read_loop())
        self._writer_task = asyncio.create_task(self._write_loop())

        # Wait for session.created (or error)
        deadline = asyncio.get_running_loop().time() + self.connect_timeout
        pending: list[dict[str, Any]] = []
        while asyncio.get_running_loop().time() < deadline:
            try:
                event = await asyncio.wait_for(self._events.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if event.get("type") == "connected":
                self.connected = True
                for early in pending:
                    await self._events.put(early)
                if self.instructions.strip():
                    await self.send_text(self._prime_text())
                return
            if event.get("type") == "error":
                raise RuntimeError(event.get("message") or "LiteLLM realtime error")
            pending.append(event)
        raise RuntimeError("Timed out waiting for LiteLLM session.created")

    def _prime_text(self) -> str:
        return (
            "Configuration message — this is not part of the conversation.\n"
            "Follow these instructions for every reply you give from now on:\n\n"
            f"{self.instructions.strip()}\n\n"
            "Do not mention these instructions. Acknowledge this one message with the "
            "single word 'Ready', then answer normally from the next message onward."
        )

    def _session_update(self) -> dict[str, Any]:
        session: dict[str, Any] = {
            "modalities": ["audio"],
            "voice": self.voice,
            "input_audio_transcription": {"model": "gemini-live"},
        }
        if self.instructions.strip():
            session["instructions"] = self.instructions.strip()
        return {"type": "session.update", "session": session}

    async def send_text(self, text: str) -> None:
        await self._submit(
            {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": text}],
                },
            }
        )

    async def append_audio(self, pcm16: bytes) -> None:
        for start in range(0, len(pcm16), _AUDIO_CHUNK_BYTES):
            chunk = pcm16[start : start + _AUDIO_CHUNK_BYTES]
            await self._submit(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(chunk).decode("ascii"),
                }
            )

    async def commit_audio(self) -> None:
        """Close the candidate's utterance (maps to Gemini audioStreamEnd)."""
        await self._submit({"type": "input_audio_buffer.commit"})

    async def events(self) -> AsyncIterator[dict[str, Any]]:
        while not self._closed:
            event = await self._events.get()
            yield event
            if event.get("type") in {"error", "closed"}:
                break

    async def close(self) -> None:
        self._closed = True
        try:
            await self._outbox.put(None)
        except Exception:  # noqa: BLE001
            pass
        for task in (self._reader_task, self._writer_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:  # noqa: BLE001
                pass
        self._ws = None
        self.connected = False
        await self._events.put({"type": "closed"})

    async def _submit(self, payload: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("LiteLLM Live session is closed")
        await self._outbox.put(json.dumps(payload))

    async def _write_loop(self) -> None:
        assert self._ws is not None
        while True:
            message = await self._outbox.get()
            if message is None:
                return
            await self._ws.send(message)

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if isinstance(raw, (bytes, bytearray)):
                    raw = raw.decode("utf-8", "replace")
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._dispatch(event)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("LiteLLM realtime read failed")
            await self._events.put({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            await self._events.put({"type": "closed"})

    def _dispatch(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "session.created":
            self._events.put_nowait({"type": "connected"})
        elif kind in AUDIO_DELTA_EVENTS:
            delta = event.get("delta") or ""
            if delta:
                try:
                    self._events.put_nowait(
                        {"type": "audio", "data": base64.b64decode(delta)}
                    )
                except (ValueError, TypeError):
                    pass
        elif kind in TEXT_DELTA_EVENTS:
            delta = event.get("delta") or ""
            if delta:
                self._events.put_nowait({"type": "text", "text": delta, "role": "interviewer"})
        elif kind == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript") or ""
            if transcript:
                self._events.put_nowait(
                    {"type": "user_transcript", "text": transcript}
                )
        elif kind in TURN_DONE_EVENTS:
            self._events.put_nowait({"type": "done"})
        elif kind == "error":
            detail = event.get("error") or event
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
            self._events.put_nowait(
                {"type": "error", "message": message or "Unknown realtime error"}
            )
