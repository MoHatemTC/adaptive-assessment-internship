"""Adaptive competency assessment, as one embeddable module.

THE STATEFUL RUNTIME IN THIS PACKAGE IS DEPRECATED. The assessment runtime — begin,
answer, report, sessions, session stores — has been replaced by the stateless
``adaptive_engine`` package at the repository root:

    from adaptive_engine import compile_assessment, start_assessment, advance_assessment

The host persists the returned adaptive state; the engine holds nothing between calls.
`AssessmentModule.begin()`/`answer()` remain only until the authoring branch (banks,
graphs, scoping, ingest) is revamped in its turn, and warn when used. Do not build new
runtime callers on them.

    from cat_engine import AssessmentModule, CatConfig

    cat = AssessmentModule(CatConfig(active_bank="AIE"))
    state = await cat.begin()
    state = await cat.answer(state.session_id, mcq=2)

There is no web framework here and no port. The host owns transport; this owns the
measurement. See `docs/module.md` for the full contract.

WHAT LIVES WHERE

    engine/       the psychometrics — 3PL core, posterior, selection, stopping, graph
    facade.py     AssessmentModule: the surface a host calls
    wiring.py     builds an Orchestrator out of in-process adapters
    contracts/    the DTOs a host receives; deliberately independent of `engine`
    stores/       sessions and banks, pluggable
    scope/        a competency selection to a sub-graph and an item allowlist
    ingest/       one uploaded file to a registered bank
    live/         realtime interview rooms, and the browser client for them

The public names are re-exported lazily so that importing this package does not drag in
numpy, a sandbox client and 600 KB of question banks before a host has decided it wants
them. `from cat_engine import AssessmentModule` still works exactly as written.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "2.0.0"

if TYPE_CHECKING:  # pragma: no cover - import-time typing only
    from .config import CatConfig
    from .errors import (
        AnswerInvalid,
        AnswerTypeMismatch,
        AssessmentUnknown,
        CapacityReached,
        CatError,
        GraderUnavailable,
        ScopeUnassessable,
        StaleAnswer,
    )
    from .facade import AssessmentModule, SyncAssessmentModule

#: public name -> the submodule that defines it.
_EXPORTS = {
    "AssessmentModule": ".facade",
    "SyncAssessmentModule": ".facade",
    "CatConfig": ".config",
    "CatError": ".errors",
    "AssessmentUnknown": ".errors",
    "StaleAnswer": ".errors",
    "AnswerTypeMismatch": ".errors",
    "AnswerInvalid": ".errors",
    "ScopeUnassessable": ".errors",
    "CapacityReached": ".errors",
    "GraderUnavailable": ".errors",
}

#: Spelled out rather than derived from `_EXPORTS`. A starred `__all__` is invisible to
#: every tool that reads a package's surface statically — linters, type checkers, and the
#: documentation generators a host might point at this — and a public surface nothing can
#: read without executing the module is not much of a surface. `test_the_lazy_exports_work`
#: asserts the two lists agree, so the duplication cannot drift.
__all__ = [
    "AnswerInvalid",
    "AnswerTypeMismatch",
    "AssessmentModule",
    "AssessmentUnknown",
    "CapacityReached",
    "CatConfig",
    "CatError",
    "GraderUnavailable",
    "ScopeUnassessable",
    "StaleAnswer",
    "SyncAssessmentModule",
    "__version__",
]


def __getattr__(name: str) -> Any:
    """PEP 562 lazy re-export. Anything not listed is a real AttributeError."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(module, __name__), name)


def __dir__() -> list[str]:
    return sorted(__all__)
