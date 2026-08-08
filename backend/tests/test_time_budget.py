"""Time is the binding constraint, and the reported stop must say so.

The review's C3: the session budget was arithmetically infeasible. Sixty questions in
ninety minutes implies ninety seconds an item; a code item costs eight times that. Ranking
on information PER ITEM inside a session bounded by MINUTES selects the diet that cannot
finish.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.config.settings import settings
from app.schemas.orchestration import DEFAULT_SECONDS_BY_MODALITY
from app.services.orchestrator import budget
from app.services.orchestrator import variables as variables_module
from app.services.orchestrator.picker import RankScore, expected_weight_for, rank
from tests.conftest import orchestrator_for


class TestFeasibility:
    def test_three_mains_fit_and_four_do_not(self) -> None:
        """The arithmetic that set the defaults. 3 x 12 questions = 76.5 min of 90."""
        assert budget.check(3).feasible
        assert not budget.check(4).feasible

    def test_the_affordable_slow_item_count_is_derived_not_hardcoded(self) -> None:
        assert budget.max_expensive_items_per_main(3) == 1
        assert budget.max_expensive_items_per_main(4) == 0
        assert budget.max_expensive_items_per_main(1) > 1

    def test_an_infeasible_configuration_warns_rather_than_raising(self, caplog) -> None:
        """A candidate mid-assessment is not the place to fail on a config problem."""
        with caplog.at_level("WARNING"):
            result = budget.warn_if_infeasible(6)

        assert result.feasible is False
        assert "time budget does not close" in caplog.text

    def test_a_longer_session_makes_more_mains_feasible(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "orchestrator_time_limit_minutes", 150)
        assert budget.check(4).feasible

    def test_the_item_cap_can_no_longer_bind_before_the_clock(self) -> None:
        """`orchestrator_max_items` is a runaway guard, not a budget.

        At three mains the session can administer at most 36 items, so a cap of 120 is
        unreachable — which is the point: time binds first, and the setting should not
        look like the operative limit.
        """
        assert settings.orchestrator_max_items > 3 * settings.cat_max_questions


class TestRanking:
    def test_information_keeps_meaning_information_while_ranking_uses_time(
        self, da_bank
    ) -> None:
        """The audit record must say what the engine valued, not what it sorted on."""
        state = variables_module.seed_variable("DA", 3, False)
        pool = da_bank.shortlist("DA", exclude=set())

        shortlist, _criterion = rank(pool, state, "DA", top_k=1)

        for item, score in shortlist:
            assert isinstance(score, RankScore)
            assert score.estimated_seconds == item.expected_seconds
            assert score.expected_weight == expected_weight_for(item.modality)
            # The reported information is untouched by time or weight.
            assert score.rank_key != pytest.approx(score.information) or (
                score.estimated_seconds == 60.0 and score.expected_weight == 1.0
            )

    def test_the_shortlist_is_ordered_by_the_ranking_key(self, da_bank) -> None:
        state = variables_module.seed_variable("DA", 3, False)
        pool = da_bank.shortlist("DA", exclude=set())

        shortlist, _ = rank(pool, state, "DA", top_k=1)
        keys = [score.rank_key for _item, score in shortlist]

        assert keys == sorted(keys, reverse=True)

    def test_turning_it_off_ranks_on_information_alone(self, da_bank, monkeypatch) -> None:
        state = variables_module.seed_variable("DA", 3, False)
        pool = da_bank.shortlist("DA", exclude=set())

        monkeypatch.setattr(settings, "orchestrator_time_aware_selection_enabled", False)
        shortlist, _ = rank(pool, state, "DA", top_k=1)

        for _item, score in shortlist:
            assert score.rank_key == pytest.approx(score.information)

    def test_a_slow_item_must_be_proportionally_better_to_win(self, da_bank) -> None:
        """A code item at eight times the cost needs eight times the information."""
        state = variables_module.seed_variable("DA", 3, False)
        pool = da_bank.shortlist("DA", exclude=set())
        shortlist, _ = rank(pool, state, "DA", top_k=1)

        by_modality: dict[str, RankScore] = {}
        for item, score in shortlist:
            by_modality.setdefault(item.modality, score)

        for modality, score in by_modality.items():
            expected = (
                score.information
                * score.expected_weight
                / (DEFAULT_SECONDS_BY_MODALITY.get(modality, score.estimated_seconds) / 60.0)
            )
            if score.estimated_seconds == DEFAULT_SECONDS_BY_MODALITY.get(modality):
                assert score.rank_key == pytest.approx(expected, rel=1e-6)

    def test_mcq_expected_weight_is_exact_and_the_others_are_not(self) -> None:
        """MCQ has no degradation path; the rest are opinions read off the graders."""
        assert expected_weight_for("mcq") == 1.0
        assert 0.0 < expected_weight_for("code") < 1.0
        assert 0.0 < expected_weight_for("voice") < 1.0


class TestStopReasons:
    def test_running_out_of_time_is_not_reported_as_running_out_of_questions(self) -> None:
        state = variables_module.seed_variable("DA", 3, False)

        timed_out = variables_module.mark_time_exhausted(state)
        exhausted = variables_module.mark_exhausted(state)

        assert timed_out.stop_reason == "time_budget"
        assert exhausted.stop_reason == "bank_exhausted"
        assert timed_out.converged is False and exhausted.converged is False

    @pytest.mark.asyncio
    async def test_no_item_is_queued_that_cannot_finish_in_the_remaining_time(
        self, monkeypatch
    ) -> None:
        """Reserving up front is what keeps room for the blueprint's slow items."""
        orchestrator = orchestrator_for("DA")
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        # One minute left of ninety, so only the cheapest modality can still fit.
        state = state.model_copy(update={"elapsed_minutes": 89.0})

        state = await orchestrator.fill_queue(
            state, use_llm=False, rng=np.random.default_rng(3)
        )

        for candidate in state.queue.values():
            item = orchestrator._bank.get(candidate.item_id)
            assert item is not None
            assert item.expected_seconds <= 60.0

    @pytest.mark.asyncio
    async def test_a_variable_with_no_affordable_item_finalises_on_time(self) -> None:
        orchestrator = orchestrator_for("DA")
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        # Past the limit entirely: nothing fits, and the bank is untouched.
        state = state.model_copy(update={"elapsed_minutes": 95.0})

        state = await orchestrator.fill_queue(
            state, use_llm=False, rng=np.random.default_rng(3)
        )

        assert state.variables["DA"].finalised is True
        assert state.variables["DA"].stop_reason == "time_budget"

    @pytest.mark.asyncio
    async def test_a_queued_item_is_revalidated_after_other_variables_spend_time(self) -> None:
        """A queue promise must not become permission to overrun the session clock."""
        orchestrator = orchestrator_for("AIE")
        state = orchestrator.begin(["C1"], intake={"C1": 3})
        state = await orchestrator.fill_queue(
            state, use_llm=False, rng=np.random.default_rng(4)
        )
        original = state.queue["C1"]
        slow = orchestrator._bank.get("code_c1_025")
        assert slow is not None and slow.expected_seconds == 900.0

        stale = original.model_copy(
            update={
                "item_id": slow.item_id,
                "modality": slow.modality,
                "estimated_seconds": slow.expected_seconds,
            }
        )
        # 11.5 usable minutes remain after the reserve, so this 15-minute promise was
        # valid earlier in the session but is no longer admissible now.
        state = state.model_copy(
            update={"queue": {"C1": stale}, "elapsed_minutes": 78.0}
        )

        refreshed = await orchestrator.fill_queue(
            state,
            use_llm=False,
            rng=np.random.default_rng(4),
            only=set(),
        )

        replacement = refreshed.queue.get("C1")
        assert replacement is not None
        replacement_item = orchestrator._bank.get(replacement.item_id)
        assert replacement_item is not None
        assert replacement.item_id != slow.item_id
        assert replacement_item.expected_seconds <= 11.5 * 60.0

    @pytest.mark.asyncio
    async def test_a_ready_coverage_item_precedes_a_redundant_precision_probe(self) -> None:
        """The scheduler must not let a hard coverage move expire in another slot."""
        orchestrator = orchestrator_for("AIE")
        state = orchestrator.begin(["C1", "C6"], intake={"C1": 3, "C6": 3})
        state = await orchestrator.fill_queue(
            state, use_llm=False, rng=np.random.default_rng(4)
        )
        template = next(iter(state.queue.values()))
        c1_item = orchestrator._bank.get("voice_c1_025")
        c6_item = orchestrator._bank.get("C6-Q015")
        assert c1_item is not None and c6_item is not None

        graph = orchestrator._graph()
        assert graph is not None
        measured = [node for node in graph.graph.nodes if node != "C6.5"]
        queue = {
            "C1": template.model_copy(
                update={
                    "variable": "C1",
                    "item_id": c1_item.item_id,
                    "modality": c1_item.modality,
                    "estimated_seconds": c1_item.expected_seconds,
                }
            ),
            "C6": template.model_copy(
                update={
                    "variable": "C6",
                    "item_id": c6_item.item_id,
                    "modality": c6_item.modality,
                    "estimated_seconds": c6_item.expected_seconds,
                }
            ),
        }
        state = state.model_copy(
            update={"queue": queue, "graph_direct_measured_nodes": measured}
        )

        assert orchestrator.choose_variable(state) == "C6"


class TestModalityBlueprint:
    @pytest.mark.asyncio
    async def test_a_session_still_administers_every_required_modality(
        self, stub_boundaries
    ) -> None:
        """Pure information per minute makes a mixed assessment all-MCQ.

        Measured on this bank, MCQ wins by roughly 6.6x, so no soft bonus survives — the
        blueprint has to be a pool constraint. It yields to coverage, which is why this
        runs a whole session rather than only to the observation floor: on a bank whose
        every sub-competency is required, coverage legitimately owns the first six picks.
        """
        from app.services.code_adaptive.bank import JsonQuestionRepository
        from app.services.code_adaptive.session import CodeAdaptiveSession
        from app.services.orchestrator.grader import GraderAgent
        from tests.test_orchestration_flow import answer_for

        orchestrator = orchestrator_for(
            "DA", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
        )
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        rng = np.random.default_rng(5)

        for _ in range(40):
            state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
            stop, _ = orchestrator.should_stop(state)
            if stop:
                break
            state = orchestrator.ensure_presenting(state)
            nxt = orchestrator.next_item(state)
            if nxt is None:
                break
            item, _ = nxt
            state, _ = orchestrator.record_response(state, item, answer_for(item))
            state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)

        # What was ADMINISTERED, not what moved an estimate: a zero-weight response is
        # still a question the candidate answered.
        served = set()
        for item_id in state.served_item_ids:
            served_item = orchestrator._bank.get(item_id)
            if served_item is not None:
                served.add(served_item.modality)
        assert "code" in served, f"the blueprint did not deliver a code item: {served}"

    @pytest.mark.asyncio
    async def test_every_main_is_assessed_in_every_modality_the_bank_supplies(
        self, stub_boundaries
    ) -> None:
        """The guarantee, on the bank that actually has three modalities.

        Without the blueprint, measured over five AI Engineer sessions, only 4 of 15
        competencies saw all three modalities and 3 saw multiple choice alone. It costs
        nothing: the stop reasons are identical with it and without it.
        """
        from app.services.code_adaptive.bank import JsonQuestionRepository
        from app.services.code_adaptive.session import CodeAdaptiveSession
        from app.services.orchestrator.grader import GraderAgent
        from tests.test_orchestration_flow import answer_for

        orchestrator = orchestrator_for(
            "AIE", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
        )
        mains = ["C1", "C3", "C6"]
        state = orchestrator.begin(mains, intake={m: 3 for m in mains})
        rng = np.random.default_rng(3)

        for _ in range(80):
            state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
            stop, _ = orchestrator.should_stop(state)
            if stop:
                break
            state = orchestrator.ensure_presenting(state)
            nxt = orchestrator.next_item(state)
            if nxt is None:
                break
            item, _ = nxt
            state, _ = orchestrator.record_response(state, item, answer_for(item))
            state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)

        for report in orchestrator.summarise(state, "done").variables:
            assert set(report.modalities_used) == {"mcq", "code", "voice"}, (
                f"{report.variable} was assessed only in {report.modalities_used}"
            )

    @pytest.mark.asyncio
    async def test_a_minimum_the_bank_cannot_supply_does_not_starve_selection(self) -> None:
        """The Data Analysis bank has no `voice` items at all.

        An unmeetable minimum is a bank fact, not a deficit. Enforcing one would restrict
        every pick to a modality that does not exist and never release, which starves the
        session for the rest of its length.
        """
        orchestrator = orchestrator_for("DA")
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        available = orchestrator._bank.shortlist("DA", exclude=set())
        assert not any(i.modality == "voice" for i in available)

        deficit = orchestrator._modality_deficit_pool(
            pool=available, state=state, variable="DA", available=available
        )

        assert all(item.modality != "voice" for item in deficit)

    @pytest.mark.asyncio
    async def test_coverage_takes_precedence_over_the_modality_blueprint(self) -> None:
        """Coverage GATES convergence; the modality mix is a blueprint preference.

        A main that converges without its required sub-competencies measured has claimed
        a measurement it did not make, which is the worse failure of the two.
        """
        orchestrator = orchestrator_for("DA")
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        # Close enough to the floor that the blueprint would restrict, with coverage still
        # outstanding (nothing measured yet).
        variable = state.variables["DA"].model_copy(
            update={"observations": settings.cat_precision_min_questions - 1}
        )
        state = state.model_copy(update={"variables": {"DA": variable}})

        state = await orchestrator.fill_queue(
            state, use_llm=False, rng=np.random.default_rng(2)
        )

        candidate = state.queue["DA"]
        item = orchestrator._bank.get(candidate.item_id)
        assert item is not None
        # Coverage won: the pick measures an unmeasured required node, whatever modality
        # the blueprint was owed.
        assert any(m.variable.startswith("DA.") for m in item.measures)
