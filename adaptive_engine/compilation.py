"""Pure assessment preparation: validate everything once, derive the indexes selection needs.

``compile_assessment`` is the single validation path — the same function the tests use,
with no second validator anywhere. It rejects a broken definition with a clear
``InvalidDefinition`` and returns a ``CompiledAssessment`` that holds the original
definition once plus derived indexes; nothing is duplicated.

The engine keeps no cache. A caller that compiles the same definition on every request is
doing cheap, pure work; a caller that wants to avoid even that may cache the compiled
object by (assessment_id, version) — that cache is the caller's, with the caller's
invalidation rules.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from adaptive_engine.errors import InvalidDefinition
from adaptive_engine.models import AssessmentDefinition, Question


@dataclass(frozen=True)
class CompiledAssessment:
    """An assessment validated and indexed for execution. In-memory, immutable.

    Holds the definition once; every other field is a derived index that selection or
    reporting genuinely reads. The mappings are read-only views so a compiled assessment
    can be shared between concurrent runs safely.
    """

    definition: AssessmentDefinition
    questions_by_id: Mapping[str, Question] = field(repr=False)
    #: Question ids that measure each competency, in definition order.
    question_ids_by_competency: Mapping[str, tuple[str, ...]] = field(repr=False)
    #: Transitive prerequisite closure per competency, used for graph-aware selection.
    ancestors_by_competency: Mapping[str, frozenset[str]] = field(repr=False)

    @property
    def assessment_id(self) -> str:
        return self.definition.assessment_id

    @property
    def version(self) -> str:
        return self.definition.version

    @property
    def competency_ids(self) -> tuple[str, ...]:
        return tuple(c.competency_id for c in self.definition.competencies)


def _prerequisite_closure(
    competency_ids: tuple[str, ...], parents: dict[str, set[str]]
) -> dict[str, frozenset[str]]:
    """Transitive ancestors per node. Iterative BFS — no recursion-depth cliff."""
    closure: dict[str, frozenset[str]] = {}
    for competency_id in competency_ids:
        seen: set[str] = set()
        frontier = deque(parents.get(competency_id, ()))
        while frontier:
            node = frontier.popleft()
            if node in seen:
                continue
            seen.add(node)
            frontier.extend(parents.get(node, ()))
        closure[competency_id] = frozenset(seen)
    return closure


def _require_acyclic(competency_ids: tuple[str, ...], parents: dict[str, set[str]]) -> None:
    """Kahn's algorithm over prerequisite edges; leftover nodes are a cycle."""
    remaining_parents = {cid: set(parents.get(cid, ())) for cid in competency_ids}
    children: dict[str, set[str]] = {cid: set() for cid in competency_ids}
    for child, parent_set in remaining_parents.items():
        for parent in parent_set:
            children[parent].add(child)

    ready = deque(cid for cid, ps in remaining_parents.items() if not ps)
    resolved = 0
    while ready:
        node = ready.popleft()
        resolved += 1
        for child in children[node]:
            remaining_parents[child].discard(node)
            if not remaining_parents[child]:
                ready.append(child)
    if resolved != len(competency_ids):
        cyclic = sorted(cid for cid, ps in remaining_parents.items() if ps)
        raise InvalidDefinition(
            f"the competency graph contains a prerequisite cycle involving {cyclic}"
        )


def compile_assessment(definition: AssessmentDefinition) -> CompiledAssessment:
    """Validate the definition end to end and build the indexes selection needs.

    Raises ``InvalidDefinition`` for: duplicate competency or question ids, a question
    measuring an unknown competency, a graph edge touching an unknown competency, a
    self-loop, a duplicate edge, a prerequisite cycle, or a competency no question
    measures (which could never be assessed and would silently block completion).

    Field-level bounds (IRT parameter ranges, weights, answer_index range) are enforced
    by the definition models themselves at construction.
    """
    if not isinstance(definition, AssessmentDefinition):
        definition = AssessmentDefinition.model_validate(definition)

    competency_ids = tuple(c.competency_id for c in definition.competencies)
    duplicates = _duplicated(competency_ids)
    if duplicates:
        raise InvalidDefinition(f"duplicate competency ids: {duplicates}")
    known = set(competency_ids)

    question_ids = tuple(q.question_id for q in definition.questions)
    duplicates = _duplicated(question_ids)
    if duplicates:
        raise InvalidDefinition(f"duplicate question ids: {duplicates}")

    questions_by_id: dict[str, Question] = {}
    by_competency: dict[str, list[str]] = {cid: [] for cid in competency_ids}
    for question in definition.questions:
        questions_by_id[question.question_id] = question
        for measurement in question.measures:
            if measurement.competency_id not in known:
                raise InvalidDefinition(
                    f"question {question.question_id} measures unknown competency "
                    f"{measurement.competency_id!r}"
                )
            by_competency[measurement.competency_id].append(question.question_id)

    unmeasured = sorted(cid for cid, qids in by_competency.items() if not qids)
    if unmeasured:
        raise InvalidDefinition(
            f"no question measures competencies {unmeasured}; they could never be assessed"
        )

    parents: dict[str, set[str]] = {}
    seen_edges: set[tuple[str, str]] = set()
    for edge in definition.graph.edges:
        for endpoint in (edge.source, edge.target):
            if endpoint not in known:
                raise InvalidDefinition(
                    f"graph edge {edge.source!r} -> {edge.target!r} references unknown "
                    f"competency {endpoint!r}"
                )
        if edge.source == edge.target:
            raise InvalidDefinition(f"graph edge {edge.source!r} -> itself is a self-loop")
        pair = (edge.source, edge.target)
        if pair in seen_edges:
            raise InvalidDefinition(f"duplicate graph edge {edge.source!r} -> {edge.target!r}")
        seen_edges.add(pair)
        parents.setdefault(edge.target, set()).add(edge.source)

    _require_acyclic(competency_ids, parents)

    return CompiledAssessment(
        definition=definition,
        questions_by_id=MappingProxyType(questions_by_id),
        question_ids_by_competency=MappingProxyType(
            {cid: tuple(qids) for cid, qids in by_competency.items()}
        ),
        ancestors_by_competency=MappingProxyType(
            _prerequisite_closure(competency_ids, parents)
        ),
    )


def _duplicated(ids: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for identifier in ids:
        if identifier in seen:
            duplicates.add(identifier)
        seen.add(identifier)
    return sorted(duplicates)
