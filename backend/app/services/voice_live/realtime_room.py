"""Realtime duplex room: browser mic ↔ Gemini Live ↔ browser speaker.

Recording to disk is optional and for testing only — the live path never depends on it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from google.genai import types

from app.config.voice_settings import voice_settings
from app.schemas.voice import VoiceResponsePackage, VoiceTurn
from app.services.voice.prompts import CHAT_SYSTEM, INTERVIEWER_SYSTEM, TURN_TAKING_DIRECTOR
from app.services.voice_live.audio_codec import LIVE_INPUT_RATE, pcm16_to_wav_bytes
from app.services.voice_live.gemini_live import GeminiLiveInterviewer, LiveInterviewResult

logger = logging.getLogger(__name__)

RECORDINGS_DIR = Path(__file__).resolve().parents[3] / "recordings"


@dataclass
class RealtimeRoomState:
    room_id: str
    item_id: str
    question: str
    mode: str = "interview"  # interview | chat
    save_recording: bool = False
    status: str = "created"  # created|connecting|live|finished|error
    turn_state: str = "idle"  # idle|listening|speaking|paused|ended|interviewer
    error: str = ""
    turns: list[dict] = field(default_factory=list)
    candidate_speech_seconds: float = 0.0
    result: LiveInterviewResult | None = None
    package: dict | None = None
    created_at: float = field(default_factory=time.time)


class RealtimeLiveRoom:
    """One continuous Live interview room."""

    _ROOMS: dict[str, "RealtimeLiveRoom"] = {}

    def __init__(
        self,
        *,
        item_id: str,
        question: str,
        save_recording: bool = False,
        mode: str = "interview",
    ) -> None:
        self.room_id = uuid.uuid4().hex[:12]
        self.state = RealtimeRoomState(
            room_id=self.room_id,
            item_id=item_id,
            question=question,
            mode=mode if mode in {"interview", "chat"} else "interview",
            save_recording=save_recording,
        )
        self._interviewer = GeminiLiveInterviewer()
        self._ctx = None
        self._session = None
        self._out_queue: asyncio.Queue = asyncio.Queue()
        self._receive_task: asyncio.Task | None = None
        self._candidate_pcm_chunks: list[bytes] = []
        self._bytes_forwarded = 0
        self._closed = False
        self._activity_open = False
        self._interviewer_playing = False
        self._last_send_at = 0.0
        self._heartbeat_task: asyncio.Task | None = None
        self._use_manual_activity = True
        self._pcm_frames = 0
        self._last_pcm_stat_at = 0.0
        self._send_lock = asyncio.Lock()

    @classmethod
    def create(
        cls,
        item_id: str,
        question: str,
        save_recording: bool = False,
        mode: str = "interview",
    ) -> "RealtimeLiveRoom":
        room = cls(
            item_id=item_id,
            question=question,
            save_recording=save_recording,
            mode=mode,
        )
        cls._ROOMS[room.room_id] = room
        return room

    @classmethod
    def get(cls, room_id: str) -> "RealtimeLiveRoom | None":
        return cls._ROOMS.get(room_id)

    @classmethod
    def drop(cls, room_id: str) -> None:
        cls._ROOMS.pop(room_id, None)

    def turn_config(self) -> dict:
        """Thresholds the browser gate + UI should use."""
        # gemini-3.1-flash-live-preview ignores continuous realtime audio under
        # server VAD (no input transcript / no reply). Manual activity markers work.
        use_manual = getattr(self, "_use_manual_activity", True)
        return {
            "gating_mode": "manual" if use_manual else "server_vad",
            "stream_mode": "gated" if use_manual else "continuous",
            "thinking_pause_ms": voice_settings.thinking_pause_ms,
            "turn_end_silence_ms": voice_settings.turn_end_silence_ms,
            "nudge_silence_ms": voice_settings.nudge_silence_ms,
            "gate_open_db": voice_settings.gate_open_db,
            "gate_close_db": voice_settings.gate_close_db,
            "gate_open_frames": voice_settings.gate_open_frames,
            "gate_close_frames": voice_settings.gate_close_frames,
            "gate_barge_in_extra_db": voice_settings.gate_barge_in_extra_db,
        }

    async def connect(self) -> None:
        if not self._interviewer.configured:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.state.status = "connecting"
        client = self._interviewer._client_or_raise()
        nonce = uuid.uuid4().hex[:8]
        chat = self.state.mode == "chat"
        base_system = CHAT_SYSTEM if chat else INTERVIEWER_SYSTEM
        system = (
            base_system
            + "\n"
            + TURN_TAKING_DIRECTOR.format(
                pause_ms=voice_settings.thinking_pause_ms,
                turn_end_ms=voice_settings.turn_end_silence_ms,
            )
            + f"\nSession nonce: {nonce}\n"
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
                f"You may ask up to {voice_settings.maximum_probes_default} short clarifying probes. "
                "When enough evidence is gathered, thank the candidate briefly and stop speaking."
            )
        # Manual activity_start/end is required for gemini-3.1-flash-live-preview:
        # server VAD + continuous PCM never produces input transcripts or replies
        # (verified with tone + trailing silence + audio_stream_end). Chat used to
        # force server_vad; that is the "mic streams but model never hears you" bug.
        use_manual = True
        if (not chat) and voice_settings.gating_mode == "server_vad":
            use_manual = False  # interview A/B only — expect silence until model fixed
        vad = types.AutomaticActivityDetection(
            disabled=use_manual,
            silence_duration_ms=None if use_manual else max(voice_settings.turn_end_silence_ms, 1200),
            prefix_padding_ms=None if use_manual else 300,
        )
        self._use_manual_activity = use_manual
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system,
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice_settings.gemini_live_voice
                    )
                )
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
            realtime_input_config=types.RealtimeInputConfig(
                automatic_activity_detection=vad,
            ),
        )
        self._ctx = client.aio.live.connect(
            model=self._interviewer.google_model_name(),
            config=config,
        )
        self._session = await self._ctx.__aenter__()
        self.state.status = "live"
        self.state.turn_state = "listening"
        self._last_send_at = time.monotonic()
        self._receive_task = asyncio.create_task(self._receive_loop())
        # Do not inject silence keepalive in manual mode. Out-of-activity PCM confuses
        # gemini-3.1-flash-live-preview (user turns get no reply). Client WS pings are
        # already disabled on the genai Client, which was the main 1011 cause.
        self._heartbeat_task = None

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
        await self._send_client(
            turns=types.Content(role="user", parts=[types.Part(text=director)]),
            turn_complete=True,
        )
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

    async def _send_realtime(self, **kwargs) -> None:
        if self._closed or self._session is None:
            return
        async with self._send_lock:
            if self._closed or self._session is None:
                return
            await self._session.send_realtime_input(**kwargs)
            self._last_send_at = time.monotonic()

    async def _send_client(self, **kwargs) -> None:
        if self._closed or self._session is None:
            return
        async with self._send_lock:
            if self._closed or self._session is None:
                return
            await self._session.send_client_content(**kwargs)
            self._last_send_at = time.monotonic()

    async def set_turn_state(self, turn_state: str) -> None:
        self.state.turn_state = turn_state
        await self._out_queue.put(
            {"type": "turn_state", "turn_state": turn_state, "room_id": self.room_id}
        )

    async def activity_start(self) -> None:
        if self._closed or self._session is None:
            return
        if getattr(self, "_use_manual_activity", True) and not self._activity_open:
            await self._send_realtime(activity_start=types.ActivityStart())
            self._activity_open = True
        await self.set_turn_state("speaking")

    async def activity_end(self) -> None:
        """Candidate ended this utterance — interviewer may reply."""
        if self._closed or self._session is None:
            return
        if getattr(self, "_use_manual_activity", True) and self._activity_open:
            await self._send_realtime(activity_end=types.ActivityEnd())
            self._activity_open = False
            await self.set_turn_state("ended")
            # Do NOT send a text DIRECTOR cue here. On gemini-3.1-flash-live-preview,
            # a send_client_content turn right after activity_end races/overrides the
            # just-closed audio turn — the model never replies to the mic audio.
            # Manual activity_end alone is enough (verified in isolation).
        else:
            # Chat / server-VAD: client markers are UI-only.
            await self.set_turn_state("ended")

    async def mark_pause(self) -> None:
        """Thinking pause — still the same turn; interviewer must stay silent."""
        if self.state.turn_state == "speaking":
            await self.set_turn_state("paused")

    async def mark_resume(self) -> None:
        if self.state.turn_state in {"paused", "ended", "listening", "idle"}:
            if getattr(self, "_use_manual_activity", True) and not self._activity_open:
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
        # Interview manual mode: only forward while activity is open.
        # Chat continuous mode: always forward (server VAD handles turns).
        if getattr(self, "_use_manual_activity", True) and not self._activity_open:
            return
        try:
            await self._send_realtime(
                audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={LIVE_INPUT_RATE}")
            )
            now = time.monotonic()
            if now - self._last_pcm_stat_at >= 2.0:
                self._last_pcm_stat_at = now
                from app.services.voice_live.debug_log import live_debug

                # Peak |sample| — distinguishes silent mic from real speech.
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
                )
        except Exception as exc:  # noqa: BLE001
            from app.services.voice_live.debug_log import live_debug_exc

            # Dropping the browser call on every transient Gemini ping timeout is harsh;
            # mark error but keep room usable for hang-up / debug.
            live_debug_exc("push_pcm_failed", exc, room_id=self.room_id)
            self.state.status = "error"
            self.state.error = str(exc)
            self._closed = True
            try:
                await self._out_queue.put({"type": "error", "error": str(exc)})
            except Exception:  # noqa: BLE001
                pass
            # Do not re-raise — otherwise FastAPI WS spam-logs and tears down mid-batch.
            return

    async def _keepalive_loop(self) -> None:
        """Send tiny silent PCM while gated idle so Gemini Live WS does not idle-timeout.

        Manual gating often withholds mic frames for 40s+ (interviewer talking / thinking
        pause). Without outbound realtime_input, the upstream socket dies with 1011
        keepalive ping timeout — seen in assessment and duplex smoke.
        """
        # 20 ms of silence @ 16 kHz mono PCM16
        silence = b"\x00\x00" * int(LIVE_INPUT_RATE * 0.02)
        try:
            while not self._closed and self._session is not None:
                await asyncio.sleep(1.0)
                if self._closed or self._session is None:
                    break
                # Skip if we recently forwarded real speech.
                if time.monotonic() - self._last_send_at < 0.8:
                    continue
                # Do not inject silence inside an open activity turn — real PCM owns that.
                if self._activity_open:
                    continue
                try:
                    await self._send_realtime(
                        audio=types.Blob(
                            data=silence,
                            mime_type=f"audio/pcm;rate={LIVE_INPUT_RATE}",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    from app.services.voice_live.debug_log import live_debug_exc

                    live_debug_exc("keepalive_failed", exc, room_id=self.room_id)
                    self.state.status = "error"
                    self.state.error = str(exc)
                    self._closed = True
                    await self._out_queue.put({"type": "error", "error": str(exc)})
                    break
        except asyncio.CancelledError:
            raise

    async def events(self):
        """Async iterator of outbound events (dicts or {"type":"audio","data": bytes})."""
        while not self._closed or not self._out_queue.empty():
            item = await self._out_queue.get()
            yield item
            if isinstance(item, dict) and item.get("type") in {"done", "error"}:
                break

    async def _receive_loop(self) -> None:
        assert self._session is not None
        try:
            # google-genai AsyncSession.receive() ends after EACH model turn
            # (breaks on turn_complete). Re-enter until the room closes — otherwise
            # we only ever hear the greeting and miss every user reply.
            while not self._closed and self._session is not None:
                async for msg in self._session.receive():
                    if self._closed:
                        return
                    sc = getattr(msg, "server_content", None)
                    if sc is None:
                        continue
                    if getattr(sc, "input_transcription", None) and sc.input_transcription.text:
                        text = sc.input_transcription.text.strip()
                        if text:
                            self._append_turn("candidate", text)
                            from app.services.voice_live.debug_log import live_debug

                            live_debug(
                                "transcript_candidate",
                                room_id=self.room_id,
                                text=text[:240],
                                chars=len(text),
                            )
                            await self._out_queue.put(
                                {"type": "transcript", "role": "candidate", "text": text}
                            )
                    if getattr(sc, "output_transcription", None) and sc.output_transcription.text:
                        text = sc.output_transcription.text.strip()
                        if text:
                            self._append_turn("interviewer", text)
                            from app.services.voice_live.debug_log import live_debug

                            live_debug(
                                "transcript_interviewer",
                                room_id=self.room_id,
                                text=text[:240],
                                chars=len(text),
                            )
                            await self._out_queue.put(
                                {"type": "transcript", "role": "interviewer", "text": text}
                            )
                    model_turn = getattr(sc, "model_turn", None)
                    if model_turn and model_turn.parts:
                        for part in model_turn.parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and inline.data:
                                if not self._interviewer_playing:
                                    self._interviewer_playing = True
                                    await self.set_turn_state("interviewer")
                                    await self._out_queue.put(
                                        {"type": "interviewer_speaking", "active": True}
                                    )
                                await self._out_queue.put({"type": "audio", "data": inline.data})
                    if getattr(sc, "turn_complete", False):
                        if self._interviewer_playing:
                            self._interviewer_playing = False
                            await self._out_queue.put(
                                {"type": "interviewer_speaking", "active": False}
                            )
                            await self.set_turn_state("listening")
                # iterator ended after one turn — loop for the next model turn
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("realtime receive loop failed")
            self.state.status = "error"
            self.state.error = str(exc)
            await self._out_queue.put({"type": "error", "error": str(exc)})

    def _append_turn(self, role: str, text: str) -> None:
        # Merge consecutive same-role partials into one growing turn.
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
                "transcript_confidence": 0.9,
            }
        )

    async def finish(self) -> LiveInterviewResult:
        if self._closed:
            if self.state.result:
                return self.state.result
            # Hang-up raced / double-clicked — still produce a package from turns.
            candidate_bits = [
                t["text"]
                for t in self.state.turns
                if t["role"] == "candidate" and t.get("text")
            ]
            transcript = "\n".join(candidate_bits).strip()
            result = LiveInterviewResult(
                item_id=self.state.item_id,
                transcript=transcript,
                turns=list(self.state.turns),
                total_speech_seconds=self.state.candidate_speech_seconds,
                outcome_status="complete" if transcript else "unscorable",
                reason_code="" if transcript else "NO_CANDIDATE_SPEECH",
            )
            self.state.result = result
            self.state.package = result.as_package().model_dump()
            if self.state.status != "error":
                self.state.status = "finished"
            return result

        try:
            if self._session is not None:
                try:
                    await asyncio.wait_for(
                        self._send_client(
                            turns=types.Content(
                                role="user",
                                parts=[
                                    types.Part(
                                        text=(
                                            "DIRECTOR: The human is hanging up. "
                                            "Say a brief goodbye in one short sentence and stop."
                                            if self.state.mode == "chat"
                                            else (
                                                "DIRECTOR: The candidate has finished. "
                                                "Thank them in one short sentence and stop."
                                            )
                                        )
                                    )
                                ],
                            ),
                            turn_complete=True,
                        ),
                        timeout=3.0,
                    )
                    await asyncio.sleep(0.3)
                except Exception:  # noqa: BLE001
                    logger.debug("finish cue failed", exc_info=True)
        finally:
            try:
                await asyncio.wait_for(self._close_transport(), timeout=5.0)
            except Exception:  # noqa: BLE001
                logger.debug("close transport timed out", exc_info=True)
                self._closed = True
                self._session = None
                self._ctx = None

        if self.state.save_recording and self._candidate_pcm_chunks:
            self._write_test_recording()

        candidate_bits = [
            t["text"] for t in self.state.turns if t["role"] == "candidate" and t.get("text")
        ]
        transcript = "\n".join(candidate_bits).strip()
        result = LiveInterviewResult(
            item_id=self.state.item_id,
            transcript=transcript,
            turns=list(self.state.turns),
            total_speech_seconds=self.state.candidate_speech_seconds,
            outcome_status="complete" if transcript else "unscorable",
            reason_code="" if transcript else "NO_CANDIDATE_SPEECH",
        )
        self.state.result = result
        self.state.package = result.as_package().model_dump()
        self.state.status = "finished"
        await self._out_queue.put(
            {
                "type": "done",
                "room_id": self.room_id,
                "package": self.state.package,
                "transcript": transcript,
                "turns": self.state.turns,
            }
        )
        return result

    def _write_test_recording(self) -> None:
        RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
        pcm = b"".join(self._candidate_pcm_chunks)
        wav = pcm16_to_wav_bytes(pcm, LIVE_INPUT_RATE)
        path = RECORDINGS_DIR / f"{self.room_id}_{self.state.item_id}.wav"
        path.write_bytes(wav)
        logger.info("saved test recording %s (%d bytes)", path, len(wav))

    async def _close_transport(self) -> None:
        self._closed = True
        for task_attr in ("_heartbeat_task", "_receive_task"):
            task = getattr(self, task_attr, None)
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                setattr(self, task_attr, None)
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                logger.debug("live close failed", exc_info=True)
        self._ctx = None
        self._session = None
