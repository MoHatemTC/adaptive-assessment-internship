"""competency-graph — node state, propagation, coverage. Never a posterior.

STATELESS, AND THAT IS A DECISION

`MIGRATION.md` planned `POST /sessions/{id}/evidence`, with this service owning per-session
node state. It does not. The property the whole architecture rests on is that the
orchestrator holds nothing between calls, so an assessment can be persisted between
requests and resumed on a different worker — and that only works while `AssessmentState` is
the single source of truth. A graph service keeping its own copy of the same session's node
state creates a second one, and the two disagree the first time a request is retried.

So a caller sends what it knows and gets back what changed. Nothing is stored, the service
scales horizontally for free, and a restart loses nothing.

WHAT THIS SERVICE CANNOT DO

It cannot move a candidate's estimate. `PropagateResponse` carries a state update and a set
of mains whose eligibility changed; `InferredSignalDTO` has no `score` and no `weight`
field. That is structural rather than stylistic: a deduction FROM a response is not a
second response, and multiplying it back in counts one answer twice — with the damage
landing on the standard error, which is what the assessment stops on. It was measured at
1.80x weight inflation on the specification's own worked example.

The worst a compromised or buggy graph service can do is change which question is asked
next. That is a quality-of-service problem rather than a correctness one, and it is the
reason this decomposition is safe where a naive one would not be.
"""

from __future__ import annotations

import logging

from adaptive_clients import BankRegistryClient, ServiceRefused, ServiceUnavailable
from adaptive_clients.engine import HttpGraphSource
from adaptive_contracts import (
    CoverageRequest,
    CoverageResponse,
    ErrorResponse,
    InferredSignalDTO,
    ManifestResponse,
    PropagateRequest,
    PropagateResponse,
)
from adaptive_service import install_error_handlers, operator_router
from adaptive_service.errors import ServiceError
from cat_engine.engine.schemas.orchestration import AssessmentState, BankItem, GradedResponse
from cat_engine.engine.services.competency_graph import config as graph_config
from cat_engine.engine.services.competency_graph.coverage import sub_nodes_for_main
from cat_engine.engine.services.orchestrator.propagation_port import build_manifest, propagate
from fastapi import FastAPI

from .config import settings

logger = logging.getLogger(__name__)

RESPONSES: dict[int | str, dict] = {
    404: {"model": ErrorResponse, "description": "no such bank, or it declares no graph"},
    503: {"model": ErrorResponse, "description": "the bank registry is unreachable"},
}

app = FastAPI(
    title="competency-graph",
    version=settings.release,
    summary="Node state, propagation and coverage. Never a posterior.",
    description=__doc__,
)
install_error_handlers(app)

_graphs = HttpGraphSource(
    BankRegistryClient(settings.bank_registry_url, timeout=settings.bank_timeout_seconds)
)

app.include_router(
    operator_router(
        settings,
        health_detail=lambda: {
            "bank_registry_url": settings.bank_registry_url,
            "graph_enabled": graph_config.graph_enabled(),
            "stateful": False,
        },
        config_extra=lambda: {"bank_registry_url": settings.bank_registry_url},
    )
)


def _graph_for(bank_id: str):
    try:
        graph = _graphs.service(bank_id)
    except ServiceRefused as exc:
        raise ServiceError(404, exc.code or "bank_unknown", exc.detail) from exc
    except ServiceUnavailable as exc:
        raise ServiceError(
            503,
            "bank_registry_unavailable",
            f"could not read the graph for {bank_id}: {exc}",
        ) from exc
    if graph is None:
        raise ServiceError(404, "graph_absent", f"bank {bank_id} declares no graph")
    return graph


def _state_from(request: PropagateRequest) -> AssessmentState:
    """The session's graph slice, as the engine's own state object.

    Only the `graph_*` fields and the session id are populated, because only those are
    sent — this service is never told what a candidate scored on anything, only what one
    response said about the nodes it touched.
    """
    return AssessmentState.model_validate(
        {
            "session_id": request.session_id,
            **{k: v for k, v in request.graph_state.items() if k.startswith("graph_")},
        }
    )


@app.post(
    "/propagate",
    response_model=PropagateResponse,
    tags=["propagation"],
    responses=RESPONSES,
    summary="Apply one response's outcomes to the graph, all or nothing",
    description=(
        "Compute, then merge. A response can produce several evidence events, and applying "
        "them one at a time straight into session state means a failure partway through "
        "leaves the session in a state no sequence of responses could have produced — and, "
        "because the evidence id was marked processed first, one that cannot be retried.\n\n"
        "Idempotent. Evidence ids are deterministic, so replaying a response already "
        "absorbed changes nothing."
    ),
)
def propagate_response(request: PropagateRequest) -> PropagateResponse:
    if not graph_config.graph_enabled():
        return PropagateResponse(applied=False, detail="the graph layer is disabled")

    graph = _graph_for(request.bank_id)
    minimum_failures = _graphs.minimum_failures_to_block(request.bank_id)

    state = _state_from(request)
    item = BankItem.model_validate(
        {
            "item_id": request.item.item_id,
            "modality": request.item.modality,
            "measures": [{"variable": o.variable, "weight": 1.0} for o in request.outcomes]
            or [{"variable": "unknown", "weight": 1.0}],
            "cat": {"a": 1.0, "b": 0.0, "c": 0.0},
            "minimum_success_confidence": request.item.minimum_success_confidence,
            # A placeholder payload. `BankItem` requires one to match the modality, and
            # propagation reads none of it — the graph is told which nodes a response
            # touched and how strongly, never what the question was.
            request.item.modality: {},
        }
    )
    graded = GradedResponse(
        item_id=request.item.item_id,
        modality=request.item.modality,
        outcomes=[
            {
                "variable": o.variable,
                "score": o.score,
                "weight": o.weight,
                "confidence": o.confidence,
                "source_item_id": o.source_item_id or request.item.item_id,
                "modality": o.modality or request.item.modality,
            }
            for o in request.outcomes
        ],
    )

    try:
        result = propagate(
            graph=graph,
            state=state,
            item=item,
            graded=graded,
            attempt_no=request.attempt_no,
            minimum_failures_to_block=minimum_failures,
            session_variables=set(request.session_variables),
        )
    except ValueError as exc:
        # Two outcomes naming one variable. The grader is expected to aggregate per
        # competency, and dropping the second silently would make a grader bug look like a
        # propagation one.
        raise ServiceError(422, "outcomes_invalid", str(exc)) from exc

    manifest, manifest_hash = build_manifest(
        bank_id=request.bank_id,
        minimum_failures_to_block=minimum_failures,
        resolved=None,
    )
    return PropagateResponse(
        state_update=result.state_update,
        selection_affected_mains=sorted(result.selection_affected_mains),
        inferred_signals=_signals(result.state_update),
        manifest=manifest,
        manifest_hash=manifest_hash,
        applied=result.applied,
    )


def _signals(state_update: dict) -> list[InferredSignalDTO]:
    """The inference provenance, for reporting.

    Read back out of the state update rather than passed alongside it, so there is exactly
    one description of what was inferred. `strength` is for ranking and for a report; it is
    NOT a weight, and this type has no field it could be mistaken for.
    """
    records = state_update.get("graph_inferred_mastery_records", {}) or {}
    return [
        InferredSignalDTO(
            node=node,
            source_node=str(record.get("source_node", "")),
            distance=int(record.get("distance", 0)),
            strength=float(record.get("strength", 0.0)),
            source_evidence_id=str(record.get("evidence_id", "")),
            modality=record.get("modality") or "mcq",
        )
        for node, record in sorted(records.items())
    ]


@app.get(
    "/manifest/{bank_id}",
    response_model=ManifestResponse,
    tags=["propagation"],
    responses=RESPONSES,
    summary="The propagation configuration in force, and its hash",
    description=(
        "Stamped into a session at `begin`, so a result can name the configuration that "
        "produced it. A number that cannot say what configuration produced it cannot be "
        "reproduced or believed. Reported by whoever OWNS propagation rather than "
        "recomputed by the caller: two answers to that question is the failure this exists "
        "to detect."
    ),
)
def manifest(bank_id: str) -> ManifestResponse:
    minimum_failures = _graphs.minimum_failures_to_block(bank_id)
    payload, digest = build_manifest(
        bank_id=bank_id, minimum_failures_to_block=minimum_failures, resolved=None
    )
    return ManifestResponse(bank_id=bank_id, manifest=payload, manifest_hash=digest)


@app.post(
    "/coverage",
    response_model=CoverageResponse,
    tags=["coverage"],
    responses=RESPONSES,
    summary="Which required sub-competencies of a main still lack direct evidence",
    description=(
        "The gate that stops a main claiming convergence on an estimate built from a "
        "corner of itself. Only DIRECT evidence counts — inferred mastery does not, "
        "because a deduction standing in for a measurement is exactly what this gate "
        "exists to prevent. Partial credit does count: the question is whether the node "
        "was measured, not whether it was passed."
    ),
)
def coverage(request: CoverageRequest) -> CoverageResponse:
    graph = _graph_for(request.bank_id)
    required = sub_nodes_for_main(
        graph, request.main, critical_only=request.critical_only
    )
    unmeasured = sorted(required - set(request.directly_measured))
    return CoverageResponse(
        main=request.main,
        required=sorted(required),
        unmeasured=unmeasured,
        satisfied=not unmeasured,
    )
