"""Configuration for bank-registry.

Extends the shared `ServiceSettings` rather than redeclaring it, so `/health` and `/config`
behave identically across every service and only what is genuinely specific to this one
lives here.
"""

from __future__ import annotations

from adaptive_service import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "bank-registry"
    port: int = 8081

    #: The write path — `POST/PUT/DELETE /banks` — in one switch.
    #:
    #: There is no authentication anywhere in this system (see ADR-0002 and
    #: docs/operations.md); that is the status quo carried forward from the monolith, not a
    #: decision this migration made. But a read-only API and one that can REPLACE the bank
    #: a live assessment is running against are different propositions, so the second is
    #: something a deployment can turn off without turning off the service.
    admin_api_enabled: bool = True


settings = Settings()
