"""Configuration for bank-registry. Env-only, twelve-factor.

Deliberately a separate `Settings` per service rather than the monolith's shared one: a
service that can read another's configuration will eventually depend on it.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    service_name: str = "bank-registry"
    port: int = 8081
    log_level: str = "INFO"

    # Set by the platform; used for the /health envelope so a mismatched pair is visible
    # in a dashboard rather than in a decoding error three hops away.
    release: str = "dev"


settings = Settings()
