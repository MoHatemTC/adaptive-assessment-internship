"""competency-scope — a selection of competencies becomes a sub-graph and an allowlist.

WHAT THIS SERVICE IS FOR

An assessment used to be a whole bank. `POST /assessments` accepted `target_variables`, but
the engine checks them against `bank.variables()`, which returns MAIN competencies only — so
`["C1", "C3"]` worked and `["C1.1", "C1.4"]` was refused as "no bank coverage". Everything
below exists to make the second one expressible, and to make it expressible without the
engine having to learn a new concept.

IT RETURNS ITEM IDS, NEVER ITEMS

`bank-registry` serves items, through two endpoints with deliberately different
authorisation — one that ranks and cannot read a question, one that renders. A scope service
returning questions would be a second item-serving path with a third answer to "what is in
this bank". It is also unnecessary: the orchestrator already holds the whole parameter pool
for the bank version, so an id allowlist costs a set intersection and no hop at all.

IT CANNOT MOVE A POSTERIOR

There is no score, no weight and no theta in the response. A scope changes which questions
may be asked and what coverage requires. As with `competency-graph`, the worst a compromised
or buggy scope service can do is narrow a pool — a quality-of-measurement problem, stated in
the report, rather than a wrong number nobody can see.

STATELESS, AND THAT IS WHAT MAKES THE IDS TRUSTWORTHY

The manifest is a pure function of the bank version and the normalised selection, so
`scope_id` is a hash of its own inputs. A client previews a scope here, the orchestrator
rebuilds it at session start, and the two agree by construction rather than by a shared
cache. Nothing is stored, this scales horizontally for free, and a restart loses nothing.
"""

from __future__ import annotations

import logging

from adaptive_clients import BankRegistryClient, ServiceRefused, ServiceUnavailable
from adaptive_contracts import ErrorResponse, ScopeManifest, ScopeRequest
from adaptive_service import install_error_handlers, operator_router
from adaptive_service.errors import ServiceError
from cat_engine.engine.config.settings import settings as engine_settings
from fastapi import FastAPI, Response

from .config import settings
from .render import render_mermaid
from .scoping import build_scope

logger = logging.getLogger(__name__)

RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "no such bank, or it declares no graph"},
    503: {"model": ErrorResponse, "description": "the bank registry is unreachable"},
}

app = FastAPI(
    title="competency-scope",
    version=settings.release,
    summary="A selection of competencies becomes a sub-graph and an item allowlist.",
    description=__doc__,
)
install_error_handlers(app)

_bank = BankRegistryClient(
    settings.bank_registry_url, timeout=settings.bank_timeout_seconds
)

# FIRST. `/health` must not be shadowed by anything registered later — the symptom of
# getting this wrong is a liveness probe returning 501 during an incident.
app.include_router(
    operator_router(
        settings,
        health_detail=lambda: {
            "bank_registry_url": settings.bank_registry_url,
            "question_budget": engine_settings.cat_max_questions,
            "stateful": False,
        },
        config_extra=lambda: {"bank_registry_url": settings.bank_registry_url},
    )
)


def _manifest(request: ScopeRequest) -> ScopeManifest:
    """Fetch what the bank declares, and induce the scope over it."""
    try:
        summary = _bank.bank(request.bank_id)
        graph = _bank.graph(request.bank_id)
        version, items = _bank.items(request.bank_id)
    except ServiceRefused as exc:
        raise ServiceError(404, exc.code or "bank_unknown", exc.detail) from exc
    except ServiceUnavailable as exc:
        raise ServiceError(
            503,
            "bank_registry_unavailable",
            f"could not read bank {request.bank_id}: {exc}",
        ) from exc

    if graph is None:
        # Without a graph there are no sub-competency nodes to select, so a scope would be
        # an allowlist with nothing behind it. Refused rather than silently degraded to
        # "the whole bank", which is what a caller would get and not what they asked for.
        raise ServiceError(
            404,
            "graph_absent",
            f"bank {request.bank_id} declares no competency graph, so it has no "
            "sub-competencies to scope to",
        )

    critical_only = (
        summary.coverage_critical_only
        if request.critical_only is None
        else request.critical_only
    )
    manifest = build_scope(
        request=request,
        bank_version=version or summary.version,
        graph=graph,
        items=items,
        critical_only=critical_only,
        question_budget=engine_settings.cat_max_questions,
    )
    logger.info(
        "scope %s over %s@%s: %d nodes, %d items, mains=%s, reachable=%s",
        manifest.scope_id,
        manifest.bank_id,
        manifest.bank_version,
        len(manifest.nodes),
        len(manifest.item_ids),
        [m.main for m in manifest.mains],
        manifest.coverage.reachable,
    )
    return manifest


@app.post(
    "/scopes",
    response_model=ScopeManifest,
    tags=["scope"],
    responses=RESPONSES,
    summary="Induce the sub-graph of a selection, and the items that measure it",
    description=(
        "Answers three questions at once, because a caller that got only one of them "
        "would have to guess the others: which nodes are in play, which items may be "
        "administered, and whether the result can actually be assessed within the "
        "question budget.\n\n"
        "An unassessable scope still returns 200 with `coverage.reachable = false` and "
        "the offending competencies named. Refusing outright would be worse: the caller "
        "is a picker trying to help somebody build a valid selection, and the useful "
        "answer is which competency to add or drop."
    ),
)
def create_scope(request: ScopeRequest, response: Response) -> ScopeManifest:
    manifest = _manifest(request)
    response.headers["ETag"] = f'"{manifest.scope_hash}"'
    return manifest


@app.post(
    "/scopes/graph",
    tags=["scope"],
    responses=RESPONSES,
    summary="The induced sub-graph, drawn",
    description=(
        "The same scope as `POST /scopes`, rendered as mermaid. Served by the service "
        "that induced it so that the drawing and the allowlist cannot disagree — a client "
        "redrawing the graph from the manifest would be a second implementation of the "
        "induction rules.\n\n"
        "Excluded siblings are drawn greyed rather than omitted: a scope is a statement "
        "about what is NOT being measured at least as much as about what is, and a picture "
        "of the retained nodes alone makes two-of-seven look like a whole competency."
    ),
)
def render_scope(request: ScopeRequest) -> Response:
    return Response(
        content=render_mermaid(_manifest(request)),
        media_type="text/vnd.mermaid",
    )
