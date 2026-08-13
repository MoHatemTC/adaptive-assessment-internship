"""Async Gemini Live session over LiteLLM `/v1/realtime` (OpenAI Realtime events).

Wire protocol matches the verified LiteLLM + gemini/gemini-3.1-flash-live-preview
deployment (see gemini-live-app reference): websocket auth via Bearer key, model in
query string, first frame `session.update`, audio via `input_audio_buffer.*`.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import ssl
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote, urlparse, urlunparse

import websockets
from websockets.exceptions import ConnectionClosed

from cat_engine.engine.config.settings import settings

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
    return urlunparse(
        (scheme, parsed.netloc, path, "", f"model={quote(model, safe='')}", "")
    )


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
        try:
            ssl_ctx = _ssl_context()
            self._ws = await websockets.connect(
                self.url,
                additional_headers={"Authorization": f"Bearer {self.api_key}"},
                max_size=None,
                open_timeout=self.connect_timeout,
                # Gemini Live can be busy emitting / processing audio long enough that the
                # peer doesn't answer the websockets library's protocol ping before its
                # timeout. That closes a healthy interview with code 1011. The same transport
                # policy is already required by the direct Gemini Live client.
                ping_interval=None,
                ping_timeout=None,
                close_timeout=15,
                ssl=ssl_ctx,
            )
            await self._ws.send(json.dumps(self._session_update()))
            self._reader_task = asyncio.create_task(self._read_loop())
            self._writer_task = asyncio.create_task(self._write_loop())

            # Wait for session.created (or error). Bound every individual queue wait by
            # the remaining handshake budget; a fixed one-second poll made a configured
            # 100ms timeout take a full second and delayed every failure response.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self.connect_timeout
            pending: list[dict[str, Any]] = []
            while (remaining := deadline - loop.time()) > 0:
                try:
                    event = await asyncio.wait_for(
                        self._events.get(), timeout=min(1.0, remaining)
                    )
                except TimeoutError:
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
        except BaseException:
            # A failed handshake already owns a socket and two tasks in the common case.
            # Do not require the caller to close an object whose connect never succeeded.
            await self.close()
            raise

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
            "input_audio_transcription": {
                "model": "gemini-live",
                "language": "en",
                "prompt": (
                    "English technical interview. Preserve Python terminology and "
                    "programming identifiers."
                ),
            },
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
        except Exception:
            logger.debug("could not signal realtime writer shutdown", exc_info=True)
        for task in (self._reader_task, self._writer_task):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                logger.debug("realtime websocket close failed", exc_info=True)
        self._ws = None
        self.connected = False
        await self._events.put({"type": "closed"})

    async def _submit(self, payload: dict[str, Any]) -> None:
        if self._closed:
            raise RuntimeError("LiteLLM Live session is closed")
        await self._outbox.put(json.dumps(payload))

    async def _write_loop(self) -> None:
        assert self._ws is not None
        try:
            while True:
                message = await self._outbox.get()
                if message is None:
                    return
                await self._ws.send(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Without an event the task dies silently and producers keep filling an
            # outbox that nobody consumes. Surface the failure to the room immediately.
            logger.exception("LiteLLM realtime write failed")
            await self._events.put(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            )

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
        except ConnectionClosed as exc:
            # The REMOTE end hung up. Almost always a gateway keepalive expiring while the
            # candidate is reading or thinking — an infrastructure event, not a fault in
            # the interview. Reporting it as an `error` made `_collect_model_turn` raise
            # and buried a routine disconnect under a traceback, when what the caller
            # needs is to stop collecting and grade whatever was already captured.
            logger.warning(
                "LiteLLM realtime connection closed by the server (%s) — "
                "ending the turn and keeping the transcript so far",
                exc,
            )
        except Exception as exc:
            logger.exception("LiteLLM realtime read failed")
            await self._events.put(
                {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
            )
        finally:
            await self._events.put({"type": "closed"})

    def _dispatch(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "session.created":
            self._events.put_nowait({"type": "connected"})
        elif kind in AUDIO_DELTA_EVENTS:
            delta = event.get("delta") or ""
            if delta:
                with contextlib.suppress(ValueError, TypeError):
                    self._events.put_nowait(
                        {"type": "audio", "data": base64.b64decode(delta)}
                    )
        elif kind in TEXT_DELTA_EVENTS:
            delta = event.get("delta") or ""
            if delta:
                self._events.put_nowait(
                    {"type": "text", "text": delta, "role": "interviewer"}
                )
        elif kind == "conversation.item.input_audio_transcription.completed":
            transcript = event.get("transcript") or ""
            if transcript:
                self._events.put_nowait({"type": "user_transcript", "text": transcript})
        elif kind in TURN_DONE_EVENTS:
            self._events.put_nowait({"type": "done"})
        elif kind == "error":
            detail = event.get("error") or event
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
            self._events.put_nowait(
                {"type": "error", "message": message or "Unknown realtime error"}
            )
