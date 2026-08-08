"""Contradiction detection, reopening, and the idempotent ledger.

The review's C2 finding: contradiction recovery as specified could never fire. The
scenario needs a BLOCKED node to receive direct evidence, and blocked nodes were excluded
from eligibility outright — so the mechanism named as the mitigation for a wrong
prerequisite edge could only trigger by accident.

Three things had to change together for it to be reachable: blocking had to work at all,
blocked items had to be penalised rather than excluded, and detection had to compare new
direct evidence against existing state instead of intersecting two sets.
"""

from __future__ import annotations

import pytest

from app.services.competency_graph import load_default_competency_graph
from app.services.competency_graph.config import PropagationConfig
from app.services.competency_graph.evidence import EvidenceEvent, evidence_id_for
from app.services.competency_graph.graph import CompetencyGraphService
from app.services.competency_graph.ledger import DuplicateEvidenceError, EvidenceLedger
from app.services.competency_graph.models import CompetencyStatus
from app.services.competency_graph.propagation import (
    apply_direct_evidence,
    apply_events,
)
from app.services.competency_graph.state import CompetencyGraphState


@pytest.fixture
def graph() -> CompetencyGraphService:
    return CompetencyGraphService(load_default_competency_graph())


def _event(
    target: str, *, score: float, modality: str = "code", suffix: str = ""
) -> EvidenceEvent:
    return EvidenceEvent(
        evidence_id=f"s:item{suffix}:1:{modality}:{target}:-",
        session_id="s",
        item_id=f"item{suffix}",
        modality=modality,
        target_node=target,
        score=score,
        weight=1.0,
        confidence=1.0,
        source="item",
    )


class TestReopening:
    def test_direct_success_reopens_a_blocked_node(self, graph) -> None:
        """Spec 48.2: new direct evidence can reopen the node."""
        results, state, ledger = apply_events(
            graph, [_event("DA.1", score=0.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )
        assert "DA.4" in results[0].blocked_nodes
        assert state.nodes["DA.4"].status is CompetencyStatus.BLOCKED

        [reopened] = [
            apply_direct_evidence(
                graph,
                state,
                _event("DA.4", score=1.0, suffix="2"),
                ledger=ledger,
                config=PropagationConfig(minimum_failures_to_block=1),
            )
        ]

        assert reopened.reopened_nodes == frozenset({"DA.4"})
        assert state.nodes["DA.4"].status is CompetencyStatus.DIRECT_MASTERED
        assert not state.nodes["DA.4"].blocked_by

    def test_the_contradiction_names_the_prerequisite_not_the_node_that_passed(
        self, graph
    ) -> None:
        """DA.4 passing does not put DA.4 in doubt — it puts DA.1's failure in doubt."""
        _results, state, ledger = apply_events(
            graph, [_event("DA.1", score=0.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )
        result = apply_direct_evidence(
            graph,
            state,
            _event("DA.4", score=1.0, suffix="2"),
            ledger=ledger,
            config=PropagationConfig(minimum_failures_to_block=1),
        )

        assert "DA.1" in result.contradicted_nodes
        assert "DA.4" not in result.contradicted_nodes
        kinds = {c["kind"] for c in state.nodes["DA.1"].contradictions}
        assert kinds == {"blocked_but_passed"}

    def test_a_node_already_demonstrated_is_not_blocked_by_a_later_failure(
        self, graph
    ) -> None:
        """Evidence against the edge, not a reason to hide a node already passed."""
        _results, state, ledger = apply_events(
            graph, [_event("DA.4", score=1.0, modality="mcq")], config=PropagationConfig(minimum_failures_to_block=1)
        )
        result = apply_direct_evidence(
            graph,
            state,
            _event("DA.1", score=0.0, suffix="2"),
            ledger=ledger,
            config=PropagationConfig(minimum_failures_to_block=1),
        )

        assert "DA.4" not in result.blocked_nodes
        assert state.nodes["DA.4"].status is CompetencyStatus.DIRECT_MASTERED


class TestDirectReversal:
    def test_the_same_node_graded_both_ways_needs_verification(self, graph) -> None:
        _results, state, ledger = apply_events(
            graph, [_event("DA.5", score=0.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )
        result = apply_direct_evidence(
            graph,
            state,
            _event("DA.5", score=1.0, suffix="2"),
            ledger=ledger,
            config=PropagationConfig(minimum_failures_to_block=1),
        )

        assert "DA.5" in result.contradicted_nodes
        assert state.nodes["DA.5"].status is CompetencyStatus.NEEDS_VERIFICATION
        assert {c["kind"] for c in state.nodes["DA.5"].contradictions} == {"direct_reversal"}


class TestWrongInference:
    def test_failing_an_inferred_node_names_the_inference_that_was_wrong(self, graph) -> None:
        """The only in-band source of ground truth for the wrong-inference rate."""
        _results, state, ledger = apply_events(
            graph, [_event("DA.6", score=1.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )
        assert state.nodes["DA.3"].status is CompetencyStatus.INFERRED_MASTERED

        result = apply_direct_evidence(
            graph,
            state,
            _event("DA.3", score=0.0, suffix="2"),
            ledger=ledger,
            config=PropagationConfig(minimum_failures_to_block=1),
        )

        assert "DA.3" in result.contradicted_nodes
        contradiction = state.nodes["DA.3"].contradictions[-1]
        assert contradiction["kind"] == "wrong_inference"
        assert "DA.6" in contradiction["against"]


class TestInferenceNeverOverridesDirect:
    def test_an_inference_does_not_overwrite_a_direct_verdict(self, graph) -> None:
        _results, state, ledger = apply_events(
            graph, [_event("DA.3", score=0.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )
        assert state.nodes["DA.3"].status is CompetencyStatus.DIRECT_NOT_MASTERED

        apply_direct_evidence(
            graph,
            state,
            _event("DA.6", score=1.0, suffix="2"),
            ledger=ledger,
            config=PropagationConfig(minimum_failures_to_block=1),
        )

        assert state.nodes["DA.3"].status is CompetencyStatus.DIRECT_NOT_MASTERED


class TestLedger:
    def test_the_same_event_twice_is_refused(self, graph) -> None:
        state = CompetencyGraphState()
        ledger = EvidenceLedger()
        event = _event("DA.1", score=1.0)

        apply_direct_evidence(graph, state, event, ledger=ledger, config=PropagationConfig(minimum_failures_to_block=1))
        with pytest.raises(DuplicateEvidenceError):
            apply_direct_evidence(
                graph, state, event, ledger=ledger, config=PropagationConfig(minimum_failures_to_block=1)
            )
        assert state.nodes["DA.1"].direct_observations == 1

    def test_it_survives_a_round_trip_through_persisted_state(self, graph) -> None:
        """A resumed session must not re-apply evidence it already applied."""
        _results, state, ledger = apply_events(
            graph, [_event("DA.1", score=1.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )

        restored_state = CompetencyGraphState.from_dict(state.to_dict())
        restored_ledger = EvidenceLedger.from_ids(ledger.processed_ids())

        with pytest.raises(DuplicateEvidenceError):
            apply_direct_evidence(
                graph,
                restored_state,
                _event("DA.1", score=1.0),
                ledger=restored_ledger,
                config=PropagationConfig(minimum_failures_to_block=1),
            )
        assert restored_state.nodes["DA.1"].direct_observations == 1

    def test_two_criteria_on_one_item_get_distinct_ids(self) -> None:
        """Without the criterion segment they collide and the second is dropped."""
        first = evidence_id_for(
            session_id="s",
            item_id="i",
            attempt_no=1,
            modality="voice",
            target_node="DA.1",
            rubric_criterion_id="technical_accuracy",
        )
        second = evidence_id_for(
            session_id="s",
            item_id="i",
            attempt_no=1,
            modality="voice",
            target_node="DA.1",
            rubric_criterion_id="completeness",
        )
        assert first != second

    def test_a_re_administration_is_a_different_attempt(self) -> None:
        kwargs = dict(session_id="s", item_id="i", modality="mcq", target_node="DA.1")
        assert evidence_id_for(attempt_no=1, **kwargs) != evidence_id_for(
            attempt_no=2, **kwargs
        )


class TestUnscorable:
    def test_an_unscorable_response_changes_nothing_and_affects_no_main(self, graph) -> None:
        """An audio failure is not a wrong answer.

        Reporting a changed node for one invalidates queue slots and triggers a rollup for
        a main where no score moved.
        """
        state = CompetencyGraphState()
        event = EvidenceEvent(
            evidence_id="s:i:1:voice:DA.1:-",
            session_id="s",
            item_id="i",
            modality="voice",
            target_node="DA.1",
            score=0.0,
            weight=0.0,
            confidence=0.0,
            source="i",
        )

        result = apply_direct_evidence(
            graph, state, event, ledger=EvidenceLedger(), config=PropagationConfig(minimum_failures_to_block=1)
        )

        assert result.unscorable is True
        assert result.changed_nodes == frozenset()
        assert result.affected_mains == frozenset()
        assert result.blocked_nodes == frozenset()
        assert state.nodes["DA.1"].direct_observations == 0


class TestChangedVersusStatusChanged:
    def test_blocking_does_not_trigger_a_rollup_but_does_invalidate_the_queue(
        self, graph
    ) -> None:
        """Review C17: a blocked descendant is a reason to re-pick, not to re-measure."""
        [result], _state, _ledger = apply_events(
            graph, [_event("DA.1", score=0.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )

        assert result.changed_nodes == frozenset({"DA.1"})
        assert result.blocked_nodes >= {"DA.3", "DA.4"}
        assert result.status_changed_nodes >= {"DA.3", "DA.4"}
        assert "DA.1" not in result.status_changed_nodes
