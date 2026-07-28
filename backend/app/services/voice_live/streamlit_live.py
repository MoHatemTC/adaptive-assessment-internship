"""Turn-based LiteLLM Live bridge for Streamlit (no FastAPI helper required).

Streamlit Cloud cannot expose a second WebSocket port, so open items use this
path: interviewer audio plays in the browser, the candidate records with
``st.audio_input``, and PCM is forwarded into the same LiteLLM realtime session.
Duplex iframe Live (``realtime_room``) remains preferred when the helper is up.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from concurrent.futures import Future
from typing import Any

from app.config.settings import settings
from app.config.voice_settings import voice_settings
from app.services.voice.prompts import INTERVIEWER_SYSTEM, TURN_TAKING_DIRECTOR
from app.services.voice_live.audio_codec import (
    LIVE_INPUT_RATE,
    LIVE_OUTPUT_RATE,
    pcm16_to_wav_bytes,
    wav_bytes_to_pcm16,
)
from app.services.voice_live.gemini_live import InterviewTurnAudio, LiveInterviewResult
from app.services.voice_live.litellm_realtime import AsyncLiteLLMLiveSession

logger = logging.getLogger(__name__)

_READY_ACK = re.compile(r"^\s*ready[.!]?\s*$", re.IGNORECASE)


class StreamlitLiteLLMLiveBridge:
    """Sync façade over a background asyncio LiteLLM Live session."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="litellm-live-bridge", daemon=True
        )
        self._thread.start()
        self._session: AsyncLiteLLMLiveSession | None = None
        self.item_id: str = ""
        self.question: str = ""
        self.session_id: str | None = None
        self.turns: list[dict] = []
        self.interviewer_wavs: list[bytes] = []
        self.candidate_speech_seconds = 0.0
        self._turn_counter = 0
        self._closed = False

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro, *, timeout: float = 180.0) -> Any:
        fut: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    @property
    def configured(self) -> bool:
        return bool(settings.litellm_api_key.strip() and settings.litellm_base_url.strip())

    @property
    def model(self) -> str:
        return settings.litellm_live_preview_model

    def start_interview(self, item_id: str, question_text: str) -> InterviewTurnAudio:
        return self._submit(self._start(item_id, question_text))

    def send_candidate_audio(self, wav_bytes: bytes) -> InterviewTurnAudio:
        if self._session is None:
            raise RuntimeError("no active interview — start the interview first")
        return self._submit(self._ingest(wav_bytes))

    def finish(self) -> LiveInterviewResult:
        if self._session is None:
            raise RuntimeError("no active interview")
        try:
            return self._submit(self._finish())
        finally:
            self._session = None
            self.session_id = None

    def abort(self) -> None:
        if self._session is None:
            return
        try:
            self._submit(self._close_session(), timeout=30.0)
        except Exception:  # noqa: BLE001
            logger.debug("abort streamlit live session failed", exc_info=True)
        finally:
            self._session = None
            self.session_id = None

    async def _start(self, item_id: str, question_text: str) -> InterviewTurnAudio:
        await self._close_session()
        self.item_id = item_id
        self.question = question_text
        self.session_id = uuid.uuid4().hex[:12]
        self.turns = []
        self.interviewer_wavs = []
        self.candidate_speech_seconds = 0.0
        self._turn_counter = 0
        self._closed = False

        system = (
            INTERVIEWER_SYSTEM
            + "\n"
            + TURN_TAKING_DIRECTOR.format(
                pause_ms=voice_settings.thinking_pause_ms,
                turn_end_ms=voice_settings.turn_end_silence_ms,
            )
            + f"\nSession nonce: {uuid.uuid4().hex[:8]}\n"
            + "Respond with natural conversational audio only after end-of-turn. "
            + "Conduct the interview in English only. "
            + f"You may ask at most {voice_settings.maximum_probes_default} short "
            + "clarifying probes, then thank them and stop."
        )
        session = AsyncLiteLLMLiveSession(
            instructions=system,
            voice=voice_settings.gemini_live_voice,
            model=settings.litellm_live_preview_model,
        )
        await session.connect()
        self._session = session

        # Drain the one-word Ready acknowledgement from session prime.
        await self._drain_ready(timeout=8.0)

        director = (
            "DIRECTOR (not spoken to candidate): Begin the interview now. "
            "Read the following assessment question clearly and naturally, then LISTEN. "
            "Do not add scoring advice.\n\n"
            f"QUESTION:\n{question_text}"
        )
        await session.send_text(director)
        return await self._collect_model_turn(role_label="interviewer")

    async def _ingest(self, wav_bytes: bytes) -> InterviewTurnAudio:
        assert self._session is not None
        pcm, duration = wav_bytes_to_pcm16(wav_bytes, LIVE_INPUT_RATE)
        self.candidate_speech_seconds += duration

        self._turn_counter += 1
        cand_id = f"c{self._turn_counter}"
        # Placeholder; ASR may fill text during the model turn collect.
        self.turns.append(
            {
                "turn_id": cand_id,
                "role": "candidate",
                "text": "",
                "transcript_confidence": 0.85,
            }
        )

        await self._session.append_audio(pcm)
        await self._session.commit_audio()
        return await self._collect_model_turn(role_label="interviewer", cand_id=cand_id)

    async def _collect_model_turn(
        self, *, role_label: str, cand_id: str | None = None, timeout: float = 90.0
    ) -> InterviewTurnAudio:
        assert self._session is not None
        pcm_chunks: list[bytes] = []
        text_parts: list[str] = []
        deadline = asyncio.get_running_loop().time() + timeout

        async for event in self._session.events():
            if asyncio.get_running_loop().time() > deadline:
                break
            kind = event.get("type")
            if kind == "audio":
                data = event.get("data") or b""
                if data:
                    pcm_chunks.append(data)
            elif kind == "text":
                piece = (event.get("text") or "").strip()
                if piece and not _READY_ACK.match(piece):
                    text_parts.append(piece)
            elif kind == "user_transcript":
                piece = (event.get("text") or "").strip()
                if piece and cand_id:
                    for turn in reversed(self.turns):
                        if turn.get("turn_id") == cand_id:
                            turn["text"] = (
                                f"{turn.get('text', '')} {piece}".strip()
                                if turn.get("text")
                                else piece
                            )
                            break
            elif kind == "done":
                if pcm_chunks or text_parts:
                    break
            elif kind in {"error", "closed"}:
                if kind == "error":
                    raise RuntimeError(event.get("message") or "LiteLLM Live error")
                break

        pcm = b"".join(pcm_chunks)
        transcript = " ".join(text_parts).strip()
        wav = pcm16_to_wav_bytes(pcm, LIVE_OUTPUT_RATE) if pcm else b""
        if wav:
            self.interviewer_wavs.append(wav)

        if transcript:
            self._turn_counter += 1
            self.turns.append(
                {
                    "turn_id": f"i{self._turn_counter}",
                    "role": role_label,
                    "text": transcript,
                    "transcript_confidence": 0.95,
                }
            )

        audio = InterviewTurnAudio(
            wav_bytes=wav,
            transcript=transcript,
            role=role_label,
            session_id=self.session_id or "",
        )
        return audio

    async def _drain_ready(self, *, timeout: float) -> None:
        assert self._session is not None
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                event = await asyncio.wait_for(self._session._events.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # No more primed events — Ready may have been audio-only.
                return
            kind = event.get("type")
            if kind == "text" and _READY_ACK.match((event.get("text") or "").strip()):
                continue
            if kind == "done":
                return
            if kind == "audio":
                # Discard Ready audio; wait for done or quiet.
                continue
            if kind in {"error", "closed"}:
                if kind == "error":
                    raise RuntimeError(event.get("message") or "LiteLLM Live error")
                return
            # Unexpected early event — put back for the next collector.
            await self._session._events.put(event)
            return

    async def _finish(self) -> LiveInterviewResult:
        candidate_bits = [
            str(t.get("text", "")).strip()
            for t in self.turns
            if t.get("role") == "candidate" and str(t.get("text", "")).strip()
        ]
        transcript = "\n".join(candidate_bits).strip()
        status = "complete" if transcript else "unscorable"
        reason = "" if transcript else "NO_CANDIDATE_SPEECH"
        result = LiveInterviewResult(
            item_id=self.item_id,
            transcript=transcript,
            turns=list(self.turns),
            total_speech_seconds=float(self.candidate_speech_seconds),
            outcome_status=status,
            reason_code=reason,
            interviewer_wavs=list(self.interviewer_wavs),
        )
        await self._close_session()
        return result

    async def _close_session(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            try:
                await session.close()
            except Exception:  # noqa: BLE001
                logger.debug("close litellm live session failed", exc_info=True)
