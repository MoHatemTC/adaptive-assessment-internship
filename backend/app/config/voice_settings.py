"""Voice / open-ended assessment settings. Separate from the shared CAT Settings.

GEMINI_* keys are used ONLY by the Live voice interviewer. Every other agent
(picker, open-ended rubric grader) routes through LiteLLM via the shared Settings.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve `.env` next to the backend package root regardless of cwd.
# backend/app/config/voice_settings.py -> parents[2] == backend/
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _PACKAGE_ROOT / ".env"


class VoiceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Gemini Live: voice interviewer ONLY ---------------------------------
    gemini_api_key: str = ""
    # Preview Live model — audio-to-audio, not used for grading or picking.
    gemini_live_model: str = "gemini-3.1-flash-live-preview"
    gemini_live_voice: str = "Puck"
    # Primary mode: manual activity control (server VAD off). Flip to server_vad to A/B.
    gating_mode: str = Field(default="manual", pattern="^(manual|server_vad)$")

    # --- Voice assessment policy ---------------------------------------------
    # Retuned for fractional voice weights (~0.6); see plan risk #1.
    # Shared Settings.cat_se_target still defaults to 0.65 for MCQ; this overrides
    # for voice sessions via VoiceAssessmentRunner.
    voice_se_target: float = 0.80
    voice_stability_se_ceiling: float = 0.95
    voice_max_questions_per_competency: int = 12
    voice_min_questions_per_competency: int = 4
    voice_session_time_limit_minutes: float = 45.0

    # Evidence / quote matcher
    quote_min_chars: int = 12
    quote_fuzzy_min_chars: int = 24
    quote_fuzzy_ratio: float = 0.82
    transcript_confidence_floor: float = 0.55
    min_speech_seconds_scorable: float = 3.0

    # Interviewer cut-off ladder (ms)
    # Short silence = thinking pause (interviewer must NOT jump in).
    thinking_pause_ms: int = 900
    # Longer silence after speech = candidate ended this utterance.
    turn_end_silence_ms: int = 1800
    nudge_silence_ms: int = 8000
    repeat_silence_ms: int = 20000
    abandon_silence_ms: int = 45000
    maximum_probes_default: int = 2

    # Client speech gate (energy). Used when gating_mode=manual.
    gate_open_db: float = -45.0
    gate_close_db: float = -50.0
    gate_open_frames: int = 3
    gate_close_frames: int = 8
    # While interviewer audio is playing, require louder barge-in.
    gate_barge_in_extra_db: float = 6.0

    # Streamlit tester: allow typed transcripts without Live
    allow_text_fallback: bool = True

    # Realtime Live WebSocket server (browser mic duplex)
    live_server_host: str = "127.0.0.1"
    live_server_port: int = 8765

    @property
    def live_server_base(self) -> str:
        return f"http://{self.live_server_host}:{self.live_server_port}"


voice_settings = VoiceSettings()
