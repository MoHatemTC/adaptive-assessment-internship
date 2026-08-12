"""The gate on enabling a prerequisite edge.

An edge that is never validated is inert and harmless. An edge validated by a statistic
that does not mean what it appears to would be enforced against real candidates — blocking
questions they could have answered. So the arithmetic is tested directly.
"""

from __future__ import annotations

import json

import pytest

from cat_engine.engine.schemas.orchestration import AssessmentState
from cat_engine.engine.services.orchestrator import session_dump
from cat_engine.scripts.validate_prerequisite_edges import (
    INSUFFICIENT,
    REFUTED,
    VALIDATED,
    EdgeEvidence,
    collect,
    wilson_interval,
)


def _verdict(evidence: EdgeEvidence, *, min_parent_failures: int = 30) -> dict:
    return evidence.verdict(min_parent_failures=min_parent_failures, max_pass_rate=0.25)


class TestWilsonInterval:
    def test_zero_successes_still_has_an_upper_bound(self) -> None:
        """The reason the bar is on the interval and not the point estimate.

        0/30 is a point estimate of exactly 0, which would pass any threshold. The upper
        bound says what 30 observations can actually rule out.
        """
        lower, upper = wilson_interval(0, 30)
        assert lower == 0.0
        assert 0.10 < upper < 0.13

    def test_more_evidence_narrows_it(self) -> None:
        _, upper_30 = wilson_interval(0, 30)
        _, upper_300 = wilson_interval(0, 300)
        assert upper_300 < upper_30

    def test_no_observations_rules_out_nothing(self) -> None:
        assert wilson_interval(0, 0) == (0.0, 1.0)


class TestVerdicts:
    def test_too_few_parent_failures_is_insufficient_not_validated(self) -> None:
        evidence = EdgeEvidence("A", "B", 10, 0, 50, 45)
        verdict = _verdict(evidence)
        assert verdict["status"] == INSUFFICIENT
        assert "10 sessions" in verdict["reason"]

    def test_a_high_pass_rate_after_failure_refutes_the_edge(self) -> None:
        """Half of those who failed the parent passed the child: the edge predicts little."""
        evidence = EdgeEvidence("A", "B", 60, 30, 60, 55)
        assert _verdict(evidence)["status"] == REFUTED

    def test_a_child_nobody_passes_does_not_validate_the_edge(self) -> None:
        """The contrast test.

        P(pass|fail) = 0 looks perfect, but P(pass|pass) is 0.02 — almost nobody passes
        the child either way. The edge has explained nothing and must not be enabled.
        """
        evidence = EdgeEvidence("A", "B", 60, 0, 100, 2)
        verdict = _verdict(evidence)
        assert verdict["status"] == REFUTED
        assert "no separation" in verdict["reason"]

    def test_a_genuinely_predictive_edge_validates(self) -> None:
        evidence = EdgeEvidence("A", "B", 80, 1, 200, 170)
        verdict = _verdict(evidence)
        assert verdict["status"] == VALIDATED
        assert verdict["ci_upper"] <= 0.25
        assert verdict["p_pass_child_given_pass_parent"] == pytest.approx(0.85)

    def test_no_parent_passes_leaves_the_contrast_unmeasurable(self) -> None:
        evidence = EdgeEvidence("A", "B", 60, 0, 0, 0)
        assert _verdict(evidence)["status"] == INSUFFICIENT


class TestCollect:
    @staticmethod
    def _record(*, mastered: list[str], not_mastered: list[str], source: str) -> dict:
        state = AssessmentState(
            session_id="s",
            graph_direct_mastered_nodes=mastered,
            graph_direct_not_mastered_nodes=not_mastered,
            graph_direct_measured_nodes=mastered + not_mastered,
        )
        return {"source": source, "bank_id": "AIE", "state": state.model_dump(mode="json")}

    def test_it_counts_both_arms_and_ignores_unmeasured_sessions(self, aie_graph) -> None:
        edges = [e for e in aie_graph.graph.edges if e.relation == "PREREQUISITE"][:1]
        parent, child = edges[0].from_id, edges[0].to_id

        records = [
            self._record(mastered=[child], not_mastered=[parent], source="live"),
            self._record(mastered=[], not_mastered=[parent, child], source="live"),
            self._record(mastered=[parent, child], not_mastered=[], source="live"),
            # Neither node measured: contributes to no arm.
            self._record(mastered=["C1.3"], not_mastered=[], source="live"),
        ]

        evidence, live, non_live = collect(records, edges)
        entry = evidence[(parent, child)]

        assert (live, non_live) == (4, 0)
        assert entry.parent_failed_child_measured == 2
        assert entry.parent_failed_child_passed == 1
        assert entry.parent_passed_child_measured == 1
        assert entry.parent_passed_child_passed == 1

    def test_simulated_records_are_counted_but_never_used(self, aie_graph) -> None:
        """A simulated candidate has no dependency structure to measure."""
        edges = [e for e in aie_graph.graph.edges if e.relation == "PREREQUISITE"][:1]
        parent, child = edges[0].from_id, edges[0].to_id

        records = [self._record(mastered=[child], not_mastered=[parent], source="simulated")]
        evidence, live, non_live = collect(records, edges)

        assert (live, non_live) == (0, 1)
        assert evidence[(parent, child)].parent_failed_child_measured == 0


class TestSessionDump:
    def test_it_is_off_unless_a_directory_is_configured(self, monkeypatch) -> None:
        from cat_engine.engine.config.settings import settings

        monkeypatch.setattr(settings, "cat_session_dump_dir", "")
        assert session_dump.dump_path("AIE") is None
        assert session_dump.record_session(AssessmentState(session_id="s"), "AIE") is None

    def test_it_appends_one_line_per_session_keyed_by_bank(self, monkeypatch, tmp_path) -> None:
        from cat_engine.engine.config.settings import settings

        monkeypatch.setattr(settings, "cat_session_dump_dir", str(tmp_path))
        session_dump.record_session(AssessmentState(session_id="a"), "AIE", stop_reason="precision")
        session_dump.record_session(AssessmentState(session_id="b"), "AIE")
        session_dump.record_session(AssessmentState(session_id="c"), "DA")

        aie = (tmp_path / "AIE.jsonl").read_text(encoding="utf-8").strip().splitlines()
        assert len(aie) == 2
        assert json.loads(aie[0])["state"]["session_id"] == "a"
        assert json.loads(aie[0])["source"] == session_dump.SOURCE_LIVE
        assert (tmp_path / "DA.jsonl").exists()

        assert len(session_dump.read_sessions(tmp_path)) == 3

    def test_an_unwritable_directory_never_reaches_the_caller(self, monkeypatch) -> None:
        """Telemetry that can fail an assessment is worse than no telemetry."""
        from cat_engine.engine.config.settings import settings

        monkeypatch.setattr(settings, "cat_session_dump_dir", "/proc/nonexistent/nope")
        assert session_dump.record_session(AssessmentState(session_id="s"), "AIE") is None
