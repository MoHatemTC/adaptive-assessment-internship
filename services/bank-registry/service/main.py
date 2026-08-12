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

from cat_engine.contracts import (
    BankItemFull,
    BankItemRef,
    BankSummary,
    CompetencyGraphDTO,
    ErrorResponse,
    ParityRowDTO,
    PolicyDTO,
)
from adaptive_service import install_error_handlers, operator_router
from adaptive_service.errors import ServiceError
from cat_engine.engine.config.settings import settings as engine_settings
from cat_engine.engine.services.orchestrator import registry
from fastapi import FastAPI, Response

from .config import settings
from .mapping import (
    bank_summary,
    graph_dto,
    item_full,
    item_ref,
    parity_rows,
    policy_dto,
)

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


# --- the write path, retired -----------------------------------------------
#
# GONE, NOT MISSING. `bank-ingest` owns writing a bank, and these four endpoints are what it
# replaced. They return 410 rather than 404 because the difference matters to whoever is
# looking at the response: a 404 says "you have the wrong URL", and the reader goes looking
# for a typo. A 410 naming the replacement says "this moved, here is where", and is the only
# thing a client integrated against the old path will actually read.
#
# WHY WRITING MOVED. This service serves candidate-facing reads on the hot path. It could
# also replace the bank a live assessment was running against, and the only thing standing
# between those two propositions was one environment variable — which is why
# `ADMIN_API_ENABLED` had to exist at all. Authoring is a different security posture and a
# different availability requirement, so it is a different service.
#
# WHAT CHANGED FOR A CALLER. The old surface took a bank AND a hand-authored competency
# graph. The new one takes one file — the questions — and derives the graph, with the
# optional `competencies` block in that file stating the two things questions cannot imply.
# See `docs/bank-schema.md` and ADR-0003.
_MOVED = (
    "writing a bank moved to bank-ingest: PUT /banks/{bank_id} with the bank file as "
    "multipart `file`. One endpoint, one file, and the competency graph is derived from "
    "the questions. See docs/adr/0003-uploaded-banks-and-scoped-assessments.md"
)

GONE: dict[int | str, dict] = {
    410: {"model": ErrorResponse, "description": "writing a bank moved to bank-ingest"}
}


def _gone() -> None:
    raise ServiceError(410, "write_path_moved", _MOVED)


@app.post("/banks/validate", tags=["admin"], responses=GONE, deprecated=True,
          summary="Gone — validate through bank-ingest")
def validate_bank() -> None:
    _gone()


@app.post("/banks", tags=["admin"], responses=GONE, deprecated=True,
          summary="Gone — upload through bank-ingest")
def create_bank() -> None:
    _gone()


@app.put("/banks/{bank_id}", tags=["admin"], responses=GONE, deprecated=True,
         summary="Gone — upload through bank-ingest")
def replace_bank(bank_id: str) -> None:
    _gone()


@app.delete("/banks/{bank_id}", tags=["admin"], responses=GONE, deprecated=True,
            summary="Gone — deregister through bank-ingest")
def delete_bank(bank_id: str) -> None:
    _gone()
