"""Thread-owned Gemini Live bridge for Streamlit.

Streamlit runs `asyncio.run()` per interaction, which destroys the event loop.
The Live WebSocket must therefore live on a dedicated background loop so the
interviewer↔candidate conversation stays continuous across mic turns.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import Future
from typing import Any

from app.services.voice_live.gemini_live import (
    ConversationalLiveSession,
    GeminiLiveInterviewer,
    InterviewTurnAudio,
    LiveInterviewResult,
)

logger = logging.getLogger(__name__)


class LiveInterviewBridge:
    """Sync façade over a background asyncio Live session."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="gemini-live-bridge", daemon=True
        )
        self._thread.start()
        self._interviewer = GeminiLiveInterviewer()
        self._session: ConversationalLiveSession | None = None
        self.session_id: str | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro) -> Any:
        fut: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=180)

    @property
    def configured(self) -> bool:
        return self._interviewer.configured

    @property
    def model(self) -> str:
        return self._interviewer.model

    def smoke_connect(self) -> dict:
        return self._submit(self._interviewer.smoke_connect())

    def start_interview(self, item_id: str, question_text: str) -> InterviewTurnAudio:
        async def _start() -> InterviewTurnAudio:
            if self._session is not None:
                await self._session._close()
                ConversationalLiveSession.drop(self._session.session_id)
            session = ConversationalLiveSession(
                self._interviewer, item_id=item_id, question_text=question_text
            )
            await session.start()
            audio = await session.prompt_question()
            ConversationalLiveSession.store(session)
            self._session = session
            self.session_id = session.session_id
            audio.session_id = session.session_id  # type: ignore[attr-defined]
            return audio

        return self._submit(_start())

    def send_candidate_audio(self, wav_bytes: bytes) -> InterviewTurnAudio:
        if self._session is None:
            raise RuntimeError("no active interview — click Start live interview first")
        return self._submit(self._session.ingest_candidate_wav(wav_bytes))

    def finish(self) -> LiveInterviewResult:
        if self._session is None:
            raise RuntimeError("no active interview")
        session = self._session

        async def _finish() -> LiveInterviewResult:
            try:
                return await session.finish()
            finally:
                ConversationalLiveSession.drop(session.session_id)

        try:
            return self._submit(_finish())
        finally:
            self._session = None
            self.session_id = None

    def abort(self) -> None:
        if self._session is None:
            return
        session = self._session

        async def _abort() -> None:
            await session._close()
            ConversationalLiveSession.drop(session.session_id)

        try:
            self._submit(_abort())
        except Exception:
            logger.debug("abort live session failed", exc_info=True)
        finally:
            self._session = None
            self.session_id = None
