"""The report says why every competency is in the state it is in.

Reporting comes before enforcement. An assessor who can read "inferred from DA.6, one hop"
can reject a bad prerequisite edge immediately; a metric needs hundreds of sessions to say
the same thing. So this must be populated whether or not any graph effect is enabled.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.config.settings import settings
from app.services.competency_graph.config import PropagationConfig
from app.services.competency_graph.evidence import EvidenceEvent
from app.services.competency_graph.propagation import apply_events
from app.services.competency_graph.report import build_main_report, build_node_reports
from tests.conftest import orchestrator_for


def _event(target: str, *, score: float, modality: str = "code") -> EvidenceEvent:
    return EvidenceEvent(
        evidence_id=f"s:item:1:{modality}:{target}:-",
        session_id="s",
        item_id="item",
        modality=modality,
        target_node=target,
        score=score,
        weight=1.0,
        confidence=1.0,
        source="item",
    )


class TestNodeReports:
    def test_it_records_direct_and_inferred_provenance_separately(self, da_graph) -> None:
        _results, state, _ledger = apply_events(
            da_graph, [_event("DA.6", score=1.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )

        by_id = {r.competency_id: r for r in build_node_reports(da_graph, state)}

        direct = by_id["DA.6"]
        assert direct.status == "direct_mastered"
        assert direct.direct_observations == 1
        assert direct.direct_evidence_ids and not direct.inferred_evidence_ids

        inferred = by_id["DA.3"]
        assert inferred.status == "inferred_mastered"
        assert inferred.inferred_evidence_ids == direct.direct_evidence_ids
        assert inferred.direct_observations == 0

    def test_a_blocked_node_names_what_blocked_it(self, da_graph) -> None:
        _results, state, _ledger = apply_events(
            da_graph, [_event("DA.1", score=0.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )

        by_id = {r.competency_id: r for r in build_node_reports(da_graph, state)}

        assert by_id["DA.4"].status == "blocked"
        assert by_id["DA.4"].blocked_by == ["DA.1"]

    def test_untouched_nodes_are_omitted_but_partial_credit_is_not(self, da_graph) -> None:
        """A partial score leaves the status UNKNOWN and is still direct measurement.

        It is what the coverage gate counts, so a report that hid it would disagree with
        the gate about what had been measured.
        """
        _results, state, _ledger = apply_events(
            da_graph, [_event("DA.5", score=0.5)], config=PropagationConfig(minimum_failures_to_block=1)
        )

        reports = build_node_reports(da_graph, state)
        reported = {r.competency_id for r in reports}

        assert reported == {"DA.5"}
        [measured] = reports
        assert measured.status == "unknown"
        assert measured.direct_observations == 1


class TestMainReport:
    def test_an_inference_does_not_resolve_a_critical_node(self, da_graph) -> None:
        """The substitution the coverage gate exists to prevent, stated in the report."""
        _results, state, _ledger = apply_events(
            da_graph, [_event("DA.6", score=1.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )

        report = build_main_report(
            da_graph, state, "DA", unmeasured_nodes=["DA.1", "DA.2"]
        )

        assert report.nodes_directly_measured == 1
        assert report.nodes_inferred >= 2
        # DA.1 and DA.2 are the critical nodes, and both are only INFERRED.
        assert report.critical_nodes_resolved is False
        assert report.coverage_satisfied is False

    def test_it_counts_nodes_not_observations(self, da_graph) -> None:
        """Node counts must never be mistaken for the CAT observation count.

        `observations` counts exactly one thing — main-level outcomes folded into a
        posterior — and all three convergence guards read it and nothing else.
        """
        _results, state, _ledger = apply_events(
            da_graph, [_event("DA.6", score=1.0)], config=PropagationConfig(minimum_failures_to_block=1)
        )
        report = build_main_report(da_graph, state, "DA", unmeasured_nodes=[])

        payload = report.as_dict()
        assert "observations" not in payload
        assert payload["nodes_directly_measured"] == 1


class TestReportWiring:
    @pytest.mark.asyncio
    async def test_a_session_report_carries_node_provenance_with_effects_off(
        self, monkeypatch, stub_boundaries
    ) -> None:
        """Populated regardless of the enforcement flags — that is the point."""
        from app.services.code_adaptive.bank import JsonQuestionRepository
        from app.services.code_adaptive.session import CodeAdaptiveSession
        from app.services.orchestrator.grader import GraderAgent
        from tests.test_orchestration_flow import answer_for

        for flag in (
            "graph_filtering_enabled",
            "graph_upward_inference_enabled",
            "graph_descendant_blocking_enabled",
            "graph_utility_enabled",
        ):
            monkeypatch.setattr(settings, flag, False)

        orchestrator = orchestrator_for(
            "DA", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
        )
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        rng = np.random.default_rng(4)
        for _ in range(4):
            state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
            state = orchestrator.ensure_presenting(state)
            nxt = orchestrator.next_item(state)
            if nxt is None:
                break
            item, _ = nxt
            state, _ = orchestrator.record_response(state, item, answer_for(item))
            state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)

        report = orchestrator.summarise(state, "test")

        assert report.graph, "the graph report was empty with effects disabled"
        assert report.graph["nodes"], "no node provenance recorded"
        assert report.graph["mains"][0]["competency_id"] == "DA"

    @pytest.mark.asyncio
    async def test_the_variable_report_carries_the_band_probability(
        self, stub_boundaries
    ) -> None:
        from app.services.code_adaptive.bank import JsonQuestionRepository
        from app.services.code_adaptive.session import CodeAdaptiveSession
        from app.services.orchestrator.grader import GraderAgent
        from tests.test_orchestration_flow import answer_for

        orchestrator = orchestrator_for(
            "DA", GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
        )
        state = orchestrator.begin(["DA"], intake={"DA": 3})
        rng = np.random.default_rng(6)
        for _ in range(3):
            state = await orchestrator.fill_queue(state, use_llm=False, rng=rng)
            state = orchestrator.ensure_presenting(state)
            item, _ = orchestrator.next_item(state)
            state, _ = orchestrator.record_response(state, item, answer_for(item))
            state = await orchestrator.after_response(state, item, use_llm=False, rng=rng)

        [variable] = orchestrator.summarise(state, "test").variables

        assert 0.0 < variable.p_reported_band <= 1.0
        assert variable.band_probability >= variable.p_reported_band
        assert variable.most_probable_band is not None
        assert variable.credible_interval_95 is not None
        # The honest name and the deprecated alias agree.
        assert variable.precision_index_pct == variable.certainty_pct
