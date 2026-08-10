"""bank-registry — items, banks, competency graphs and the propagation policy.

TWO READ PATHS, ON PURPOSE

`GET /banks/{id}/items` returns parameters and never payloads; `GET /banks/{id}/items/{id}`
returns the payload too. A caller that only ranks should not be able to read a question,
and that is a property of which endpoint it is allowed to call rather than of what it
chooses to look at. The orchestrator uses the first for every decision and the second only
for the item it is about to present; the grader uses only the second.

A WRITE PATH, WHICH IS NEW

Banks used to be a dict in a Python file. They are content, not code, so another service
can now register one. Everything a posted bank has to satisfy is checked BEFORE it is
written — the same invariants the engine's own suite asserts of the five checked-in banks,
because otherwise that suite is testing the seeds rather than the system.

NO AUTHENTICATION. This carries the monolith's posture forward unchanged and is recorded
as a gap in ADR-0002 and docs/operations.md, not implied. `ADMIN_API_ENABLED=false` turns
the write path off without turning off the service, because a read-only registry and one
that can replace the bank a live assessment is running against are different propositions.
"""

from __future__ import annotations

import logging

from adaptive_contracts import (
    BankItemFull,
    BankItemRef,
    BankSubmission,
    BankSummary,
    BankValidationReport,
    CompetencyGraphDTO,
    ErrorResponse,
    ParityRowDTO,
    PolicyDTO,
)
from adaptive_service import install_error_handlers, operator_router
from adaptive_service.errors import ServiceError
from app.config.settings import settings as engine_settings
from app.services.orchestrator import registry
from fastapi import FastAPI, Response

from .config import settings
from .mapping import (
    bank_summary,
    graph_dto,
    item_full,
    item_ref,
    parity_rows,
    policy_dto,
    submission_to_bank_item,
    validation_report,
)

logger = logging.getLogger(__name__)

RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "no such bank or item"},
    503: {"model": ErrorResponse, "description": "the write path is disabled"},
}

app = FastAPI(
    title="bank-registry",
    version=settings.release,
    summary="Items, banks, competency graphs and the propagation policy.",
    description=__doc__,
)
install_error_handlers(app)

# FIRST. `/health` must not be shadowed by anything registered later — the symptom of
# getting this wrong is a liveness probe returning 501 during an incident.
app.include_router(
    operator_router(
        settings,
        health_detail=lambda: {
            "banks": len(registry.REGISTRY),
            "store_dir": str(registry.STORE.store_dir),
            "admin_api_enabled": settings.admin_api_enabled,
        },
        config_extra=lambda: {
            "engine_data_dir": engine_settings.engine_data_dir,
            "bank_store_dir": str(registry.STORE.store_dir),
        },
    )
)


def _bank_or_404(bank_id: str):
    try:
        return registry.resolve_bank_id(bank_id)
    except KeyError as exc:
        raise ServiceError(404, "bank_unknown", str(exc)) from exc


def _require_admin() -> None:
    if not settings.admin_api_enabled:
        raise ServiceError(
            503,
            "admin_api_disabled",
            "the bank write path is disabled on this deployment (ADMIN_API_ENABLED)",
        )


# --- read ------------------------------------------------------------------
@app.get(
    "/banks",
    response_model=list[BankSummary],
    tags=["banks"],
    summary="Every registered bank, seeded or posted",
)
def list_banks() -> list[BankSummary]:
    return [bank_summary(row) for row in registry.describe()]


@app.get(
    "/banks/{bank_id}",
    response_model=BankSummary,
    tags=["banks"],
    responses=RESPONSES,
    summary="One bank's profile and version",
)
def get_bank(bank_id: str, response: Response) -> BankSummary:
    resolved = _bank_or_404(bank_id)
    row = next(r for r in registry.describe() if r["bank_id"] == resolved)
    summary = bank_summary(row)
    response.headers["ETag"] = f'"{summary.version}"'
    return summary


@app.get(
    "/banks/{bank_id}/items",
    response_model=list[BankItemRef],
    tags=["banks"],
    responses=RESPONSES,
    summary="Item parameters for ranking. Never payloads.",
    description=(
        "What SELECTION needs: a, b, c, which variables each item measures and how long it "
        "takes. No stem, no options, no answer, no test cases. A caller with only this "
        "endpoint can rank the whole pool and read no question."
    ),
)
def list_items(bank_id: str, response: Response) -> list[BankItemRef]:
    resolved = _bank_or_404(bank_id)
    response.headers["ETag"] = f'"{registry.version(resolved)}"'
    return [item_ref(i) for i in registry.get_bank(resolved).all_items()]


@app.get(
    "/banks/{bank_id}/items/{item_id}",
    response_model=BankItemFull,
    tags=["banks"],
    responses=RESPONSES,
    summary="One item including its modality payload, for rendering and grading",
)
def get_item(bank_id: str, item_id: str) -> BankItemFull:
    resolved = _bank_or_404(bank_id)
    found = registry.get_bank(resolved).get(item_id)
    if found is None:
        raise ServiceError(404, "item_unknown", f"no item {item_id!r} in bank {resolved}")
    return item_full(found)


@app.get(
    "/banks/{bank_id}/graph",
    response_model=CompetencyGraphDTO,
    tags=["graph"],
    responses=RESPONSES,
    summary="The competency graph paired with this bank",
)
def get_graph(bank_id: str, response: Response) -> CompetencyGraphDTO:
    resolved = _bank_or_404(bank_id)
    service = registry.get_graph_service(resolved)
    if service is None:
        raise ServiceError(404, "graph_absent", f"bank {resolved} declares no graph")
    response.headers["ETag"] = f'"{registry.version(resolved)}"'
    return graph_dto(service.graph)


@app.get(
    "/banks/{bank_id}/policy",
    response_model=PolicyDTO,
    tags=["graph"],
    responses=RESPONSES,
    summary="The resolved propagation policy, per edge, with reasons",
    description=(
        "Deployment, bank and edge permissions already combined, so an operator can ask "
        "why a given edge is inert without reading three files and doing the AND in their "
        "head. `*_allowed` includes the deployment switch; `*_authorised_by_bank` does not."
    ),
)
def get_policy(bank_id: str) -> PolicyDTO:
    resolved = _bank_or_404(bank_id)
    policy = registry.get_propagation_policy(resolved)
    if policy is None:
        raise ServiceError(404, "graph_absent", f"bank {resolved} declares no graph")
    return policy_dto(policy)


@app.get(
    "/banks/{bank_id}/coverage",
    tags=["banks"],
    responses=RESPONSES,
    summary="Item counts per main competency and modality",
)
def get_coverage(bank_id: str) -> dict[str, dict[str, int]]:
    return registry.get_bank(_bank_or_404(bank_id)).coverage()


@app.get(
    "/banks/{bank_id}/parity",
    response_model=list[ParityRowDTO],
    tags=["banks"],
    responses=RESPONSES,
    summary="Information parity: which modality wins each variable, and why",
    description=(
        "Separates the two reasons a modality can lose a ranking. `rarely_selected` means "
        "it loses WITH loading applied, which is correct — an item loading 0.2 on a "
        "variable genuinely tells you less. `miscalibrated` means it loses even at full "
        "loading, which means the item parameters are wrong."
    ),
)
def get_parity(bank_id: str) -> list[ParityRowDTO]:
    return parity_rows(registry.get_bank(_bank_or_404(bank_id)).parity_report())


# --- write -----------------------------------------------------------------
def _validate(submission: BankSubmission):
    return registry.STORE.validate(
        bank_id=submission.bank_id,
        items=[submission_to_bank_item(i.model_dump()) for i in submission.items],
        # `by_alias` so edges serialise as `from`/`to` — the shape the graph file uses and
        # the parser reads. Without it every posted graph is rejected as unparseable.
        graph=submission.graph.model_dump(by_alias=True) if submission.graph else None,
        coverage_critical_only=submission.coverage_critical_only,
        question_budget=engine_settings.cat_max_questions,
        deployment_critical_only=engine_settings.graph_coverage_critical_only,
    )


def _write(submission: BankSubmission, validation) -> BankValidationReport:
    registry.STORE.save(
        bank_id=submission.bank_id,
        title=submission.title,
        items=[submission_to_bank_item(i.model_dump()) for i in submission.items],
        # `by_alias` so edges serialise as `from`/`to` — the shape the graph file uses and
        # the parser reads. Without it every posted graph is rejected as unparseable.
        graph=submission.graph.model_dump(by_alias=True) if submission.graph else None,
        coverage_critical_only=submission.coverage_critical_only,
        validation=validation,
    )
    logger.info(
        "registered bank %s: %d items, mains %s, version %s",
        submission.bank_id,
        validation.items,
        validation.mains,
        registry.version(submission.bank_id),
    )
    return validation_report(validation, version=registry.version(submission.bank_id))


@app.post(
    "/banks/validate",
    response_model=BankValidationReport,
    tags=["admin"],
    responses=RESPONSES,
    summary="Check a bank without registering it",
    description=(
        "Same checks as a registration, no write. Intended for an authoring tool: the "
        "findings name the item, node or main at fault, so a bank can be fixed before it "
        "is anywhere near a candidate."
    ),
)
def validate_bank(submission: BankSubmission) -> BankValidationReport:
    _require_admin()
    return validation_report(_validate(submission))


@app.post(
    "/banks",
    response_model=BankValidationReport,
    status_code=201,
    tags=["admin"],
    responses={
        **RESPONSES,
        409: {"model": ErrorResponse, "description": "that bank id is already registered"},
        422: {"model": ErrorResponse, "description": "the bank failed validation"},
    },
    summary="Register a new bank",
    description=(
        "Refuses an id that already exists, INCLUDING one of the five checked-in banks. "
        "Replacing an existing bank is a PUT, so overwriting one is never something a "
        "retried POST can do by accident."
    ),
)
def create_bank(submission: BankSubmission, response: Response) -> BankValidationReport:
    _require_admin()
    if submission.bank_id in registry.REGISTRY:
        raise ServiceError(
            409,
            "bank_exists",
            f"bank {submission.bank_id!r} is already registered; PUT to replace it",
        )
    validation = _validate(submission)
    if not validation.accepted:
        raise ServiceError(
            422,
            "bank_invalid",
            "; ".join(f"{f.code}: {f.message}" for f in validation.errors),
        )
    report = _write(submission, validation)
    response.headers["ETag"] = f'"{report.version}"'
    return report


@app.put(
    "/banks/{bank_id}",
    response_model=BankValidationReport,
    tags=["admin"],
    responses={
        **RESPONSES,
        409: {"model": ErrorResponse, "description": "the body names a different bank"},
        422: {"model": ErrorResponse, "description": "the bank failed validation"},
    },
    summary="Replace a bank, creating a new version",
    description=(
        "Sessions already in flight are unaffected: each pins the bank version it began "
        "under, so a replacement changes what the NEXT assessment sees and nothing about "
        "one already running."
    ),
)
def replace_bank(
    bank_id: str, submission: BankSubmission, response: Response
) -> BankValidationReport:
    _require_admin()
    if submission.bank_id != bank_id:
        raise ServiceError(
            409,
            "bank_id_mismatch",
            f"path says {bank_id!r} and body says {submission.bank_id!r}",
        )
    validation = _validate(submission)
    if not validation.accepted:
        raise ServiceError(
            422,
            "bank_invalid",
            "; ".join(f"{f.code}: {f.message}" for f in validation.errors),
        )
    shadowing_a_seed = (
        bank_id in registry.REGISTRY
        and registry.REGISTRY[bank_id].source == "seed"
    )
    report = _write(submission, validation)
    if shadowing_a_seed:
        logger.warning(
            "bank %s now shadows a checked-in bank of the same id; DELETE the stored "
            "copy to restore it",
            bank_id,
        )
    response.headers["ETag"] = f'"{report.version}"'
    return report


@app.delete(
    "/banks/{bank_id}",
    tags=["admin"],
    responses=RESPONSES,
    summary="Deregister a posted bank",
    description=(
        "Removes the stored copy only. If it was shadowing a checked-in bank of the same "
        "id, that one reappears — which is what makes an accidental overwrite recoverable "
        "without a redeploy."
    ),
)
def delete_bank(bank_id: str) -> dict:
    _require_admin()
    if not registry.STORE.is_stored(bank_id):
        raise ServiceError(
            404,
            "bank_not_stored",
            f"bank {bank_id!r} was not posted to this registry; checked-in banks are "
            "removed by changing the deployment, not by an API call",
        )
    registry.STORE.delete(bank_id)
    restored = bank_id in registry.REGISTRY
    return {"deleted": bank_id, "seed_restored": restored}
