"""`/health` and `/config` — the two routes an operator reaches for first.

WHY THEY ARE SHARED CODE AND NOT A CONVENTION

Three of the four things these endpoints do have been production incidents somewhere:

    a liveness probe returning 501 because a catch-all was registered ahead of `/health`
    two services silently speaking different contract versions
    `/config` printing a credential

None of them needs the service to do anything, and all of them are cheap to get right once
and expensive to get right five times.

THE FOURTH IS SPECIFIC TO THIS SYSTEM

`engine_config_fingerprint` covers the settings that decide what a candidate is scored by.
Split across services, nothing makes those agree: a grader running `CODE_APPROACH=C` beside
an orchestrator that believes it is `B` yields a session whose scores were computed one way
and whose stopping rule assumed another — internally consistent, entirely wrong, and no
request fails. Reporting it here turns that into a dashboard field.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from cat_engine.contracts import SCHEMA_VERSION

#: Substrings that mark a setting as a credential. A field NAME test, not a value test —
#: `api_key`, `jwt_secret`, `refresh_token` and `db_password` are caught; a credential
#: embedded in a `database_url` is not. That gap is real and recorded rather than implied:
#: see `services/tests/test_services.py::test_known_gap_*`.
SECRET_MARKERS = ("key", "secret", "token", "password", "credential")


class ServiceSettings(BaseSettings):
    """What every service declares. Extended, never replaced, by each service.

    Deliberately a separate `Settings` per service rather than the engine's shared one: a
    service that can read another's configuration will eventually depend on it. The engine
    keeps its own `Settings` for measurement policy, which is a different question and is
    meant to be identical everywhere — which is what the fingerprint checks.
    """

    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    service_name: str = "service"
    port: int = 8000
    log_level: str = "INFO"
    #: Set by the platform. On `/health` so a mismatched pair shows up in a dashboard
    #: rather than in a decoding error three hops away.
    release: str = "dev"


class HealthResponse(BaseModel):
    service: str
    release: str
    status: str = "ok"
    contract_schema_version: str
    #: Twelve hex characters over the engine settings that decide what a candidate is
    #: scored by. Services whose fingerprints differ are not running the same assessment.
    engine_config_fingerprint: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


def redact(values: dict[str, Any]) -> dict[str, Any]:
    """Configuration minus anything whose NAME looks like a credential."""
    return {
        key: value
        for key, value in values.items()
        if not any(marker in key.lower() for marker in SECRET_MARKERS)
    }


def operator_router(
    settings: ServiceSettings,
    *,
    health_detail: Callable[[], dict[str, Any]] | None = None,
    config_extra: Callable[[], dict[str, Any]] | None = None,
) -> APIRouter:
    """The two operator routes for one service.

    MUST be included before any catch-all. `/{path:path}` matches everything including
    `/health`, FastAPI resolves in registration order, and the symptom of getting that
    wrong is a liveness probe returning 501 during an incident.
    """
    router = APIRouter(tags=["operator"])

    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Liveness, release, and the contract version this build speaks",
    )
    def health() -> HealthResponse:
        from cat_engine.engine.config.fingerprint import engine_config_fingerprint

        return HealthResponse(
            service=settings.service_name,
            release=settings.release,
            status="ok",
            contract_schema_version=SCHEMA_VERSION,
            engine_config_fingerprint=engine_config_fingerprint(),
            detail=health_detail() if health_detail else {},
        )

    @router.get(
        "/config",
        summary="Effective configuration, credentials excluded",
    )
    def config() -> dict[str, Any]:
        from cat_engine.engine.config.fingerprint import measurement_settings

        payload = redact(settings.model_dump())
        payload["engine"] = redact(measurement_settings())
        if config_extra:
            payload.update(redact(config_extra()))
        return payload

    return router
