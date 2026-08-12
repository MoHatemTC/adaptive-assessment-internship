"""Conversational Gemini Live interviewer — voice assessment ONLY.

Never used for picking or rubric grading (those stay on LiteLLM).

Streamlit cannot keep a browser mic duplex socket open easily, so this module
runs a turn-based Live conversation that still feels human↔human:

  1. Interviewer speaks the question (audio + transcript)
  2. Candidate records an answer → audio sent into the same Live session
  3. Interviewer acknowledges / probes (audio + transcript)
  4. Repeat until the candidate finishes or probe budget is exhausted
  5. Return a VoiceResponsePackage for the async rubric grader
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, ClassVar

from google.genai import types

from cat_engine.engine.config.voice_settings import voice_settings
from cat_engine.engine.schemas.voice import VoiceResponsePackage, VoiceTurn
from cat_engine.engine.services.voice.prompts import INTERVIEWER_SYSTEM
from cat_engine.engine.services.voice_live.audio_codec import (
    LIVE_INPUT_RATE,
    LIVE_OUTPUT_RATE,
    pcm16_to_wav_bytes,
    wav_bytes_to_pcm16,
)

logger = logging.getLogger(__name__)


@dataclass
class InterviewTurnAudio:
    """One interviewer utterance ready for Streamlit playback."""

    wav_bytes: bytes
    transcript: str
    role: str = "interviewer"
    session_id: str = ""


@dataclass
class LiveInterviewResult:
    """What a completed Live item returns to the assessment half."""

    item_id: str
    transcript: str
    turns: list[dict] = field(default_factory=list)
    total_speech_seconds: float = 0.0
    outcome_status: str = "complete"
    reason_code: str = ""
    available: bool = True
    error_message: str = ""
    interviewer_wavs: list[bytes] = field(default_factory=list)

    def as_package(self) -> VoiceResponsePackage:
        voice_turns = [
            VoiceTurn(
                turn_id=str(t.get("turn_id", f"t{i}")),
                role=t.get("role", "candidate"),  # type: ignore[arg-type]
                text=t.get("text", ""),
                transcript_confidence=(
                    None
                    if t.get("transcript_confidence") is None
                    else float(t["transcript_confidence"])
                ),
            )
            for i, t in enumerate(self.turns)
            if t.get("text")
        ]
        candidate_text = "\n".join(
            t.text for t in voice_turns if t.role == "candidate"
        ).strip()
        known = [
            float(t.transcript_confidence)
            for t in voice_turns
            if t.role == "candidate" and t.transcript_confidence is not None
        ]
        return VoiceResponsePackage(
            item_id=self.item_id,
            outcome_status=self.outcome_status,  # type: ignore[arg-type]
            reason_code=self.reason_code,
            turns=voice_turns,
            total_speech_seconds=self.total_speech_seconds,
            mean_transcript_confidence=(sum(known) / len(known) if known else None),
            word_count=len(candidate_text.split()),
            live_text=candidate_text,
            final_text=candidate_text,
        )


class GeminiLiveInterviewer:
    """Gemini Live conversational interviewer (audio in / audio out)."""

    def __init__(self) -> None:
        self._client = None

    @property
    def configured(self) -> bool:
        return bool(voice_settings.gemini_api_key.strip())

    @property
    def model(self) -> str:
        return voice_settings.gemini_live_model

    def google_model_name(self) -> str:
        m = (self.model or "").strip()
        return m.split("/")[-1] if "/" in m else m

    def _client_or_raise(self):
        if not self.configured:
            raise RuntimeError(
                "GEMINI_API_KEY is not set — required only for voice Live interviews"
            )
        if self._client is None:
            try:
                from google import genai
                from google.genai import types as genai_types
            except ImportError as exc:
                raise RuntimeError(
                    "google-genai is not installed — pip install google-genai"
                ) from exc
            # websockets defaults (ping_interval=20, ping_timeout=20) close the Live
            # socket with 1011 while audio is flowing and the peer is slow to pong.
            # Pass through Client http_options → ws_connect kwargs.
            self._client = genai.Client(
                api_key=voice_settings.gemini_api_key,
                http_options=genai_types.HttpOptions(
                    async_client_args={
                        "ping_interval": None,
                        "ping_timeout": None,
                        "max_size": 8_000_000,
                        "max_queue": 64,
                        "close_timeout": 15,
                    }
                ),
            )
        return self._client

    def _connect_config(
        self, question_text: str, nonce: str
    ) -> types.LiveConnectConfig:
        system = (
            INTERVIEWER_SYSTEM
            + f"\nSession nonce: {nonce}"
            + "\nYou may ask up to "
            + str(voice_settings.maximum_probes_default)
            + " short clarifying probes if the answer is incomplete."
            + "\nWhen enough evidence is gathered, thank the candidate briefly and stop."
        )
        # Server VAD works for Streamlit turn batches (record → send → wait).
        # Manual gating is for the future AudioWorklet continuous mic stream.
        vad_disabled = voice_settings.gating_mode == "manual"
        return types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
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
                automatic_activity_detection=types.AutomaticActivityDetection(
                    disabled=vad_disabled,
                )
            ),
        )

    async def smoke_connect(self) -> dict:
        client = self._client_or_raise()
        return {
            "ok": True,
            "model": self.google_model_name(),
            "litellm_model": self.model,
            "client": type(client).__name__,
            "gating_mode": voice_settings.gating_mode,
            "note": "Conversational Live interview is available in Streamlit voice mode.",
        }

    async def ask_question(
        self, item_id: str, question_text: str
    ) -> InterviewTurnAudio:
        """Open a short Live turn: interviewer reads the question aloud."""
        session = ConversationalLiveSession(
            self, item_id=item_id, question_text=question_text
        )
        await session.start()
        audio = await session.prompt_question()
        # Keep the open session handle on the audio object via session registry
        ConversationalLiveSession.store(session)
        audio.session_id = session.session_id  # type: ignore[attr-defined]
        return audio

    async def continue_with_candidate_audio(
        self,
        session_id: str,
        wav_bytes: bytes,
    ) -> InterviewTurnAudio:
        session = ConversationalLiveSession.get(session_id)
        if session is None:
            raise RuntimeError("interview session expired — start the interview again")
        return await session.ingest_candidate_wav(wav_bytes)

    async def finish(self, session_id: str) -> LiveInterviewResult:
        session = ConversationalLiveSession.get(session_id)
        if session is None:
            raise RuntimeError("interview session expired — start the interview again")
        try:
            return await session.finish()
        finally:
            ConversationalLiveSession.drop(session_id)


class ConversationalLiveSession:
    """One Gemini Live WebSocket conversation for a single bank item."""

    _REGISTRY: ClassVar[dict[str, ConversationalLiveSession]] = {}

    def __init__(
        self,
        interviewer: GeminiLiveInterviewer,
        *,
        item_id: str,
        question_text: str,
    ) -> None:
        self.interviewer = interviewer
        self.item_id = item_id
        self.question_text = question_text
        self.session_id = uuid.uuid4().hex[:12]
        self.nonce = uuid.uuid4().hex[:8]
        self.turns: list[dict] = []
        self.interviewer_wavs: list[bytes] = []
        self.candidate_speech_seconds = 0.0
        self.probe_count = 0
        self._ctx: Any = None
        self._session: Any = None
        self._turn_counter = 0

    @classmethod
    def store(cls, session: ConversationalLiveSession) -> None:
        cls._REGISTRY[session.session_id] = session

    @classmethod
    def get(cls, session_id: str) -> ConversationalLiveSession | None:
        return cls._REGISTRY.get(session_id)

    @classmethod
    def drop(cls, session_id: str) -> None:
        cls._REGISTRY.pop(session_id, None)

    async def start(self) -> None:
        client = self.interviewer._client_or_raise()
        config = self.interviewer._connect_config(self.question_text, self.nonce)
        # Keep the async context manager alive for the whole interview.
        self._ctx = client.aio.live.connect(
            model=self.interviewer.google_model_name(),
            config=config,
        )
        self._session = await self._ctx.__aenter__()

    async def prompt_question(self) -> InterviewTurnAudio:
        assert self._session is not None
        director = (
            "DIRECTOR (not spoken to candidate): Begin the interview now. "
            "Read the following assessment question clearly and naturally. "
            "Do not add scoring advice. Wait for the candidate to answer.\n\n"
            f"QUESTION:\n{self.question_text}"
        )
        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part(text=director)]),
            turn_complete=True,
        )
        return await self._collect_model_audio(role_label="interviewer")

    async def ingest_candidate_wav(self, wav_bytes: bytes) -> InterviewTurnAudio:
        assert self._session is not None
        pcm, duration = wav_bytes_to_pcm16(wav_bytes, LIVE_INPUT_RATE)
        self.candidate_speech_seconds += duration

        # Manual mode: explicit activity markers. Server VAD: stream + audio_stream_end.
        if voice_settings.gating_mode == "manual":
            await self._session.send_realtime_input(
                activity_start=types.ActivityStart()
            )
            await self._session.send_realtime_input(
                audio=types.Blob(
                    data=pcm, mime_type=f"audio/pcm;rate={LIVE_INPUT_RATE}"
                )
            )
            await self._session.send_realtime_input(activity_end=types.ActivityEnd())
        else:
            # Chunk large recordings so the Live socket stays responsive.
            chunk = 3200 * 2  # 100 ms @ 16 kHz mono PCM16
            for i in range(0, len(pcm), chunk):
                await self._session.send_realtime_input(
                    audio=types.Blob(
                        data=pcm[i : i + chunk],
                        mime_type=f"audio/pcm;rate={LIVE_INPUT_RATE}",
                    )
                )
            await self._session.send_realtime_input(audio_stream_end=True)

        # Placeholder candidate turn; input transcription may arrive during receive.
        self._turn_counter += 1
        cand_id = f"c{self._turn_counter}"
        self.turns.append(
            {
                "turn_id": cand_id,
                "role": "candidate",
                "text": "",
                "transcript_confidence": None,
            }
        )
        audio = await self._collect_model_audio(role_label="interviewer")
        self.probe_count += 1
        return audio

    async def _collect_model_audio(
        self, role_label: str = "interviewer"
    ) -> InterviewTurnAudio:
        assert self._session is not None
        pcm_chunks: list[bytes] = []
        out_text_parts: list[str] = []
        in_text_parts: list[str] = []

        async for msg in self._session.receive():
            sc = getattr(msg, "server_content", None)
            if sc is None:
                continue
            if getattr(sc, "input_transcription", None) and sc.input_transcription.text:
                in_text_parts.append(sc.input_transcription.text)
            if (
                getattr(sc, "output_transcription", None)
                and sc.output_transcription.text
            ):
                out_text_parts.append(sc.output_transcription.text)
            model_turn = getattr(sc, "model_turn", None)
            if model_turn and model_turn.parts:
                for part in model_turn.parts:
                    inline = getattr(part, "inline_data", None)
                    if inline and inline.data:
                        pcm_chunks.append(inline.data)
            if getattr(sc, "turn_complete", False):
                break

        if in_text_parts:
            # Attach latest input transcription to the most recent candidate turn.
            for t in reversed(self.turns):
                if t["role"] == "candidate":
                    existing = (t.get("text") or "").strip()
                    added = " ".join(in_text_parts).strip()
                    t["text"] = (existing + " " + added).strip() if existing else added
                    break

        transcript = " ".join(out_text_parts).strip()
        pcm = b"".join(pcm_chunks)
        wav = pcm16_to_wav_bytes(pcm, LIVE_OUTPUT_RATE) if pcm else b""
        if wav:
            self.interviewer_wavs.append(wav)

        self._turn_counter += 1
        turn_id = f"i{self._turn_counter}"
        if transcript:
            self.turns.append(
                {
                    "turn_id": turn_id,
                    "role": role_label,
                    "text": transcript,
                    "transcript_confidence": None,
                }
            )

        result = InterviewTurnAudio(
            wav_bytes=wav, transcript=transcript, role=role_label
        )
        result.session_id = self.session_id  # type: ignore[attr-defined]
        return result

    async def finish(self) -> LiveInterviewResult:
        try:
            if self._session is not None:
                # Soft close cue so the model does not keep waiting.
                try:
                    await self._session.send_client_content(
                        turns=types.Content(
                            role="user",
                            parts=[
                                types.Part(
                                    text=(
                                        "DIRECTOR: The candidate has finished answering. "
                                        "Thank them briefly in one short sentence and stop."
                                    )
                                )
                            ],
                        ),
                        turn_complete=True,
                    )
                    # Drain a short goodbye if it arrives quickly.
                    try:
                        await asyncio.wait_for(
                            self._collect_model_audio(role_label="interviewer"),
                            timeout=8.0,
                        )
                    except asyncio.TimeoutError:
                        pass
                except Exception:
                    logger.debug("live finish cue failed", exc_info=True)
        finally:
            await self._close()

        candidate_bits = [
            t["text"] for t in self.turns if t["role"] == "candidate" and t.get("text")
        ]
        transcript = "\n".join(candidate_bits).strip()
        status = "complete" if transcript else "unscorable"
        reason = "" if transcript else "NO_CANDIDATE_SPEECH"
        return LiveInterviewResult(
            item_id=self.item_id,
            transcript=transcript,
            turns=list(self.turns),
            total_speech_seconds=self.candidate_speech_seconds,
            outcome_status=status,
            reason_code=reason,
            interviewer_wavs=list(self.interviewer_wavs),
        )

    async def _close(self) -> None:
        if self._ctx is not None:
            try:
                await self._ctx.__aexit__(None, None, None)
            except Exception:
                logger.debug("live session close failed", exc_info=True)
        self._ctx = None
        self._session = None
