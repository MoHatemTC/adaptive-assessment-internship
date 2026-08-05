"""The harness reads what the engine now writes.

Every defect fixed here was SILENT. The harness imports cleanly against this branch and
runs to completion; it just computes the wrong numbers, and the wrong numbers are all
plausible. A wrong-inference rate of 0/0 rendered as "no evidence of harm" rather than
"no evidence", and a coverage-satisfaction rate of 1.0 was reported for an arm that has
no coverage gate.

These tests pin the seams between the two, because a seam that produces a believable
number when it breaks will not announce itself.
"""

from __future__ import annotations

import json

import pytest

from evaluation.analyse import _dominant_source, _rate_over_gated, dag_safety


class _Cohort:
    """The one thing `dag_safety` needs from a cohort: node truth and true edges."""

    def __init__(self, nodes: dict[str, int], prerequisites=()):
        self.simulees = [type("S", (), {"simulee_id": "sim-1", "nodes": nodes})()]
        self.true_prerequisites = list(prerequisites)


def _record(**graph) -> dict:
    return {"simulee_id": "sim-1", "variables": [], "graph": graph}


class TestR2SafetyEvidenceSurvivesTheEnforcementSplit:
    """`graph_delta` split the enforced sets from the mirrors; the harness read one.

    C-shipped and C-hybrid hold the deployment switches off by definition, so
    `inferred_mastered` is empty for them by construction. An analysis reading only that
    reports 0 events out of 0 verified for exactly the arms the study exists to judge.
    """

    def test_the_mirror_is_counted_when_the_enforced_set_is_empty(self):
        cohort = _Cohort({"A": 0, "B": 1})
        safety = dag_safety([_record(shadow_inferred_mastered=["A", "B"])], cohort)

        assert safety["C-DAG-03_wrong_inference"]["verified"] == 2
        assert safety["C-DAG-03_wrong_inference"]["events"] == 1
        assert safety["wrong_inference_source"] == "shadow"

    def test_the_enforced_set_wins_when_both_are_present(self):
        """What the deployment ACTED on is the headline; the mirror is the fallback."""
        cohort = _Cohort({"A": 0, "B": 1, "C": 0})
        safety = dag_safety(
            [_record(inferred_mastered=["A"], shadow_inferred_mastered=["A", "B", "C"])],
            cohort,
        )
        assert safety["C-DAG-03_wrong_inference"]["verified"] == 1
        assert safety["wrong_inference_source"] == "enforced"

    def test_blocking_gets_the_same_treatment(self):
        cohort = _Cohort({"A": 1, "B": 0})
        safety = dag_safety([_record(shadow_blocked=["A", "B"])], cohort)
        # A false block is a node the candidate HAS that the graph declared unreachable.
        assert safety["C-DAG-04_false_blocking"]["events"] == 1
        assert safety["C-DAG-04_false_blocking"]["verified"] == 2


class TestPreviewIsReportedButNeverPooled:
    """The preview answers a different question and may not share a column.

    On the shipped configuration it is the ONLY source with events — the AIE edges all
    declare `unvalidated` and are inert, so the traversal concludes nothing to enforce or
    to mirror. That makes pooling tempting and wrong: a rate computed from edges nobody
    has validated is not comparable with one computed from edges a deployment acted on.
    """

    def test_preview_events_do_not_enter_the_headline(self):
        cohort = _Cohort({"A": 0, "B": 0})
        safety = dag_safety([_record(preview_inferred={"A": {}, "B": {}})], cohort)

        assert safety["C-DAG-03_wrong_inference"]["verified"] == 0
        assert safety["wrong_inference_by_source"]["preview"]["verified"] == 2
        assert safety["wrong_inference_by_source"]["preview"]["events"] == 2

    def test_the_headline_source_is_never_preview(self):
        """"none" is the honest label for a configuration that concludes nothing."""
        assert _dominant_source(
            {
                "enforced": {"n": 0, "wrong": 0},
                "shadow": {"n": 0, "wrong": 0},
                "preview": {"n": 99, "wrong": 40},
            }
        ) == "none"


class TestR5CoverageIsTriState:
    """An arm with no coverage gate has not satisfied it; the gate did not run."""

    def test_a_missing_gate_is_excluded_rather_than_counted_as_a_pass(self):
        assert _rate_over_gated([None, None]) != 1.0
        assert _rate_over_gated([None, None]) != _rate_over_gated([True, True])
        assert _rate_over_gated([True, False]) == 0.5
        assert _rate_over_gated([True, False, None]) == 0.5

    def test_no_applicable_variables_is_not_perfect_compliance(self):
        import math

        assert math.isnan(_rate_over_gated([None, None, None]))


class TestStratifiedInference:
    """C-DAG-03d / C-DAG-03e, which need provenance the engine did not used to record."""

    def test_depth_and_edge_are_both_stratified(self):
        # `edge_path` runs from the node that was TESTED to the ancestor inferred from it,
        # so it walks edges backwards: a PREREQUISITE edge points parent -> child, and the
        # path visits child first. `tested` was answered; `near` and `far` were deduced.
        cohort = _Cohort({"near": 0, "far": 1})
        record = _record(
            inferred_mastered=["near", "far"],
            inferred_records={
                # one hop:  near -> tested
                "near": {"distance": 1, "edge_path": ["tested", "near"]},
                # two hops: far -> middle -> tested
                "far": {"distance": 2, "edge_path": ["tested", "middle", "far"]},
            },
        )
        safety = dag_safety([record], cohort)

        by_depth = safety["C-DAG-03d_wrong_inference_by_depth"]
        assert by_depth["1"]["verified"] == 1 and by_depth["1"]["events"] == 1
        assert by_depth["2"]["verified"] == 1 and by_depth["2"]["events"] == 0

        by_edge = safety["C-DAG-03e_wrong_inference_by_edge"]
        # Keyed parent->child, the direction the graph declares the edge in.
        assert by_edge["near->tested"]["events"] == 1
        # Every edge on a multi-hop path is blamed: one observation cannot say which of
        # them was wrong, and picking one would invent a precision the data lacks.
        assert by_edge["middle->tested"]["verified"] == 1
        assert by_edge["far->middle"]["verified"] == 1
        assert "tested->near" not in by_edge, "edge recorded child->parent"

    def test_a_record_without_provenance_is_skipped_not_miscounted(self):
        """Sessions written before provenance existed must not become depth-0 events."""
        cohort = _Cohort({"A": 0})
        safety = dag_safety([_record(inferred_mastered=["A"])], cohort)
        assert safety["C-DAG-03d_wrong_inference_by_depth"] == {}
        assert safety["C-DAG-03_wrong_inference"]["verified"] == 1


class TestFrozenEnvHoldsThePreconditions:
    """PRE-3 and PRE-5 are configuration, so they are asserted as configuration."""

    def test_exposure_top_k_is_not_the_forbidden_value(self):
        from evaluation.arms import FROZEN_ENV

        # docs/operations.md: "Production must not run at 1." Every exposure figure in
        # the prior study was measured there, and none of them transfer.
        assert FROZEN_ENV["CAT_EXPOSURE_TOP_K"] == "3"

    def test_the_band_probability_stop_is_conjunctive_in_the_harness(self):
        from evaluation.arms import FROZEN_ENV

        assert FROZEN_ENV["CAT_BAND_PROBABILITY_STOP_CONJUNCTIVE"] == "true"

    def test_the_blocking_threshold_is_pinned(self):
        """R3: the prior false-blocking figures were measured at 1, not 2."""
        from evaluation.arms import FROZEN_ENV

        assert FROZEN_ENV["GRAPH_MINIMUM_FAILURES_TO_BLOCK"] == "2"

    def test_frozen_keys_are_real_settings(self):
        """A frozen env var that no setting reads freezes nothing."""
        from app.config.settings import Settings
        from evaluation.arms import FROZEN_ENV

        known = {name.upper() for name in Settings.model_fields}
        unknown = {k for k in FROZEN_ENV if k not in known}
        assert unknown == set(), f"FROZEN_ENV sets keys no setting reads: {sorted(unknown)}"


class TestTruncatedRunsAreRecoverable:
    """A killed run leaves a partial final line, and killed runs are the normal case.

    Both the reader and the resume path used to mishandle it: the reader raised, and the
    resume counted the fragment as a record and then appended onto it, producing a line
    nothing could parse. The analysis would then die on exactly the runs that most needed
    reading.
    """

    def test_the_reader_tolerates_a_truncated_last_line(self, tmp_path):
        from evaluation.analyse_sweep import _read_jsonl

        path = tmp_path / "results.jsonl"
        path.write_text('{"a": 1}\n{"b": 2}\n{"c": 3', encoding="utf-8")
        assert _read_jsonl(path) == [{"a": 1}, {"b": 2}]

    def test_corruption_anywhere_else_still_raises(self, tmp_path):
        """Incomplete is recoverable; corrupt is not, and the two must not be conflated."""
        import json

        from evaluation.analyse_sweep import _read_jsonl

        path = tmp_path / "results.jsonl"
        path.write_text('{"a": 1}\nNOT JSON\n{"c": 3}\n', encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            _read_jsonl(path)

    def test_a_complete_file_reads_unchanged(self, tmp_path):
        from evaluation.analyse_sweep import _read_jsonl

        path = tmp_path / "results.jsonl"
        path.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
        assert _read_jsonl(path) == [{"a": 1}, {"b": 2}]
