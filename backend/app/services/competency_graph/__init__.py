"""Competency dependency graph layer.

This package is designed to be a deterministic add-on to the existing CAT engine.
In Phase A it provides:
- graph JSON loading + validation (startup fail when enabled)
- basic graph traversal helpers (ancestors/descendants)
- node runtime state models (used later by shadow/live phases)
"""

from pathlib import Path

from .graph import CompetencyGraphService
from .models import CompetencyGraph
from .validator import load_and_validate_graph

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # backend/app
DEFAULT_GRAPH_PATH = _PACKAGE_ROOT / "data" / "competency_graph.json"


def load_default_competency_graph() -> CompetencyGraph:
    return load_and_validate_graph(DEFAULT_GRAPH_PATH)


__all__ = ["CompetencyGraph", "CompetencyGraphService", "load_and_validate_graph", "load_default_competency_graph", "DEFAULT_GRAPH_PATH"]

