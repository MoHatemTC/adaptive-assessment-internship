from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class CompetencyStatus(StrEnum):
    UNKNOWN = "unknown"
    DIRECT_MASTERED = "direct_mastered"
    INFERRED_MASTERED = "inferred_mastered"
    DIRECT_NOT_MASTERED = "direct_not_mastered"
    INFERRED_NOT_MASTERED = "inferred_not_mastered"
    BLOCKED = "blocked"
    NEEDS_VERIFICATION = "needs_verification"
    CONTRADICTED = "contradicted"


NodeType = Literal["main", "sub_competency"]
RelationType = Literal[
    "PREREQUISITE",
    "CONTRIBUTES_TO",
    "EQUIVALENT_TO",
    "CONTEXT_VARIANT_OF",
    "RELATED_TO",
    "SUPPORTS",
]


@dataclass(frozen=True)
class CompetencyNode:
    competency_id: str
    title: str
    node_type: NodeType

    critical: bool = False
    context_specific: bool = False
    main_competencies: tuple[str, ...] = ()
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CompetencyEdge:
    from_id: str
    to_id: str
    relation: RelationType

    # Meaning depends on relation:
    # - PREREQUISITE: strength/confidence of the dependency edge.
    # - CONTRIBUTES_TO: weight/loading contribution (used later).
    strength: float = 1.0
    weight: float = 1.0

    # Only applies to PREREQUISITE edges (policy controls).
    allow_upward_inference: bool = True
    allow_downward_blocking: bool = True
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CompetencyGraph:
    schema_version: str
    nodes: dict[str, CompetencyNode]
    edges: tuple[CompetencyEdge, ...]
    # Precomputed edge lists; still safe to hold on the immutable graph.
    prerequisite_edges: tuple[CompetencyEdge, ...] = ()

