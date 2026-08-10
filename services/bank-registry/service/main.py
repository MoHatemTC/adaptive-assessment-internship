"""bank-registry — Items, banks, competency graphs and the propagation policy.

BOILERPLATE. The routes below are declared and return 501. They are here so the contract is
reviewable before any code moves, and so a client can be written against a running service
rather than against a document.

Migration checklist is in MIGRATION.md; the modules it names still live in `backend/`.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from adaptive_contracts import SCHEMA_VERSION

from .config import settings

app = FastAPI(title=settings.service_name, version=settings.release)


@app.get("/health")
def health() -> dict:
    """Liveness plus the contract version this build speaks."""
    return {
        "service": settings.service_name,
        "release": settings.release,
        "contract_schema_version": SCHEMA_VERSION,
        "status": "ok",
    }


@app.get("/config")
def config() -> dict:
    """Effective configuration, secrets excluded. What an operator checks first."""
    return {
        k: v
        for k, v in settings.model_dump().items()
        if not any(s in k for s in ("key", "secret", "token", "password"))
    }


# Planned surface. Each returns 501 until the module named in MIGRATION.md moves here.
    #   GET   /banks                                           registered banks with item counts and modalities
    #   GET   /banks/{bank_id}/items                           BankItemRef list — parameters, never payloads
    #   GET   /banks/{bank_id}/items/{item_id}/payload         the modality payload, for rendering and grading
    #   GET   /banks/{bank_id}/graph                           nodes and edges
    #   GET   /banks/{bank_id}/policy                          resolved propagation policy, per edge, with reasons
@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
def not_yet_implemented(path: str) -> JSONResponse:
    return JSONResponse(
        status_code=501,
        content={
            "detail": "boilerplate: this service does not implement any route yet",
            "service": settings.service_name,
            "see": "services/bank-registry/MIGRATION.md",
        },
    )
