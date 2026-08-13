"""Realtime duplex room: browser mic ↔ LiteLLM Gemini Live ↔ browser speaker.

Live audio goes through LiteLLM `/v1/realtime` (OpenAI Realtime event protocol).
Picker and open rubric grading stay on separate LiteLLM chat completions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from cat_engine.engine.config.settings import settings
from cat_engine.engine.config.voice_settings import voice_settings
from cat_engine.engine.services import observability
from cat_engine.engine.services.observability import LiveHandle
from cat_engine.engine.services.voice.language import looks_non_english
from cat_engine.engine.services.voice.prompts import (
    CHAT_SYSTEM,
    INTERVIEWER_SYSTEM,
    TURN_TAKING_DIRECTOR,
)
from cat_engine.engine.services.voice_live.audio_codec import LIVE_INPUT_RATE, pcm16_to_wav_bytes
from cat_engine.engine.services.voice_live.gemini_live import LiveInterviewResult
from cat_engine.engine.services.voice_live.litellm_realtime import AsyncLiteLLMLiveSession

logger = logging.getLogger(__name__)

RECORDINGS_DIR = Path(__file__).resolve().parents[3] / "recordings"

# LiteLLM/Gemini often emits a one-word "Ready" before the real opener; that must never
# enter the graded transcript or unblock candidate capture.
_READY_ACK = re.compile(r"^\s*ready[.!]?\s*$", re.IGNORECASE)
# Singing / filler loops (e.g. "la la la…") are not assessment evidence.
_FILLER_TOKEN = re.compile(
    r"^(?:la|da|na|ba|um+|uh+|hm+|hmm+|ah+|oh+|mm+)\W*$", re.IGNORECASE
)


@dataclass
class RealtimeRoomState:
    room_id: str
    item_id: str
    question: str
    mode: str = "interview"  # interview | chat
    save_recording: bool = False
    assessment_session_id: str = ""
    status: str = "created"  # created|connecting|live|finished|error
    turn_state: str = "idle"  # idle|listening|speaking|paused|ended|interviewer
    error: str = ""
    turns: list[dict] = field(default_factory=list)
    candidate_speech_seconds: float = 0.0
    result: LiveInterviewResult | None = None
    package: dict | None = None
    created_at: float = field(default_factory=time.time)


class RealtimeLiveRoom:
    """One continuous Live interview room via LiteLLM realtime."""

    _ROOMS: ClassVar[dict[str, RealtimeLiveRoom]] = {}

    def __init__(
        self,
        *,
        item_id: str,
        question: str,
        save_recording: bool = False,
        mode: str = "interview",
        assessment_session_id: str = "",
    ) -> None:
        self.room_id = uuid.uuid4().hex[:12]
        self.state = RealtimeRoomState(
            room_id=self.room_id,
            item_id=item_id,
            question=question,
            mode=mode if mode in {"interview", "chat"} else "interview",
            save_recording=save_recording,
            assessment_session_id=(assessment_session_id or "").strip(),
        )
        self._session: AsyncLiteLLMLiveSession | None = None
        self._out_queue: asyncio.Queue = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None
        self._candidate_pcm_chunks: list[bytes] = []
        self._bytes_forwarded = 0
        self._closed = False
        self._activity_open = False
        self._interviewer_playing = False
        self._pcm_frames = 0
        self._last_pcm_stat_at = 0.0
        self._send_lock = asyncio.Lock()
        self._use_manual_activity = True
        self._prime_draining = True
        self._question_opened = False  # True after interviewer speaks a real opener
        self._live_trace: LiveHandle | None = None
        self._english_nudge_sent = False

    @classmethod
    def create(
        cls,
        item_id: str,
        question: str,
        save_recording: bool = False,
        mode: str = "interview",
        assessment_session_id: str = "",
    ) -> RealtimeLiveRoom:
        cls.prune()
        if len(cls._ROOMS) >= voice_settings.live_room_max_count:
            raise RuntimeError(
                "live room capacity reached; retry after a room finishes"
            )
        room = cls(
            item_id=item_id,
            question=question,
            save_recording=save_recording,
            mode=mode,
            assessment_session_id=assessment_session_id,
        )
        cls._ROOMS[room.room_id] = room
        return room

    @classmethod
    def get(cls, room_id: str) -> RealtimeLiveRoom | None:
        return cls._ROOMS.get(room_id)

    @classmethod
    def drop(cls, room_id: str) -> None:
        cls._ROOMS.pop(room_id, None)

    @classmethod
    def prune(cls, *, now: float | None = None) -> int:
        """Expire old terminal rooms and evict the oldest terminal room at capacity."""
        now = time.time() if now is None else now
        terminal = [
            room
            for room in cls._ROOMS.values()
            if room.state.status in {"finished", "error"}
        ]
        expired = [
            room
            for room in terminal
            if now - room.state.created_at >= voice_settings.live_room_retention_seconds
        ]
        removed = 0
        for room in expired:
            removed += cls._ROOMS.pop(room.room_id, None) is not None

        terminal = sorted(
            (
                room
                for room in cls._ROOMS.values()
                if room.state.status in {"finished", "error"}
            ),
            key=lambda room: room.state.created_at,
        )
        while len(cls._ROOMS) >= voice_settings.live_room_max_count and terminal:
            room = terminal.pop(0)
            removed += cls._ROOMS.pop(room.room_id, None) is not None
        return removed

    def turn_config(self) -> dict:
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
            "live_model": settings.litellm_live_preview_model,
            "via": "litellm",
        }

    async def connect(self) -> None:
        if not settings.litellm_api_key.strip():
            message = "LITELLM_API_KEY is not set — required for Live interviews"
            self.state.status = "error"
            self.state.error = message
            self._closed = True
            raise RuntimeError(message)
        self.state.status = "connecting"
        self._live_trace = observability.start_live(
            model=settings.litellm_live_preview_model,
            room_id=self.room_id,
            item_id=self.state.item_id,
            assessment_session_id=self.state.assessment_session_id or None,
            mode=self.state.mode,
            question=self.state.question,
        )
        chat = self.state.mode == "chat"
        base_system = CHAT_SYSTEM if chat else INTERVIEWER_SYSTEM
        system = (
            base_system
            + "\n"
            + TURN_TAKING_DIRECTOR.format(
                pause_ms=voice_settings.thinking_pause_ms,
                turn_end_ms=voice_settings.turn_end_silence_ms,
            )
            + f"\nSession nonce: {uuid.uuid4().hex[:8]}\n"
            + "Respond with natural conversational audio. "
        )
        if chat:
            system += (
                "This is an open voice call. The client signals when the human starts "
                "and finishes each utterance. Listen while they speak; reply only after "
                "their turn ends. Keep the conversation going until they say goodbye."
            )
        else:
            system += (
                "Respond with natural conversational audio only after end-of-turn. "
                "Conduct the interview in English only (HARD RULE). "
                f"You may ask at most {voice_settings.maximum_probes_default} short clarifying "
                "probes, and only for missing required parts — then thank them and stop. "
                "Do NOT judge language yourself — accented technical English is English. "
                "Issue a language reminder ONLY when a DIRECTOR message says the last "
                "utterance was NOT in English; then ask once to continue in English. "
                "Never translate for them. "
                "If the first English answer already covers the required parts, do not "
                "probe further."
            )

        try:
            self._session = AsyncLiteLLMLiveSession(
                instructions=system,
                voice=voice_settings.gemini_live_voice,
                model=settings.litellm_live_preview_model,
            )
            await self._session.connect()
        except Exception as exc:
            self.state.status = "error"
            self.state.error = str(exc)[:500]
            self._closed = True
            transport: AsyncLiteLLMLiveSession | None = self._session
            self._session = None
            if transport is not None:
                await transport.close()
            observability.end_live(
                self._live_trace,
                outcome_status="error",
                reason_code="CONNECT_FAILED",
                error=str(exc),
            )
            observability.flush()
            raise
        self.state.status = "live"
        self.state.turn_state = "listening"
        self._receive_task = asyncio.create_task(self._receive_loop())

        if chat:
            opener = self.state.question.strip() or (
                "Greet the human warmly in one short sentence and invite them to talk "
                "about whatever they like. Then listen."
            )
            director = (
                "DIRECTOR (not spoken): Begin the voice call now. "
                f"{opener} "
                "After you greet, wait for the human to finish speaking before you reply."
            )
        else:
            director = (
                "DIRECTOR (not spoken to candidate): Begin the realtime interview now. "
                "Read the following assessment question clearly and naturally, then LISTEN. "
                "Do not speak again until the candidate's turn ends. "
                "Do not add scoring advice.\n\n"
                f"QUESTION:\n{self.state.question}"
            )
        try:
            # Drain the Ready acknowledgement; stay primed until a real opener arrives.
            await asyncio.sleep(0.6)
            # Do NOT clear _prime_draining here — wait for non-Ready interviewer text/audio
            # so early ASR hallucinations are not captured as the candidate's answer.
            assert self._session is not None
            await self._session.send_text(director)
            await self._out_queue.put(
                {
                    "type": "status",
                    "status": "live",
                    "room_id": self.room_id,
                    "mode": self.state.mode,
                    "turn_state": self.state.turn_state,
                    "turn_config": self.turn_config(),
                }
            )
        except BaseException as exc:
            self.state.status = "error"
            self.state.error = str(exc)[:500]
            await self._close_transport()
            self._close_live_trace()
            raise

    async def set_turn_state(self, turn_state: str) -> None:
        self.state.turn_state = turn_state
        await self._out_queue.put(
            {"type": "turn_state", "turn_state": turn_state, "room_id": self.room_id}
        )

    async def activity_start(self) -> None:
        if self._closed or self._session is None:
            return
        self._activity_open = True
        await self.set_turn_state("speaking")

    async def activity_end(self) -> None:
        """Candidate ended this utterance — commit audio buffer so the model replies."""
        if self._closed or self._session is None:
            return
        if self._activity_open:
            self._activity_open = False
            async with self._send_lock:
                await self._session.commit_audio()
            await self.set_turn_state("ended")
        else:
            await self.set_turn_state("ended")

    async def mark_pause(self) -> None:
        if self.state.turn_state == "speaking":
            await self.set_turn_state("paused")

    async def mark_resume(self) -> None:
        if self.state.turn_state in {"paused", "ended", "listening", "idle"}:
            if not self._activity_open:
                await self.activity_start()
            else:
                await self.set_turn_state("speaking")

    async def push_pcm(self, pcm: bytes) -> None:
        if self._closed or self._session is None or not pcm:
            return
        self._bytes_forwarded += len(pcm)
        self._pcm_frames += 1
        self.state.candidate_speech_seconds += len(pcm) / (2.0 * LIVE_INPUT_RATE)
        if self.state.save_recording:
            self._candidate_pcm_chunks.append(pcm)
        if not self._activity_open:
            return
        try:
            async with self._send_lock:
                await self._session.append_audio(pcm)
            now = time.monotonic()
            if now - self._last_pcm_stat_at >= 2.0:
                self._last_pcm_stat_at = now
                from cat_engine.engine.services.voice_live.debug_log import live_debug

                peak = 0
                if len(pcm) >= 2:
                    view = memoryview(pcm).cast("h")
                    peak = max(abs(s) for s in view) if len(view) else 0
                live_debug(
                    "pcm_stats",
                    room_id=self.room_id,
                    frames=self._pcm_frames,
                    bytes_fwd=self._bytes_forwarded,
                    speech_s=round(self.state.candidate_speech_seconds, 2),
                    mode=self.state.mode,
                    peak=peak,
                    activity_open=self._activity_open,
                    via="litellm",
                )
        except Exception as exc:  # noqa: BLE001
            from cat_engine.engine.services.voice_live.debug_log import live_debug_exc

            live_debug_exc("push_pcm_failed", exc, room_id=self.room_id)
            self.state.status = "error"
            self.state.error = str(exc)
            self._closed = True
            transport, self._session = self._session, None
            if transport is not None:
                await transport.close()
            self._close_live_trace()
            try:
                await self._out_queue.put({"type": "error", "error": str(exc)})
            except Exception:
                logger.debug("could not enqueue realtime push error", exc_info=True)

    async def events(self):
        while not self._closed or not self._out_queue.empty():
            item = await self._out_queue.get()
            yield item
            if isinstance(item, dict) and item.get("type") in {"done", "error"}:
                break

    async def _receive_loop(self) -> None:
        assert self._session is not None
        try:
            async for event in self._session.events():
                if self._closed:
                    return
                kind = event.get("type")
                if kind == "connected":
                    continue
                if kind == "user_transcript":
                    text = (event.get("text") or "").strip()
                    if not text:
                        continue
                    # Ignore ASR until the interviewer has asked the real question —
                    # otherwise room tone / Ready / early noise becomes "the answer".
                    if self._prime_draining or not self._question_opened:
                        continue
                    if _is_nonsubstantive_speech(text):
                        logger.info(
                            "dropping nonsubstantive candidate ASR room=%s chars=%d",
                            self.room_id,
                            len(text),
                        )
                        continue
                    self._append_turn("candidate", text)
                    await self._out_queue.put(
                        {"type": "transcript", "role": "candidate", "text": text}
                    )
                    if looks_non_english(text):
                        await self._nudge_english_only()
                elif kind == "text":
                    text = (event.get("text") or "").strip()
                    if not text:
                        continue
                    if _is_ready_ack(text):
                        # Keep draining; do not show or store Ready.
                        continue
                    self._prime_draining = False
                    self._question_opened = True
                    self._append_turn("interviewer", text)
                    await self._out_queue.put(
                        {"type": "transcript", "role": "interviewer", "text": text}
                    )
                elif kind == "audio":
                    data = event.get("data") or b""
                    if not data:
                        continue
                    if self._prime_draining and not self._question_opened:
                        # Drop Ready acknowledgement audio.
                        continue
                    # First non-primed audio counts as the question opening even if
                    # text transcript arrives later (or never).
                    if not self._question_opened:
                        self._prime_draining = False
                        self._question_opened = True
                    if not self._interviewer_playing:
                        self._interviewer_playing = True
                        await self.set_turn_state("interviewer")
                        await self._out_queue.put(
                            {"type": "interviewer_speaking", "active": True}
                        )
                    await self._out_queue.put({"type": "audio", "data": data})
                elif kind == "done":
                    if self._interviewer_playing:
                        self._interviewer_playing = False
                        await self._out_queue.put(
                            {"type": "interviewer_speaking", "active": False}
                        )
                        await self.set_turn_state("listening")
                elif kind == "error":
                    message = event.get("message") or "LiteLLM Live error"
                    self.state.status = "error"
                    self.state.error = message
                    self._closed = True
                    error_transport = self._session
                    self._session = None
                    if error_transport is not None:
                        await error_transport.close()
                    self._close_live_trace()
                    await self._out_queue.put({"type": "error", "error": message})
                    return
                elif kind == "closed":
                    # A clean provider close is terminal too. Returning without closing
                    # the room leaves ``events()`` blocked forever on an empty queue, and
                    # therefore leaves the browser WebSocket waiting forever. Preserve
                    # and package any transcript already captured so callers can grade a
                    # completed answer (or receive an explicit unscorable result).
                    self._closed = True
                    closed_transport = self._session
                    self._session = None
                    if closed_transport is not None:
                        await closed_transport.close()
                    self._finalize_from_turns()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("realtime receive loop failed")
            self.state.status = "error"
            self.state.error = str(exc)
            self._closed = True
            failed_transport = self._session
            self._session = None
            if failed_transport is not None:
                await failed_transport.close()
            self._close_live_trace()
            await self._out_queue.put({"type": "error", "error": str(exc)})

    async def _nudge_english_only(self) -> None:
        """Force one English reminder instead of letting the model thank-and-close."""
        if self._english_nudge_sent or self._session is None or self._closed:
            return
        self._english_nudge_sent = True
        director = (
            "DIRECTOR (not spoken to candidate): The candidate's last utterance was "
            "NOT in English per the application language detector. Do NOT thank them, "
            "do NOT say their answer covers the question, and do NOT end the interview. "
            "Say only a brief reminder to continue in English (≤ 12 words), then LISTEN. "
            "Do not translate or paraphrase their non-English content. Without a DIRECTOR "
            "message like this, never raise a language issue."
        )
        try:
            await self._session.send_text(director)
            logger.info("english-only nudge sent room=%s", self.room_id)
        except Exception:
            logger.debug(
                "english-only nudge failed room=%s", self.room_id, exc_info=True
            )

    def _append_turn(self, role: str, text: str) -> None:
        if role == "interviewer" and _is_ready_ack(text):
            return
        if self.state.turns and self.state.turns[-1]["role"] == role:
            prev = self.state.turns[-1]["text"]
            if text.startswith(prev):
                self.state.turns[-1]["text"] = text
            elif text not in prev:
                self.state.turns[-1]["text"] = (prev + " " + text).strip()
            return
        self.state.turns.append(
            {
                "turn_id": f"t{len(self.state.turns)}",
                "role": role,
                "text": text,
                # Live ASR rarely exposes calibrated confidence; leave unknown.
                "transcript_confidence": None,
            }
        )

    async def finish(self) -> LiveInterviewResult:
        if self._closed:
            if self.state.result:
                return self.state.result
            return self._finalize_from_turns()

        try:
            if self._session is not None:
                try:
                    await asyncio.wait_for(
                        self._session.send_text(
                            "DIRECTOR: The human is hanging up. "
                            "Say a brief goodbye in one short sentence and stop."
                            if self.state.mode == "chat"
                            else (
                                "DIRECTOR: The candidate has finished. "
                                "Thank them in one short sentence and stop."
                            )
                        ),
                        timeout=3.0,
                    )
                    await asyncio.sleep(0.3)
                except Exception:
                    logger.debug("finish cue failed", exc_info=True)
        finally:
            try:
                await asyncio.wait_for(self._close_transport(), timeout=5.0)
            except Exception:
                logger.debug("close transport timed out", exc_info=True)
                self._closed = True
                self._session = None

        if self.state.save_recording and self._candidate_pcm_chunks:
            self._write_test_recording()
        return self._finalize_from_turns()

    def _finalize_from_turns(self) -> LiveInterviewResult:
        # Drop Ready / empty interviewer crumbs that slipped through.
        clean_turns = [
            t
            for t in self.state.turns
            if t.get("text")
            and not (t.get("role") == "interviewer" and _is_ready_ack(t["text"]))
        ]
        self.state.turns = clean_turns
        candidate_bits = [
            t["text"]
            for t in clean_turns
            if t["role"] == "candidate"
            and t.get("text")
            and not _is_nonsubstantive_speech(t["text"])
        ]
        transcript = "\n".join(candidate_bits).strip()
        if not transcript:
            # Keep raw candidate text for debugging in package detail, but mark unscorable
            # when only filler/singing was captured.
            raw = "\n".join(
                t["text"]
                for t in clean_turns
                if t["role"] == "candidate" and t.get("text")
            ).strip()
            result = LiveInterviewResult(
                item_id=self.state.item_id,
                transcript=raw,
                turns=list(clean_turns),
                total_speech_seconds=self.state.candidate_speech_seconds,
                outcome_status="unscorable",
                reason_code="NONSUBSTANTIVE_SPEECH" if raw else "NO_CANDIDATE_SPEECH",
            )
        else:
            result = LiveInterviewResult(
                item_id=self.state.item_id,
                transcript=transcript,
                turns=list(clean_turns),
                total_speech_seconds=self.state.candidate_speech_seconds,
                outcome_status="complete",
                reason_code="",
            )
        self.state.result = result
        self.state.package = result.as_package().model_dump()
        if self.state.status != "error":
            self.state.status = "finished"
        self._close_live_trace(result)
        try:
            self._out_queue.put_nowait(
                {
                    "type": "done",
                    "room_id": self.room_id,
                    "package": self.state.package,
                    "transcript": result.transcript,
                    "turns": self.state.turns,
                }
            )
        except Exception:
            logger.debug("could not enqueue realtime terminal package", exc_info=True)
        return result

    def _close_live_trace(self, result: LiveInterviewResult | None = None) -> None:
        if self._live_trace is None or self._live_trace.ended:
            return
        result = result or self.state.result
        candidate_turns = sum(
            1 for t in self.state.turns if t.get("role") == "candidate"
        )
        interviewer_turns = sum(
            1 for t in self.state.turns if t.get("role") == "interviewer"
        )
        error = self.state.error if self.state.status == "error" else ""
        observability.end_live(
            self._live_trace,
            outcome_status=(
                result.outcome_status
                if result is not None
                else ("error" if error else self.state.status)
            ),
            reason_code=(result.reason_code if result is not None else ""),
            error=error,
            candidate_turns=candidate_turns,
            interviewer_turns=interviewer_turns,
            speech_seconds=self.state.candidate_speech_seconds,
            transcript=(result.transcript if result is not None else ""),
        )
        observability.flush()

    def _write_test_recording(self) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        pcm = b"".join(self._candidate_pcm_chunks)
        wav = pcm16_to_wav_bytes(pcm, LIVE_INPUT_RATE)
        path = RECORDINGS_DIR / f"{self.room_id}_{self.state.item_id}.wav"
        path.write_bytes(wav)
        logger.info("saved test recording %s (%d bytes)", path, len(wav))

    async def _close_transport(self) -> None:
        self._closed = True
        if self._receive_task is not None:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
            self._receive_task = None
        if self._session is not None:
            try:
                await self._session.close()
            except Exception:
                logger.debug("live close failed", exc_info=True)
        self._session = None


def _is_ready_ack(text: str) -> bool:
    return bool(_READY_ACK.match((text or "").strip()))


def _is_nonsubstantive_speech(text: str) -> bool:
    """True for singing/filler loops that carry no assessment evidence."""
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    words = cleaned.split()
    if len(words) >= 12:
        filler = sum(1 for w in words if _FILLER_TOKEN.match(w.strip(".,!?;:")))
        if filler / len(words) >= 0.8:
            return True
        unique = {w.lower().strip(".,!?;:") for w in words}
        if len(unique) <= 2 and len(words) >= 20:
            return True
    return bool(_READY_ACK.match(cleaned))
