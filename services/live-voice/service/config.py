"""Configuration for live-voice.

The realtime half. Everything here is about audio transport and turn-taking; nothing here
decides anything about a candidate's ability, which is why this service can be restarted,
scaled or switched off without touching a measurement.
"""

from __future__ import annotations

from adaptive_service import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "live-voice"
    #: 8765 rather than 808x, because that is the port the monolith's helper used and the
    #: `/interview` iframe is embedded by URL. Keeping it means one fewer thing to change
    #: in whatever is already pointing at it.
    port: int = 8765

    #: The debug feed carries transcripts and room internals. Off by default: it is an
    #: operator tool, and a candidate's spoken answer is in it.
    live_debug_api_enabled: bool = False

    #: A frontend embeds `/interview` in an iframe from its own origin.
    cors_allow_origins: str = "*"

    def allowed_origins(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        return ["*"] if raw in ("", "*") else [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()
