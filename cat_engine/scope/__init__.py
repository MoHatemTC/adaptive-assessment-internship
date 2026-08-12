"""A selection of competencies becomes a sub-graph and an item allowlist.

WHAT THIS IS FOR

An assessment used to be a whole bank. `begin()` accepts `target_variables`, but the engine
checks those against `bank.variables()`, which returns MAIN competencies only — so
`["C1", "C3"]` worked and `["C1.1", "C1.4"]` was refused as "no bank coverage". Everything
here exists to make the second expressible, and to make it expressible without the engine
having to learn a new concept.

IT RETURNS ITEM IDS, NEVER ITEMS

A scope names what may be administered; `catalogue` is the only thing that serves items,
through two functions with deliberately different return types. A scope that returned
questions would be a second item-serving path with a third answer to "what is in this bank".

IT CANNOT MOVE A POSTERIOR

There is no score, no weight and no theta in a manifest. A scope changes which questions may
be asked and what coverage requires. The worst a buggy scope can do is narrow a pool — a
quality-of-measurement problem, stated in the report, rather than a wrong number nobody can
see.

THE MANIFEST IS A PURE FUNCTION, WHICH IS WHAT MAKES THE IDS TRUSTWORTHY

It depends only on the bank version and the normalised selection, so `scope_id` is a hash of
its own inputs. A host previews a scope, `begin()` rebuilds it at session start, and the two
agree by construction rather than through a shared cache. Nothing is stored.
"""

from __future__ import annotations

import logging

from cat_engine.catalogue import graph as graph_dto_for
from cat_engine.catalogue import item_refs, summary
from cat_engine.contracts import ScopeManifest, ScopeRequest
from cat_engine.engine.config.settings import settings as engine_settings
from cat_engine.errors import BankUnknown
from cat_engine.scope.bank import ScopedBank, induced_graph, scoped_graph_service
from cat_engine.scope.render import render_mermaid
from cat_engine.scope.scoping import build_scope

logger = logging.getLogger(__name__)

__all__ = [
    "ScopedBank",
    "build_manifest",
    "induced_graph",
    "render_mermaid",
    "scoped_graph_service",
]


def build_manifest(request: ScopeRequest) -> ScopeManifest:
    """Induce the scope of a selection over what the bank declares.

    A bank with no graph is refused rather than silently degraded to "the whole bank":
    without sub-competency nodes there is nothing to scope to, and widening the assessment
    is not what the caller asked for and not something the report could show them.
    """
    bank = summary(request.bank_id)
    graph = graph_dto_for(request.bank_id)
    if graph is None:
        raise BankUnknown(
            f"bank {request.bank_id} declares no competency graph, so it has no "
            "sub-competencies to scope to",
            code="graph_absent",
            status_code=404,
        )

    version, items = item_refs(request.bank_id)
    critical_only = (
        bank.coverage_critical_only
        if request.critical_only is None
        else request.critical_only
    )
    manifest = build_scope(
        request=request,
        bank_version=version or bank.version,
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
