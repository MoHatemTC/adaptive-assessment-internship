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
from app.services import observability
from app.services.observability import LiveHandle
from app.services.voice.language import looks_non_english
from app.services.voice.prompts import INTERVIEWER_SYSTEM, TURN_TAKING_DIRECTOR
from app.services.voice_live.audio_codec import (
    LIVE_INPUT_RATE,
    LIVE_OUTPUT_RATE,
    pcm16_to_wav_bytes,
    wav_bytes_to_pcm16,
)
from app.services.voice_live.gemini_live import InterviewTurnAudio, LiveInterviewResult
from app.services.voice_live.litellm_realtime import AsyncLiteLLMLiveSession
from app.services.voice_live.transcribe import transcribe_audio_bytes

logger = logging.getLogger(__name__)

_READY_ACK = re.compile(r"^\s*ready[.!]?\s*$", re.IGNORECASE)


# How long to wait for a turn's TRANSCRIPT after its audio has finished. Gemini Live
# sometimes sends the two out of order; without this the question is recorded untexted and
# its transcript reappears after the answer.
TEXT_AFTER_AUDIO_GRACE_SECONDS = 2.5

# How long to keep listening after a transcript CHUNK. The transcript does not arrive as
# one message: a question came through as "You're facing" and then, separately, "an
# intermittent bug that only shows up in production under load…". Closing the turn on the
# first chunk records a fragment as the whole question and leaves the remainder to be
# collected during the NEXT turn — so the transcript reads
# `question-fragment / answer / question-remainder / answer`, which is the bug the grace
# window was added to fix, one chunk further along.
#
# Short, because it is paid on every turn once the text has started arriving. The full
# grace above still applies while there is no text at all.
TEXT_CHUNK_QUIET_SECONDS = 0.6


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
        self._english_nudge_sent = False
        self._live_trace: LiveHandle | None = None
        self._finished_result: LiveInterviewResult | None = None
        self.assessment_session_id: str = ""

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _submit(self, coro, *, timeout: float = 180.0) -> Any:
        fut: Future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    @property
    def configured(self) -> bool:
        return bool(
            settings.litellm_api_key.strip() and settings.litellm_base_url.strip()
        )

    @property
    def model(self) -> str:
        return settings.litellm_live_preview_model

    def start_interview(
        self,
        item_id: str,
        question_text: str,
        *,
        assessment_session_id: str = "",
    ) -> InterviewTurnAudio:
        return self._submit(
            self._start(
                item_id,
                question_text,
                assessment_session_id=assessment_session_id,
            )
        )

    def send_candidate_audio(self, wav_bytes: bytes) -> InterviewTurnAudio:
        if self._session is None:
            raise RuntimeError("no active interview — start the interview first")
        return self._submit(self._ingest(wav_bytes))

    def finish(self) -> LiveInterviewResult:
        if self._session is None:
            if self._finished_result is not None:
                return self._finished_result
            raise RuntimeError("no active interview")
        try:
            return self._submit(self._finish())
        except Exception as exc:
            self._close_live_trace(
                outcome_status="error",
                reason_code="FINISH_FAILED",
                error=str(exc),
            )
            raise
        finally:
            self._session = None
            self.session_id = None

    def abort(self) -> None:
        if self._session is not None:
            try:
                self._submit(self._close_session(), timeout=30.0)
            except Exception:
                logger.debug("abort streamlit live session failed", exc_info=True)
        self._close_live_trace(
            outcome_status="error",
            reason_code="ABORTED",
            error="interview aborted",
        )
        self._finished_result = None
        self._session = None
        self.session_id = None

    async def _start(
        self,
        item_id: str,
        question_text: str,
        *,
        assessment_session_id: str = "",
    ) -> InterviewTurnAudio:
        await self._close_session()
        self._close_live_trace(
            outcome_status="error",
            reason_code="SUPERSEDED",
            error="replaced by new interview",
        )
        self.item_id = item_id
        self.question = question_text
        self.session_id = uuid.uuid4().hex[:12]
        self.assessment_session_id = (assessment_session_id or "").strip()
        self.turns = []
        self.interviewer_wavs = []
        self.candidate_speech_seconds = 0.0
        self._turn_counter = 0
        self._closed = False
        self._english_nudge_sent = False
        self._finished_result = None
        self._live_trace = observability.start_live(
            model=settings.litellm_live_preview_model,
            room_id=self.session_id,
            item_id=item_id,
            assessment_session_id=self.assessment_session_id or None,
            mode="interview",
            question=question_text,
            via="streamlit_native",
        )

        system = (
            INTERVIEWER_SYSTEM
            + "\n"
            + TURN_TAKING_DIRECTOR.format(
                pause_ms=voice_settings.thinking_pause_ms,
                turn_end_ms=voice_settings.turn_end_silence_ms,
            )
            + f"\nSession nonce: {uuid.uuid4().hex[:8]}\n"
            + "Respond with natural conversational audio only after end-of-turn. "
            + "Conduct the interview in English only (HARD). "
            + "Do NOT judge language yourself — accented technical English is English. "
            + "Issue a language reminder ONLY when a DIRECTOR message says the last "
            + "utterance was NOT in English; then ask once to continue in English. "
            + f"You may ask at most {voice_settings.maximum_probes_default} short "
            + "clarifying probes, then thank them and stop."
        )
        session = AsyncLiteLLMLiveSession(
            instructions=system,
            voice=voice_settings.gemini_live_voice,
            model=settings.litellm_live_preview_model,
        )
        try:
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
        except Exception as exc:
            self._session = None
            try:
                await session.close()
            except Exception:
                logger.debug(
                    "close litellm live session after start failure", exc_info=True
                )
            self._close_live_trace(
                outcome_status="error",
                reason_code="CONNECT_FAILED",
                error=str(exc),
            )
            raise

    async def _ingest(self, wav_bytes: bytes) -> InterviewTurnAudio:
        assert self._session is not None
        pcm, duration = wav_bytes_to_pcm16(wav_bytes, LIVE_INPUT_RATE)
        self.candidate_speech_seconds += duration
        # Gemini Live's conversational transcript is useful for turn-taking, but it may
        # auto-detect the wrong language for accented technical English. Run the dedicated
        # English-hinted STT path in parallel and use it as the grading transcript.
        english_stt = asyncio.create_task(
            asyncio.to_thread(
                transcribe_audio_bytes,
                wav_bytes,
                f"{self.item_id or 'answer'}-{self._turn_counter + 1}.wav",
                language="en",
            )
        )

        self._turn_counter += 1
        cand_id = f"c{self._turn_counter}"
        # Placeholder; ASR may fill text during the model turn collect.
        self.turns.append(
            {
                "turn_id": cand_id,
                "role": "candidate",
                "text": "",
                # Unknown until a provider supplies a real score — never invent 0.85/0.95.
                "transcript_confidence": None,
            }
        )

        await self._session.append_audio(pcm)
        await self._session.commit_audio()
        try:
            reply = await self._collect_model_turn(
                role_label="interviewer", cand_id=cand_id
            )
        except Exception:
            english_stt.cancel()
            raise

        try:
            authoritative_text = (await english_stt).strip()
        except Exception as exc:  # noqa: BLE001
            authoritative_text = ""
            logger.warning(
                "English-hinted candidate transcription failed; using Live transcript (%s)",
                exc,
            )

        cand_text = ""
        for turn in reversed(self.turns):
            if turn.get("turn_id") == cand_id:
                if authoritative_text:
                    turn["text"] = authoritative_text
                    # Dedicated STT returned text but not a calibrated confidence score.
                    turn["transcript_confidence"] = None
                cand_text = str(turn.get("text") or "")
                break
        if (
            looks_non_english(cand_text)
            and not self._english_nudge_sent
            and self._session is not None
        ):
            self._english_nudge_sent = True
            await self._session.send_text(
                "DIRECTOR (not spoken to candidate): The candidate's last utterance was "
                "NOT in English per the application language detector. Do NOT thank them "
                "or end. Ask once, briefly, to continue in English, then LISTEN. Do not "
                "translate their answer. Without a DIRECTOR message like this, never "
                "raise a language issue."
            )
            reply = await self._collect_model_turn(role_label="interviewer")
        return reply

    async def _collect_model_turn(
        self, *, role_label: str, cand_id: str | None = None, timeout: float = 90.0
    ) -> InterviewTurnAudio:
        assert self._session is not None
        pcm_chunks: list[bytes] = []
        text_parts: list[str] = []
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Set once the turn is otherwise finished but its TEXT has not arrived. Gemini
        # Live can deliver a question as audio and follow it with the transcript a moment
        # later; breaking the instant audio stops means the turn is recorded with no text
        # — and the text then lands inside the NEXT collection, so the question appears
        # in the transcript AFTER the answer to it. Waiting briefly is what keeps the
        # record in the order the conversation actually happened.
        text_grace_deadline: float | None = None

        # EVERY WAIT IS BOUNDED. `events()` blocks on an unbounded queue get, so a
        # deadline checked only when an event arrives is not a deadline at all: if the
        # stream goes quiet the collector waits forever and the caller sees nothing but a
        # spinner until its own timeout fires, minutes later. Polling the queue with an
        # explicit limit — the same thing `_drain_ready` does — means the worst case is
        # `timeout`, always.
        while True:
            now = loop.time()
            limit = (
                deadline
                if text_grace_deadline is None
                else min(deadline, text_grace_deadline)
            )
            remaining = limit - now
            if remaining <= 0:
                break
            try:
                event = await asyncio.wait_for(
                    self._session._events.get(), timeout=remaining
                )
            except asyncio.TimeoutError:
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
                    if text_grace_deadline is not None:
                        # The text we were waiting for — but not necessarily all of it.
                        # Keep listening for a short quiet window instead of closing on
                        # the first chunk; a question that arrives in two parts must be
                        # recorded as one turn, before the answer to it.
                        text_grace_deadline = min(
                            loop.time() + TEXT_CHUNK_QUIET_SECONDS, deadline
                        )
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
                # `done` is not the last word on the transcript — text arrives after it,
                # and in pieces. Breaking here because SOME text has landed is what let a
                # half-transcribed question close a turn. Wait out a window instead: a
                # short one when text is already flowing, the full grace when none has
                # arrived at all.
                if not pcm_chunks and not text_parts:
                    break
                window = (
                    TEXT_CHUNK_QUIET_SECONDS
                    if text_parts
                    else TEXT_AFTER_AUDIO_GRACE_SECONDS
                )
                text_grace_deadline = min(loop.time() + window, deadline)
                continue
            elif kind in {"error", "closed"}:
                if kind == "error":
                    raise RuntimeError(event.get("message") or "LiteLLM Live error")
                break

        pcm = b"".join(pcm_chunks)
        transcript = " ".join(text_parts).strip()
        wav = pcm16_to_wav_bytes(pcm, LIVE_OUTPUT_RATE) if pcm else b""
        if wav:
            self.interviewer_wavs.append(wav)

        if transcript or wav:
            # Recorded even when the transcript never arrived. The candidate HEARD this
            # turn, so leaving it out of the record misrepresents the interview — and it
            # is what let a later text event attach itself to the wrong place.
            self._turn_counter += 1
            self.turns.append(
                {
                    "turn_id": f"i{self._turn_counter}",
                    "role": role_label,
                    "text": transcript,
                    "audio_only": not transcript,
                    "transcript_confidence": None,
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
        # Cache before closing transport. Streamlit reruns and grading retries may call
        # finish again after the realtime socket has already been closed.
        self._finished_result = result
        await self._close_session()
        self._close_live_trace(result=result)
        return result

    async def _close_session(self) -> None:
        session = self._session
        self._session = None
        if session is not None:
            try:
                await session.close()
            except Exception:
                logger.debug("close litellm live session failed", exc_info=True)

    def _close_live_trace(
        self,
        *,
        result: LiveInterviewResult | None = None,
        outcome_status: str = "",
        reason_code: str = "",
        error: str = "",
    ) -> None:
        if self._live_trace is None or self._live_trace.ended:
            return
        candidate_turns = sum(1 for t in self.turns if t.get("role") == "candidate")
        interviewer_turns = sum(1 for t in self.turns if t.get("role") == "interviewer")
        observability.end_live(
            self._live_trace,
            outcome_status=(
                result.outcome_status
                if result is not None
                else (outcome_status or ("error" if error else "unknown"))
            ),
            reason_code=(result.reason_code if result is not None else reason_code),
            error=error,
            candidate_turns=candidate_turns,
            interviewer_turns=interviewer_turns,
            speech_seconds=self.candidate_speech_seconds,
            transcript=(result.transcript if result is not None else ""),
        )
        observability.flush()
        self._live_trace = None
