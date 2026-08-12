"""bank-ingest — one uploaded file becomes a registered bank.

ONE WRITER, MANY READERS

`bank-registry` used to own the write path, gated by `ADMIN_API_ENABLED`. That flag exists
because the service candidates read from on the hot path could also replace the bank a live
assessment is running against — two very different propositions behind one door. Writing
moves here: an authoring concern with its own security posture, its own availability
requirement, and no candidate traffic at all.

ONE FILE, ONE ENDPOINT

An author uploads questions. They do not author a competency graph and are never asked for
one, so the graph is DERIVED from the items — nodes from `measures[].variable`, titles from
each item's own `sub_competency` label, weights from item co-measurement, and every
prerequisite edge inert. See `derive.py`, which is where that modelling decision lives and
the only file that has to change to replace it.

`PUT /banks/{bank_id}` is the whole write surface. Register and replace used to be separate
verbs, and validate a third endpoint; the difference between them was something a client had
to know before it could act, and a retried POST could fail for having succeeded. A bank is
identified by its id and its content, so uploading one is idempotent by nature — the same
bytes under the same id produce the same version whether it is the first upload or the
fifth. `dry_run=true` is the same path without the write.

VALIDATION IS REUSED, NOT REIMPLEMENTED

`BankStore.validate` is the same function the checked-in banks are asserted against. A bank
arriving as an upload clears exactly the bar the seeds clear — otherwise the engine's own
suite is testing the seeds rather than the system.

STILL NO AUTHENTICATION. Carried forward from the write path this replaces rather than
introduced here, and recorded as a gap in `docs/operations.md`. `INGEST_API_ENABLED=false`
stops uploads without stopping the service.
"""

from __future__ import annotations

import json
import logging

from cat_engine.contracts import (
    BankValidationReport,
    DerivedGraphDTO,
    ErrorResponse,
    UploadReceipt,
    ValidationFinding,
)
from adaptive_service import install_error_handlers, operator_router
from adaptive_service.errors import ServiceError
from cat_engine.engine.config.settings import settings as engine_settings
from cat_engine.engine.services.orchestrator import registry
from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .derive import derive_graph, parse_declaration, summarise
from .uploads import Upload, UploadStore

logger = logging.getLogger(__name__)


def _install_store() -> None:
    """Point the process at Postgres when this deployment has one.

    Imported lazily and only on this branch, so an image without `adaptive-store` installed
    — every service that has no business holding a database driver — cannot fail at import
    for a dependency it does not use.
    """
    dsn = settings.bank_database_url.strip()
    if not dsn:
        logger.info("BANK_DATABASE_URL is unset — banks are resolved from files")
        return
    from cat_engine.stores.sql.backed import install

    install(dsn)
    logger.info("banks are resolved from Postgres")


_install_store()

RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "no such upload or bank"},
    503: {"model": ErrorResponse, "description": "the ingest path is disabled"},
}

app = FastAPI(
    title="bank-ingest",
    version=settings.release,
    summary="One uploaded file becomes a registered bank. The only writer.",
    description=__doc__,
)
install_error_handlers(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOADS = UploadStore(maximum=settings.max_retained_uploads)

# FIRST. `/health` must not be shadowed by anything registered later — the symptom of
# getting this wrong is a liveness probe returning 501 during an incident.
app.include_router(
    operator_router(
        settings,
        health_detail=lambda: {
            "store_dir": str(registry.STORE.store_dir),
            "ingest_api_enabled": settings.ingest_api_enabled,
            "uploads_retained": len(UPLOADS),
            "relation_threshold": settings.relation_threshold,
        },
        config_extra=lambda: {"bank_store_dir": str(registry.STORE.store_dir)},
    )
)


def _require_enabled() -> None:
    if not settings.ingest_api_enabled:
        raise ServiceError(
            503,
            "ingest_api_disabled",
            "bank ingest is disabled on this deployment (INGEST_API_ENABLED)",
        )


def _report(validation, *, version: str = "") -> BankValidationReport:
    return BankValidationReport(
        bank_id=validation.bank_id,
        accepted=validation.accepted,
        version=version,
        items=validation.items,
        mains=list(validation.mains),
        modalities=list(validation.modalities),
        findings=[
            ValidationFinding(
                severity=f.severity, code=f.code, message=f.message, subject=f.subject
            )
            for f in validation.findings
        ],
    )


def _parse(raw: bytes) -> tuple[list[dict], str, dict]:
    """The uploaded bytes into items, a title, and whatever the bank declares about itself.

    Both shapes the engine's own parser accepts: `{schema_version, competency, items}` and
    a bare list. Nothing else — a bank is JSON, and guessing at a spreadsheet would mean
    inventing a second bank schema and maintaining it forever.

    The optional `competencies` block is the one addition, and it is what lets an author
    say which sub-competencies are critical and which serve more than one main — the two
    things a file of questions cannot imply. Still one file, and still not a graph: it has
    no edges. See `derive.py`.
    """
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError("the file is not UTF-8 text") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"the file is not valid JSON: {exc}") from exc

    declared: dict = {}
    if isinstance(parsed, dict):
        items = parsed.get("items")
        title = str(parsed.get("competency") or "").strip()
        declared = parse_declaration(parsed.get("competencies"))
    elif isinstance(parsed, list):
        items, title = parsed, ""
    else:
        raise ValueError(
            "expected a bank object with an `items` list, or a bare list of items"
        )

    if not isinstance(items, list) or not items:
        raise ValueError("this file declares no items")
    if not all(isinstance(i, dict) for i in items):
        raise ValueError("every item must be an object")
    return items, title, declared


def _ingest(upload: Upload, bank_id: str, *, write: bool) -> UploadReceipt:
    """Parse, derive, validate, and — when asked — write. In that order, and all of it here.

    `write=False` is `dry_run=true`: the identical path with the last step omitted, rather
    than a second endpoint that could drift from the one that actually registers a bank.
    """
    receipt = upload.receipt
    receipt.bank_id = bank_id
    receipt.status = "parsing"

    try:
        items, title, declared = _parse(upload.raw)
    except ValueError as exc:
        receipt.status = "rejected"
        receipt.error = str(exc)
        return receipt

    receipt.items = len(items)
    receipt.title = title or bank_id
    receipt.status = "deriving"
    graph = derive_graph(
        bank_id,
        items,
        declared=declared,
        relation_threshold=settings.relation_threshold,
        edge_floor=settings.edge_floor,
    )
    upload.derived = graph
    receipt.derived_graph = DerivedGraphDTO.model_validate(summarise(graph))

    # WHETHER THE CRITICAL SET MEANS ANYTHING FOR THIS BANK.
    #
    # With no declaration every derived node is critical, so critical-only and full coverage
    # are the same requirement and the flag is inert. When the bank narrows the set, the
    # flag is what makes the narrowing take effect — without it the gate would still require
    # every node and the declaration would be decoration.
    #
    # Derived rather than configured, and stated on the profile, so the bank is validated
    # against exactly the rule it will be gated by.
    critical_only = any(
        not node.get("critical", True)
        for node in graph["nodes"]
        if node["node_type"] == "sub_competency"
    )

    receipt.status = "validating"
    validation = registry.STORE.validate(
        bank_id=bank_id,
        items=items,
        graph=graph,
        coverage_critical_only=critical_only,
        question_budget=engine_settings.cat_max_questions,
        deployment_critical_only=engine_settings.graph_coverage_critical_only,
    )
    receipt.validation = _report(validation)

    if not validation.accepted:
        receipt.status = "rejected"
        receipt.error = "; ".join(f"{f.code}: {f.message}" for f in validation.errors)
        return receipt

    if not write:
        receipt.status = "validating"
        return receipt

    registry.STORE.save(
        bank_id=bank_id,
        title=receipt.title,
        items=items,
        graph=graph,
        coverage_critical_only=critical_only,
        validation=validation,
    )
    registry.reset_caches()
    receipt.version = registry.version(bank_id)
    receipt.validation = _report(validation, version=receipt.version)
    receipt.status = "registered"
    logger.info(
        "registered bank %s from upload %s: %d items, mains %s, version %s",
        bank_id,
        upload.upload_id,
        validation.items,
        validation.mains,
        receipt.version,
    )
    return receipt


async def _read(file: UploadFile) -> bytes:
    raw = await file.read()
    if not raw:
        raise ServiceError(422, "upload_empty", "the uploaded file is empty")
    if len(raw) > settings.max_upload_bytes:
        raise ServiceError(
            413,
            "upload_too_large",
            f"{len(raw)} bytes exceeds the {settings.max_upload_bytes}-byte ceiling",
        )
    return raw


@app.put(
    "/banks/{bank_id}",
    response_model=UploadReceipt,
    tags=["ingest"],
    responses={
        **RESPONSES,
        413: {"model": ErrorResponse, "description": "the file is too large"},
        422: {"model": ErrorResponse, "description": "the bank failed validation"},
    },
    summary="Upload a bank of questions. The only way a bank enters the system.",
    description=(
        "ONE FILE: the questions. The competency graph is DERIVED from them — an author "
        "does not write one and is not asked for one. The optional `competencies` block in "
        "the file states the two things questions cannot imply: which sub-competencies are "
        "critical, and which serve more than one main.\n\n"
        "ONE ENDPOINT, AND IT IS A PUT. Registering and replacing a bank were separate "
        "verbs, which made the difference between them a thing a client had to know before "
        "it could act — and made a retried POST able to fail for having succeeded. A bank "
        "is identified by its id and its content, so uploading one is idempotent by "
        "nature: the same bytes under the same id produce the same version, whether that "
        "is the first upload or the fifth.\n\n"
        "Sessions already in flight are unaffected. Each pins the bank version it began "
        "under, so a replacement changes what the NEXT assessment sees and nothing about "
        "one already running.\n\n"
        "`dry_run=true` runs the identical path and writes nothing — the receipt still "
        "carries the derived graph, which is the part an author did not upload and cannot "
        "otherwise see."
    ),
)
async def upload_bank(
    bank_id: str,
    dry_run: bool = False,
    file: UploadFile = File(...),  # noqa: B008 - FastAPI marker
) -> UploadReceipt:
    _require_enabled()
    upload = UPLOADS.new(file.filename or "bank.json", await _read(file))
    receipt = _ingest(upload, bank_id, write=not dry_run)
    if receipt.status == "rejected":
        raise ServiceError(422, "bank_invalid", receipt.error)
    if dry_run:
        logger.info("validated %s without writing: %d items", bank_id, receipt.items)
    elif registry.STORE.is_stored(bank_id) and bank_id in registry.REGISTRY:
        # A bank that shadows a checked-in one of the same id. Worth a line in the log,
        # because DELETE is what restores the seed and nothing else says it is shadowed.
        logger.warning(
            "bank %s is stored over a checked-in bank of the same id; DELETE the stored "
            "copy to restore it",
            bank_id,
        )
    return receipt


@app.get(
    "/uploads",
    response_model=list[UploadReceipt],
    tags=["ingest"],
    summary="Recent uploads, newest first",
)
def list_uploads(limit: int = 50) -> list[UploadReceipt]:
    return [u.receipt for u in UPLOADS.recent(limit)]


@app.get(
    "/uploads/{upload_id}",
    response_model=UploadReceipt,
    tags=["ingest"],
    responses=RESPONSES,
    summary="What one upload became, and why",
)
def get_upload(upload_id: str) -> UploadReceipt:
    upload = UPLOADS.get(upload_id)
    if upload is None:
        raise ServiceError(404, "upload_unknown", f"no upload {upload_id!r}")
    return upload.receipt


@app.delete(
    "/banks/{bank_id}",
    tags=["ingest"],
    responses=RESPONSES,
    summary="Deregister an uploaded bank",
    description=(
        "Removes the stored copy only. If it was shadowing a checked-in bank of the same "
        "id, that one reappears — which is what makes an accidental overwrite recoverable "
        "without a redeploy."
    ),
)
def delete_bank(bank_id: str) -> dict:
    _require_enabled()
    if not registry.STORE.is_stored(bank_id):
        raise ServiceError(
            404,
            "bank_not_stored",
            f"bank {bank_id!r} was not uploaded to this deployment; checked-in banks are "
            "removed by changing the deployment, not by an API call",
        )
    registry.STORE.delete(bank_id)
    registry.reset_caches()
    return {"deleted": bank_id, "seed_restored": bank_id in registry.REGISTRY}
