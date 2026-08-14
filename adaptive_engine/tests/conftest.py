"""Shared builders for the adaptive_engine suite.

Definitions are built in the engine's own wire shapes (BankItem dicts, graph JSON), the
same way a host or the authoring branch would supply them. Measurement policy is tuned
per test by patching the engine settings object directly — the same technique the legacy
suite uses — because the config layer deliberately refuses two different applied
policies in one process.
"""

from __future__ import annotations

import pytest

from adaptive_engine import AssessmentDefinition


def mcq_item(
    item_id: str,
    variable: str,
    *,
    b: float = 0.0,
    a: float = 2.0,
    c: float = 0.0,
    answer_index: int = 0,
    weight: float = 1.0,
) -> dict:
    return {
        "item_id": item_id,
        "modality": "mcq",
        "measures": [{"variable": variable, "weight": weight}],
        "cat": {"a": a, "b": b, "c": c},
        "mcq": {
            "stem": f"Question {item_id}?",
            "options": ["option a", "option b", "option c"],
            "answer_index": answer_index,
        },
    }


def code_item(item_id: str, measures: list[tuple[str, float]], *, b: float = 0.5) -> dict:
    return {
        "item_id": item_id,
        "modality": "code",
        "measures": [{"variable": v, "weight": w} for v, w in measures],
        "cat": {"a": 1.8, "b": b, "c": 0.0},
        "code": {"prompt": f"Implement {item_id}.", "language": "python"},
    }


def bank_for(variable: str, count: int = 12) -> list[dict]:
    """A spread of difficulties wide enough for the run to localise any ability."""
    return [
        mcq_item(
            f"{variable}-q{i}",
            variable,
            b=min(4.0, (i % 9) - 4.0 + (i // 9) * 0.1),
        )
        for i in range(count)
    ]


def two_main_graph() -> dict:
    return {
        "version": "1.0",
        "nodes": [
            {"competency_id": "databases", "title": "Databases", "node_type": "main"},
            {"competency_id": "python", "title": "Python", "node_type": "main"},
        ],
        "edges": [
            {"from": "python", "to": "databases", "relation": "PREREQUISITE", "strength": 0.9}
        ],
    }


def definition_with(
    *,
    items: list[dict] | None = None,
    graph: dict | None = None,
    targets: list[str] | None = None,
    version: str = "v1",
) -> AssessmentDefinition:
    return AssessmentDefinition(
        assessment_id="python-backend",
        version=version,
        items=items
        if items is not None
        else [*bank_for("python"), *bank_for("databases")],
        graph=graph,
        targets=targets,
    )


@pytest.fixture()
def definition() -> AssessmentDefinition:
    return definition_with()


@pytest.fixture()
def fast_convergence(monkeypatch):
    """Loosen the observation floors so short deterministic runs can converge."""
    from cat_engine.engine.config.settings import settings

    monkeypatch.setattr(settings, "cat_min_questions", 3)
    monkeypatch.setattr(settings, "cat_precision_min_questions", 3)
    return settings
