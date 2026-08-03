"""One node, one authoritative state, several main competencies recalculated.

The shared-evidence case the design exists for (specification section 22). Until the AI
Engineer bank carried it, the whole apparatus had nothing to act on: every item measured
exactly one sub-competency at weight 1.0 and every sub-competency belonged to exactly one
main, so no response could ever move more than one estimate. A candidate answering "why
must LLM JSON output be validated" was credited under application engineering and not at
all under the LLM track — the same question with a different label on it.

Two mechanisms, and they are not the same thing:

    CROSS-LOADED ITEM   `measures` names variables under two mains, so ONE response
                        updates both posteriors and invalidates both queue slots.
    SHARED NODE         the graph declares one sub-competency under two mains, so
                        coverage for either is satisfied by measuring it once.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.competency_graph.coverage import (
    sub_nodes_for_main,
    unmeasured_required_nodes,
)
from app.services.orchestrator import registry
from app.services.orchestrator.competency import affected_mains, rollup_outcomes
from app.services.orchestrator.outcome import GradedOutcome
from tests.conftest import orchestrator_for

MAINS = ["C1", "C3", "C6"]
SHARED = {"C1.6": {"C1", "C6"}, "C6.3": {"C6", "C1"}, "C3.9": {"C3", "C6"}, "C3.10": {"C3", "C6"}}


@pytest.fixture(scope="module")
def cross_loaded(aie_bank):
    items = [
        item
        for item in aie_bank.all_items()
        if len({m.variable.split(".")[0] for m in item.measures}) > 1
    ]
    assert items, "the bank carries no item evidencing more than one main"
    return items


class TestSharedNodes:
    def test_a_shared_node_reports_every_main_it_serves(self, aie_graph) -> None:
        for node, mains in SHARED.items():
            assert set(aie_graph.mains_for_node(node)) == mains, node

    def test_an_unshared_node_still_reports_exactly_one(self, aie_graph) -> None:
        assert aie_graph.mains_for_node("C1.1") == ("C1",)

    def test_a_shared_node_appears_in_both_mains_coverage_sets(self, aie_graph) -> None:
        """Measuring it once is what satisfies both — that is the saving."""
        assert "C1.6" in sub_nodes_for_main(aie_graph, "C1", critical_only=False)
        assert "C1.6" in sub_nodes_for_main(aie_graph, "C6", critical_only=False)

    def test_measuring_a_shared_node_once_clears_it_from_both(self, aie_graph) -> None:
        measured = {"C1.6"}
        for main in ("C1", "C6"):
            missing = unmeasured_required_nodes(
                aie_graph,
                main,
                measured=measured,
                mastered=set(),
                not_mastered=set(),
                critical_only=True,
            )
            assert "C1.6" not in missing, main

    def test_contributions_into_every_main_still_sum_to_one(self, aie_graph) -> None:
        """Adding a shared contributor must renormalise, not inflate."""
        totals: dict[str, float] = {}
        for edge in aie_graph.graph.edges:
            if edge.relation == "CONTRIBUTES_TO":
                totals[edge.to_id] = totals.get(edge.to_id, 0.0) + edge.weight

        assert set(totals) == set(MAINS)
        for main, total in totals.items():
            assert total == pytest.approx(1.0), f"{main} sums to {total}"

    def test_the_prerequisite_chain_follows_the_home_main_only(self, aie_graph) -> None:
        """Being reused elsewhere is not a second dependency.

        A shared node keeps one chain; giving it a second would let a main's numbering
        order imply prerequisites in a track it merely borrows the node from.
        """
        prerequisites = [e for e in aie_graph.graph.edges if e.relation == "PREREQUISITE"]
        assert len(prerequisites) == 30
        for edge in prerequisites:
            assert edge.from_id.split(".")[0] == edge.to_id.split(".")[0], (
                f"{edge.from_id} -> {edge.to_id} crosses main competencies"
            )

    def test_they_are_still_inert(self, aie_graph) -> None:
        for edge in aie_graph.graph.edges:
            if edge.relation == "PREREQUISITE":
                assert not edge.allow_upward_inference
                assert not edge.allow_downward_blocking


class TestCrossLoadedItems:
    def test_the_bank_carries_them(self, cross_loaded) -> None:
        assert len(cross_loaded) >= 15

    def test_one_item_evidences_several_mains(self, cross_loaded) -> None:
        for item in cross_loaded:
            assert len(affected_mains(item, set(MAINS))) > 1, item.item_id

    def test_the_secondary_loading_is_partial(self, cross_loaded) -> None:
        """It is what the item is PARTLY about. A second full-weight claim would say the
        question measures two things completely, which is not what a shared item is."""
        for item in cross_loaded:
            primary, *rest = item.measures
            assert primary.weight == 1.0, item.item_id
            for secondary in rest:
                assert 0.0 < secondary.weight < 1.0, item.item_id

    def test_one_response_produces_one_outcome_per_main(self, cross_loaded) -> None:
        item = cross_loaded[0]
        raw = [
            GradedOutcome(m.variable, score=1.0, weight=m.weight).__dict__
            for m in item.measures
        ]

        rolled = rollup_outcomes(raw, set(MAINS))

        assert len({o.variable for o in rolled}) > 1
        # Each main is weighted by what THIS item told it, not by the sum across mains.
        by_main = {o.variable: o for o in rolled}
        assert by_main[item.measures[0].variable.split(".")[0]].weight == pytest.approx(1.0)


class TestSharedMainGain:
    """Specification section 27's S(q).

    Ranking is per variable, so an item that also evidences another OPEN main delivers
    value this variable's information score cannot see. Without the correction the
    sharing is real but rarely chosen — measured on this bank, evidence reuse sat at
    1.109 main updates per scored response with the gain off and 1.126 with it on.
    """

    def _modifiers(self, orchestrator, state, variable: str):
        pool = orchestrator._bank.shortlist(variable, exclude=set())
        return orchestrator._graph_utility_modifiers(
            pool=pool, state=state, variable=variable
        ), pool

    @pytest.mark.asyncio
    async def test_it_is_off_unless_graph_utility_is_enabled(self, monkeypatch) -> None:
        from app.config.settings import settings

        monkeypatch.setattr(settings, "graph_utility_enabled", False)
        orchestrator = orchestrator_for("AIE")
        state = orchestrator.begin(MAINS, intake={m: 3 for m in MAINS})

        modifiers, _pool = self._modifiers(orchestrator, state, "C1")

        assert not modifiers

    @pytest.mark.asyncio
    async def test_an_item_measuring_another_open_main_is_preferred(
        self, monkeypatch, cross_loaded
    ) -> None:
        from app.config.settings import settings

        monkeypatch.setattr(settings, "graph_utility_enabled", True)
        orchestrator = orchestrator_for("AIE")
        state = orchestrator.begin(MAINS, intake={m: 3 for m in MAINS})

        modifiers, pool = self._modifiers(orchestrator, state, "C1")
        shared = {i.item_id for i in cross_loaded}

        bonused = {k for k, v in modifiers.items() if v > 0}
        assert bonused, "no item received the shared-main gain"
        assert bonused <= shared, "an item that measures one main was bonused"
        for item_id in bonused:
            assert modifiers[item_id] == pytest.approx(settings.graph_shared_main_gain)

    @pytest.mark.asyncio
    async def test_a_main_that_has_finalised_no_longer_counts(self, monkeypatch) -> None:
        """The gain is for evidence the session can still USE."""
        from app.config.settings import settings

        monkeypatch.setattr(settings, "graph_utility_enabled", True)
        orchestrator = orchestrator_for("AIE")
        state = orchestrator.begin(MAINS, intake={m: 3 for m in MAINS})
        closed = {
            main: (
                variable.model_copy(update={"finalised": True, "stop_reason": "precision"})
                if main in ("C3", "C6")
                else variable
            )
            for main, variable in state.variables.items()
        }
        state = state.model_copy(update={"variables": closed})

        modifiers, _pool = self._modifiers(orchestrator, state, "C1")

        assert not any(v > 0 for v in (modifiers or {}).values())


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_answering_one_item_moves_every_main_it_measures(
        self, cross_loaded, stub_boundaries
    ) -> None:
        """The behaviour the shared design exists to produce."""
        orchestrator = orchestrator_for("AIE")
        item = next(i for i in cross_loaded if i.modality == "mcq")
        touched = sorted(affected_mains(item, set(MAINS)))
        assert len(touched) > 1

        state = orchestrator.begin(MAINS, intake={m: 3 for m in MAINS})
        before = {m: state.variables[m].theta_hat for m in touched}

        state, _ = orchestrator.record_response(
            state, item, int(item.payload["answer_index"])
        )

        for main in touched:
            assert state.variables[main].theta_hat != before[main], (
                f"{main} was measured by {item.item_id} and did not move"
            )
            assert state.variables[main].observations == 1

    @pytest.mark.asyncio
    async def test_it_invalidates_the_queue_slot_of_every_main_it_touched(
        self, cross_loaded, stub_boundaries
    ) -> None:
        """Those picks were made against estimates this answer has just changed."""
        orchestrator = orchestrator_for("AIE")
        item = next(i for i in cross_loaded if i.modality == "mcq")
        touched = affected_mains(item, set(MAINS))

        state = orchestrator.begin(MAINS, intake={m: 3 for m in MAINS})
        state = await orchestrator.fill_queue(
            state, use_llm=False, rng=np.random.default_rng(0)
        )
        assert set(state.queue) >= touched

        state, _ = orchestrator.record_response(
            state, item, int(item.payload["answer_index"])
        )

        assert not (touched & set(state.queue))

    @pytest.mark.asyncio
    async def test_the_shared_node_is_recorded_once_with_one_state(
        self, cross_loaded, stub_boundaries
    ) -> None:
        """One node, one authoritative state — not one copy per main."""
        orchestrator = orchestrator_for("AIE")
        item = next(
            i for i in cross_loaded if i.modality == "mcq" and i.measures[0].variable == "C1.6"
        )

        state = orchestrator.begin(MAINS, intake={m: 3 for m in MAINS})
        state, _ = orchestrator.record_response(
            state, item, int(item.payload["answer_index"])
        )

        assert state.graph_node_states["C1.6"]["direct_observations"] == 1
        assert len(state.graph_node_states["C1.6"]["direct_evidence_ids"]) == 1
        # And it counts as measured for BOTH mains without being measured twice.
        assert "C1.6" in state.graph_direct_measured_nodes
