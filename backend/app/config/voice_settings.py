"""Voice / open-ended assessment settings. Separate from the shared CAT Settings.

Live interviewer audio uses LiteLLM realtime (`LITELLM_LIVE_PREVIEW_MODEL`).
Picker + open rubric grader use `LITELLM_MODEL` via the shared Settings.
`GEMINI_API_KEY` is unused when Live is routed through LiteLLM.
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

    # --- Legacy direct Gemini key (unused when Live goes through LiteLLM) -----
    gemini_api_key: str = ""
    # Kept for docs; Live model id comes from Settings.litellm_live_preview_model.
    gemini_live_model: str = "gemini/gemini-3.1-flash-live-preview"
    gemini_live_voice: str = "Puck"
    # Primary mode: manual activity control (server VAD off). Flip to server_vad to A/B.
    gating_mode: str = Field(default="manual", pattern="^(manual|server_vad)$")

    # --- Voice assessment policy ---------------------------------------------
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

    # Interviewer cut-off ladder (ms). Thinking pause is deliberately generous so a
    # mid-sentence breath does not look like end-of-turn (which triggers "Thanks…").
    thinking_pause_ms: int = 1200
    turn_end_silence_ms: int = 2200
    nudge_silence_ms: int = 8000
    repeat_silence_ms: int = 20000
    abandon_silence_ms: int = 45000
    maximum_probes_default: int = 2

    # Client speech gate (energy). Used when gating_mode=manual.
    gate_open_db: float = -45.0
    gate_close_db: float = -50.0
    gate_open_frames: int = 3
    gate_close_frames: int = 8
    gate_barge_in_extra_db: float = 6.0

    # Open items are Live-only in this branch — never typed/transcript answers.
    allow_text_fallback: bool = False

    # Realtime Live WebSocket server (browser mic duplex)
    live_server_host: str = "127.0.0.1"
    live_server_port: int = 8765

    @property
    def live_server_base(self) -> str:
        return f"http://{self.live_server_host}:{self.live_server_port}"


voice_settings = VoiceSettings()
