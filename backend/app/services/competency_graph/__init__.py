"""Competency dependency graph layer.

This package is designed to be a deterministic add-on to the existing CAT engine.
In Phase A it provides:
- graph JSON loading + validation (startup fail when enabled)
- basic graph traversal helpers (ancestors/descendants)
- node runtime state models (used later by shadow/live phases)
"""

from pathlib import Path

from app.config.paths import DATA_DIR

from .graph import CompetencyGraphService
from .models import CompetencyGraph
from .validator import load_and_validate_graph

DEFAULT_GRAPH_PATH = DATA_DIR / "competency_graph.json"


def load_competency_graph(path: str | Path) -> CompetencyGraph:
    """Load and validate the graph at `path`.

    Caching belongs to `services.orchestrator.registry`, which knows which graph pairs
    with which bank; this function always reads from disk.
    """
    return load_and_validate_graph(path)


def load_default_competency_graph() -> CompetencyGraph:
    """The Data Analysis graph, by path.

    Retained for callers that predate the registry. Anything that also touches a bank
    should use `registry.get_graph_service(bank_id)` instead — a graph loaded without
    reference to a bank is how the coverage gate ends up evaluating the wrong nodes.
    """
    return load_and_validate_graph(DEFAULT_GRAPH_PATH)


__all__ = [
    "DEFAULT_GRAPH_PATH",
    "CompetencyGraph",
    "CompetencyGraphService",
    "load_and_validate_graph",
    "load_competency_graph",
    "load_default_competency_graph",
]
