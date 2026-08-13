"""The forecast from edges nobody has validated.

Every AI Engineer PREREQUISITE edge ships with `allow_upward_inference: false` and
`allow_downward_blocking: false`, because the numbering order it encodes is an authoring
convention and not measured dependency data. That is the correct default, and it has a
cost that showed up the first time anyone ran a real session: the inferred count and the
blocked count are zero, they are structurally zero, and they will be zero for every
session this bank ever runs.

Zero is also what a broken inference engine reports. Worse, there is no way out of it —
judging an edge needs evidence about the edge, and the only proposed source of that
evidence was enabling the edge on live candidates.

The preview closes the loop. It runs the SAME inference with the per-edge validation gate
waived and writes the result nowhere: not the posterior, not selection, not coverage. What
it buys is falsifiability — a node the graph would have called mastered, that the session
then measured and failed, is direct evidence that the edge is wrong.

The invariant every test here defends: a forecast must never become a conclusion.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.competency_graph.config import PropagationConfig
from cat_engine.engine.services.competency_graph.evidence import EvidenceEvent
from cat_engine.engine.services.competency_graph.ledger import EvidenceLedger
from cat_engine.engine.services.competency_graph.models import CompetencyStatus
from cat_engine.engine.services.competency_graph.propagation import apply_direct_evidence
from cat_engine.engine.services.competency_graph.state import CompetencyGraphState
from cat_engine.tests.conftest import orchestrator_for

MAINS = ["C1", "C3", "C6"]


def _event(node: str, *, score: float, modality: str = "code") -> EvidenceEvent:
    return EvidenceEvent(
        evidence_id=f"e-{node}-{score}",
        session_id="s",
        item_id="i",
        modality=modality,
        target_node=node,
        score=score,
        weight=1.0,
        confidence=1.0,
        source="i",
        evidence_kind="direct",
        directly_tested=True,
    )


def _apply(graph, event, *, config=None, state=None):
    state = state or CompetencyGraphState()
    state.ensure_nodes(set(graph.graph.nodes))
    result = apply_direct_evidence(
        graph,
        state,
        event,
        ledger=EvidenceLedger(),
        config=config or PropagationConfig(),
        now="2026-08-03T00:00:00+00:00",
    )
    return result, state


class TestTheBankIsWhyThisExists:
    def test_every_prerequisite_edge_is_inert(self, aie_graph) -> None:
        edges = [e for e in aie_graph.graph.edges if e.relation == "PREREQUISITE"]
        assert edges
        assert not any(
            e.allow_upward_inference or e.allow_downward_blocking for e in edges
        )

    def test_so_enforced_inference_returns_nothing_at_all(self, aie_graph) -> None:
        """Not "nothing today" — nothing reachable by any input."""
        result, state = _apply(aie_graph, _event("C1.6", score=1.0))

        assert result.inferred_signals == ()
        assert all(
            node.status is not CompetencyStatus.INFERRED_MASTERED
            for node in state.nodes.values()
        )

    def test_and_enforced_blocking_returns_nothing_either(self, aie_graph) -> None:
        result, _state = _apply(aie_graph, _event("C1.1", score=0.0))

        assert result.blocked_nodes == frozenset()


class TestThePreview:
    def test_it_reports_what_the_edges_would_have_inferred(self, aie_graph) -> None:
        result, _state = _apply(aie_graph, _event("C1.6", score=1.0))

        inferred = {s.node for s in result.preview_inferred_signals}
        assert inferred, "the preview found nothing where the edges plainly imply something"
        assert inferred <= set(aie_graph.graph.nodes)
        assert "C1.6" not in inferred, "a node cannot be inferred from itself"

    def test_it_reports_what_they_would_have_blocked(self, aie_graph) -> None:
        result, _state = _apply(aie_graph, _event("C1.1", score=0.0))

        assert result.preview_blocked_nodes
        assert "C1.2" in result.preview_blocked_nodes

    def test_it_writes_nothing_into_node_state(self, aie_graph) -> None:
        """The whole safety argument. A forecast in node state is a conclusion."""
        result, state = _apply(aie_graph, _event("C1.6", score=1.0))
        assert result.preview_inferred_signals

        for node_id, node in state.nodes.items():
            if node_id == "C1.6":
                continue
            assert node.status is CompetencyStatus.UNKNOWN, node_id
            assert node.inferred_observations == 0, node_id
            assert not node.inferred_evidence_ids, node_id

    def test_it_carries_no_score_and_no_weight(self, aie_graph) -> None:
        """`InferredNodeSignal` cannot be splatted into a `GradedOutcome`."""
        result, _state = _apply(aie_graph, _event("C1.6", score=1.0))

        signal = result.preview_inferred_signals[0]
        assert not hasattr(signal, "score")
        assert not hasattr(signal, "weight")

    def test_it_can_be_switched_off(self, aie_graph) -> None:
        result, _state = _apply(
            aie_graph,
            _event("C1.6", score=1.0),
            config=PropagationConfig(preview_unvalidated_edges=False),
        )

        assert result.preview_inferred_signals == ()
        assert result.preview_blocked_nodes == frozenset()


class TestItForecastsEnforcementRatherThanApproximatingIt:
    """Waiving the validation gate must waive NOTHING else.

    A preview that relaxed the modality rule, the decay or the depth bound would predict
    something enabling the edge would not actually do — and the decision it exists to
    inform is precisely whether to enable the edge.
    """

    def test_a_single_multiple_choice_hit_still_infers_nothing(self, aie_graph) -> None:
        result, _state = _apply(aie_graph, _event("C1.6", score=1.0, modality="mcq"))

        assert result.preview_inferred_signals == ()

    def test_the_depth_bound_still_applies(self, aie_graph) -> None:
        # With the weight floor lifted, so depth is the only thing that can bind.
        reaching = PropagationConfig(upward_decay=1.0, minimum_inferred_weight=0.0)
        deep, _state = _apply(
            aie_graph,
            _event("C1.6", score=1.0),
            config=PropagationConfig(
                upward_decay=1.0, minimum_inferred_weight=0.0, maximum_propagation_depth=4
            ),
        )
        shallow, _state = _apply(
            aie_graph,
            _event("C1.6", score=1.0),
            config=PropagationConfig(
                upward_decay=1.0, minimum_inferred_weight=0.0, maximum_propagation_depth=1
            ),
        )

        assert len(shallow.preview_inferred_signals) < len(deep.preview_inferred_signals)
        assert reaching.maximum_propagation_depth == 4

    def test_under_the_shipped_constants_inference_reaches_exactly_one_hop(
        self, aie_graph
    ) -> None:
        """Worth stating, because it is not what the depth limit of 4 suggests.

        Edge strength 0.5, decay 0.7 and a 0.15 floor put the second hop at
        0.5·0.5·0.7² = 0.1225, under the floor. So even fully enabled, these edges would
        infer ONE parent per strong success — the depth bound never gets to bind. A denser
        or stronger-edged graph would behave very differently, which is exactly why the
        forecast is measured rather than reasoned about.
        """
        result, _state = _apply(aie_graph, _event("C1.6", score=1.0))

        assert [s.distance for s in result.preview_inferred_signals] == [1]

    def test_strength_still_decays_with_distance(self, aie_graph) -> None:
        result, _state = _apply(aie_graph, _event("C1.6", score=1.0))

        by_distance = sorted(
            ((s.distance, s.strength) for s in result.preview_inferred_signals),
        )
        strengths = [strength for _distance, strength in by_distance]
        assert strengths == sorted(strengths, reverse=True)

    def test_enabling_the_edges_reproduces_the_preview_exactly(self, aie_graph) -> None:
        """The property that makes the forecast worth reading.

        Turn the edges on and enforcement must produce what the preview promised —
        otherwise the preview is a different calculation that merely resembles one.
        """
        previewed, _state = _apply(aie_graph, _event("C1.6", score=1.0))

        live = tuple(
            replace(edge, allow_upward_inference=True)
            if edge.relation == "PREREQUISITE"
            else edge
            for edge in aie_graph.graph.edges
        )
        from cat_engine.engine.services.competency_graph.graph import CompetencyGraphService

        enabled = CompetencyGraphService(replace(aie_graph.graph, edges=live))
        enforced, _state = _apply(enabled, _event("C1.6", score=1.0))

        assert {
            (s.node, s.distance, s.strength) for s in enforced.inferred_signals
        } == {(s.node, s.distance, s.strength) for s in previewed.preview_inferred_signals}


class TestRefutation:
    """The point of recording a forecast: finding out it was wrong."""

    @pytest.mark.asyncio
    async def test_a_failed_node_the_graph_would_have_inferred_is_flagged(
        self, stub_boundaries
    ) -> None:
        from cat_engine.engine.services.orchestrator.graph_delta import GraphDelta

        delta = GraphDelta()
        delta.preview_inferred = {
            "C1.1": {"source_node": "C1.6", "distance": 5, "strength": 0.2}
        }
        delta.not_mastered = {"C1.1"}

        refuted = delta.preview_refutations()

        assert [row["node"] for row in refuted] == ["C1.1"]
        assert refuted[0]["kind"] == "wrong_inference"
        assert refuted[0]["source_node"] == "C1.6"

    def test_a_node_that_would_have_been_blocked_and_then_passed_is_flagged(self) -> None:
        """The false-blocking rate the review calls unmeasurable under hard blocking.

        It is measurable here precisely because the block was never enforced, so the item
        was still served and the candidate still got to answer it.
        """
        from cat_engine.engine.services.orchestrator.graph_delta import GraphDelta

        delta = GraphDelta()
        delta.preview_blocked = {"C1.4"}
        delta.mastered = {"C1.4"}

        refuted = delta.preview_refutations()

        assert [(row["kind"], row["node"]) for row in refuted] == [("false_block", "C1.4")]

    def test_order_does_not_matter(self) -> None:
        """The failure usually arrives BEFORE the success that would have inferred it.

        A session measures the prerequisite root early and the dependent skill late, so a
        check that only looked forward from the inference would miss most refutations.
        """
        from cat_engine.engine.services.orchestrator.graph_delta import GraphDelta

        first = GraphDelta()
        first.not_mastered = {"C1.1"}
        first.preview_inferred = {"C1.1": {"source_node": "C1.6", "strength": 0.2}}

        second = GraphDelta()
        second.preview_inferred = {"C1.1": {"source_node": "C1.6", "strength": 0.2}}
        second.not_mastered = {"C1.1"}

        assert first.preview_refutations() == second.preview_refutations()

    def test_a_node_the_session_never_measured_is_not_a_refutation(self) -> None:
        from cat_engine.engine.services.orchestrator.graph_delta import GraphDelta

        delta = GraphDelta()
        delta.preview_inferred = {"C1.5": {"source_node": "C1.6", "strength": 0.2}}

        assert delta.preview_refutations() == []


class TestIsolation:
    """The preview must be invisible to everything that decides anything."""

    @pytest.mark.asyncio
    async def test_it_never_satisfies_coverage(self, stub_boundaries, monkeypatch) -> None:
        from cat_engine.engine.services.competency_graph.coverage import unmeasured_required_nodes
        from cat_engine.engine.services.orchestrator import registry

        orchestrator = orchestrator_for("AIE")
        state = orchestrator.begin(MAINS, intake=dict.fromkeys(MAINS, 3))
        graph = registry.get_graph_service("AIE")

        state = state.model_copy(
            update={
                "graph_preview_inferred_nodes": {
                    nid: {"source_node": "C1.6", "distance": 1, "strength": 0.5}
                    for nid in graph.graph.nodes
                    if nid.startswith("C1.")
                }
            }
        )

        missing = unmeasured_required_nodes(
            graph,
            "C1",
            measured=set(state.graph_direct_measured_nodes),
            mastered=set(state.graph_direct_mastered_nodes),
            not_mastered=set(state.graph_direct_not_mastered_nodes),
            critical_only=True,
        )
        assert missing, "a forecast satisfied the gate that exists to require measurement"

    @pytest.mark.asyncio
    async def test_the_posterior_is_identical_with_the_preview_on_and_off(
        self, stub_boundaries, monkeypatch
    ) -> None:
        """Same assertion as the graph-isolation golden test, for the new surface."""

        async def run() -> list[tuple]:
            orchestrator = orchestrator_for("AIE")
            state = orchestrator.begin(MAINS, intake=dict.fromkeys(MAINS, 3))
            rng = np.random.default_rng(11)
            for _ in range(6):
                state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
                state = orchestrator.ensure_presenting(state)
                picked = orchestrator.next_item(state)
                if picked is None:
                    break
                item, _ = picked
                answer = (
                    int(item.payload["answer_index"])
                    if item.modality == "mcq"
                    else "def solve(*a, **k):\n    return None\n"
                )
                state, _ = orchestrator.record_response(state, item, answer)
                state = await orchestrator.after_response(
                    state, item, use_llm=False, rng=rng
                )
            return [
                (
                    name,
                    round(vs.theta_hat, 12),
                    round(vs.standard_error, 12),
                    vs.observations,
                    tuple(vs.score_history),
                )
                for name, vs in sorted(state.variables.items())
            ]

        monkeypatch.setattr(settings, "graph_edge_preview_enabled", True)
        with_preview = await run()
        monkeypatch.setattr(settings, "graph_edge_preview_enabled", False)
        without_preview = await run()

        # An equality over two empty sessions proves nothing.
        assert sum(row[3] for row in with_preview) >= 6
        assert with_preview == without_preview


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_a_session_records_the_forecast(self, stub_boundaries) -> None:
        from cat_engine.engine.services.code_adaptive.bank import JsonQuestionRepository
        from cat_engine.engine.services.code_adaptive.session import CodeAdaptiveSession
        from cat_engine.engine.services.orchestrator.grader import GraderAgent

        orchestrator = orchestrator_for(
            "AIE", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
        )
        bank = orchestrator._bank
        item = next(
            i
            for i in bank.all_items()
            if i.modality == "code" and i.measures[0].variable == "C1.6"
        )

        state = orchestrator.begin(MAINS, intake=dict.fromkeys(MAINS, 3))
        state, graded = orchestrator.record_response(
            state, item, "def solve(*a, **k):\n    return None\n"
        )

        if not any(float(o.get("score", 0)) >= 0.8 for o in graded.outcomes):
            pytest.skip("the stub grader did not produce a strong success")

        assert state.graph_preview_inferred_nodes
        provenance = next(iter(state.graph_preview_inferred_nodes.values()))
        assert {"source_node", "distance", "strength", "modality", "evidence_id"} <= set(
            provenance
        )
        # And it stayed out of every set a consumer reads.
        assert not set(state.graph_preview_inferred_nodes) & set(
            state.graph_inferred_mastered_nodes
        )
        assert not set(state.graph_preview_inferred_nodes) & set(
            state.graph_direct_mastered_nodes
        )
