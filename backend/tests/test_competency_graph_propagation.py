from __future__ import annotations

from app.services.competency_graph import load_default_competency_graph
from app.services.competency_graph.evidence import EvidenceEvent
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.propagation import (
    PropagationConfig,
    shadow_apply_events,
)


def _event(*, target: str, modality: str, score: float = 1.0) -> EvidenceEvent:
    return EvidenceEvent(
        evidence_id=f"session:item:{modality}:{target}",
        session_id="session",
        item_id="item",
        modality=modality,
        target_node=target,
        score=score,
        weight=1.0,
        confidence=1.0,
        source="item",
    )


def test_single_mcq_hit_does_not_infer_prerequisite_mastery() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    [update] = shadow_apply_events(
        graph,
        [_event(target="DA.4", modality="mcq")],
        config=PropagationConfig(),
    )

    assert update.direct_updates[0].target_node == "DA.4"
    assert update.inferred_updates == ()


def test_code_success_can_infer_enabled_prerequisites() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    [update] = shadow_apply_events(
        graph,
        [_event(target="DA.6", modality="code")],
        config=PropagationConfig(),
    )

    # `.node`, not `.target_node`: an inferred signal is deliberately NOT an evidence
    # event. It carries no score and no weight, so it cannot become a graded outcome.
    inferred = {signal.node for signal in update.inferred_signals}
    assert inferred == {"DA.1", "DA.2", "DA.3", "DA.4"}
    assert all(signal.source_node == "DA.6" for signal in update.inferred_signals)
    assert not hasattr(update.inferred_signals[0], "score")
    assert not hasattr(update.inferred_signals[0], "weight")


def test_non_strong_score_does_not_mark_mastery_or_propagate() -> None:
    graph = CompetencyGraphService(load_default_competency_graph())
    [update] = shadow_apply_events(
        graph,
        [_event(target="DA.5", modality="open", score=0.5274)],
        config=PropagationConfig(),
    )

    assert update.inferred_updates == ()
    assert update.blocked_nodes == frozenset()
