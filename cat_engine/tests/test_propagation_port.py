"""The propagation port: applying one graded response to the competency graph.

WHAT SURVIVED FROM `test_competency_graph_service.py`

That file drove `InProcessPropagation` through an HTTP service and asserted on the JSON.
The service is gone; the port is not, and it is still the seam the orchestrator reaches
propagation through. These are the same assertions against the object directly.

`TestTheServiceAndTheInProcessPathAgree` did NOT survive, and its absence is the point of
the whole change: it existed to prove that propagating over a wire produced the same node
state as propagating in this process. There is one path now, and
`test_parity_facade_vs_engine.py::test_the_graph_reached_the_same_conclusions` is what
carries the guarantee forward — node status, mastery, the evidence ledger and the
unvalidated-edge forecast, compared between the facade and a raw orchestrator.

THE INVARIANT THIS EXISTS TO PROTECT

**Only a directly observed response may move a posterior.** Propagation can change what is
ASKED and when the test may STOP. It never changes what is ESTIMATED. The structural half
of that is `test_contracts.py::TestInferredSignalCannotCarryEvidence` — the DTO has no
`score` and no `weight` to put one in. The behavioural half is here.
"""

from __future__ import annotations

import pytest

BANK = "DA"


def nodes() -> list[str]:
    from cat_engine.engine.services.orchestrator import registry

    graph = registry.get_graph_service(BANK)
    return sorted(
        nid for nid, node in graph.graph.nodes.items() if node.node_type != "main"
    )


@pytest.fixture()
def port():
    """The port the orchestrator reaches propagation through, built as `wiring` builds it."""
    from cat_engine.engine.services.orchestrator import registry
    from cat_engine.engine.services.orchestrator.propagation_port import (
        InProcessPropagation,
    )

    return InProcessPropagation(
        lambda: registry.get_graph_service(BANK),
        bank_id=BANK,
        minimum_failures_provider=lambda: None,
    )


def a_response(node: str, *, score: float = 1.0):
    """One graded response naming one node, and the state it is applied to.

    The state comes from `Orchestrator.begin` rather than being constructed here.
    `AssessmentState.variables` is a dict of `VariableState` with a seeded posterior in
    each, and hand-building one would be a second, simpler definition of the object under
    test — the kind that passes while production's does not.
    """
    from cat_engine.engine.schemas.orchestration import BankItem, GradedResponse
    from cat_engine.tests.conftest import orchestrator_for

    item = BankItem.model_validate(
        {
            "item_id": "q1",
            "modality": "mcq",
            "measures": [{"variable": node, "weight": 1.0}],
            "cat": {"a": 1.0, "b": 0.0, "c": 0.25},
            "mcq": {"stem": "?", "options": ["a", "b"], "answer_index": 1},
        }
    )
    graded = GradedResponse(
        item_id="q1",
        modality="mcq",
        outcomes=[
            {
                "variable": node,
                "score": score,
                "weight": 1.0,
                "confidence": 1.0,
                "source_item_id": "q1",
                "modality": "mcq",
            }
        ],
    )
    state = orchestrator_for(BANK).begin([BANK])
    return state, item, graded


class TestPropagation:
    def test_a_direct_success_marks_the_node_measured_and_mastered(self, port):
        node = nodes()[0]
        result = port.apply(*a_response(node))
        assert result.applied is True
        assert node in result.state_update["graph_direct_measured_nodes"]
        assert node in result.state_update["graph_direct_mastered_nodes"]

    def test_a_direct_failure_is_measured_but_not_mastered(self, port):
        """Coverage asks whether a node was MEASURED. Mastery is a separate question, and
        conflating them would let a competency go uncovered because the candidate got it
        wrong."""
        node = nodes()[0]
        update = port.apply(*a_response(node, score=0.0)).state_update
        assert node in update["graph_direct_measured_nodes"]
        assert node in update["graph_direct_not_mastered_nodes"]
        assert node not in update["graph_direct_mastered_nodes"]

    def test_replaying_the_same_response_changes_nothing(self, port):
        """Evidence ids are deterministic, which is what makes a retry safe. Without it a
        retry would count one answer twice, and the damage would land on the standard error
        — the thing the assessment stops on."""
        node = nodes()[0]
        state, item, graded = a_response(node)
        first = port.apply(state, item, graded)

        replayed = state.model_copy(update=first.state_update)
        second = port.apply(replayed, item, graded)

        assert second.state_update["graph_node_states"] == (
            first.state_update["graph_node_states"]
        )
        assert second.state_update["graph_processed_evidence_ids"] == (
            first.state_update["graph_processed_evidence_ids"]
        )


class TestNothingComingBackCanMoveAPosterior:
    """The invariant the whole design rests on, checked on what propagation returns."""

    def test_the_result_carries_no_score_and_no_weight(self, port):
        result = port.apply(*a_response(nodes()[0]))
        for forbidden in ("score", "weight", "theta", "standard_error"):
            assert forbidden not in result.state_update, forbidden

    def test_what_it_may_change_is_selection_and_coverage_only(self, port):
        """`selection_affected_mains` is the whole of propagation's reach into the loop: it
        says which competencies should be re-ranked. There is no channel from here to an
        estimate."""
        result = port.apply(*a_response(nodes()[0]))
        assert isinstance(result.selection_affected_mains, set)
        assert all(isinstance(m, str) for m in result.selection_affected_mains)

    def test_propagation_ships_inert_so_nothing_is_inferred_at_all(self, port):
        """A completed screening study measured the wrong-inference rate at 7-17% against a
        3% gate. Turning either direction on is a per-edge decision with an experimental
        design behind it — so the shipped default infers nothing, and this is what says so.
        """
        update = port.apply(*a_response(nodes()[0])).state_update
        assert not update.get("graph_inferred_nodes")


class TestTheManifest:
    def test_it_names_the_configuration_in_force(self, port):
        manifest, fingerprint = port.manifest()
        assert fingerprint
        assert isinstance(manifest, dict)

    def test_it_is_stable_across_calls(self, port):
        """It is read to decide whether two components agree about the graph. One that
        changed between calls could not be compared."""
        assert port.manifest() == port.manifest()
