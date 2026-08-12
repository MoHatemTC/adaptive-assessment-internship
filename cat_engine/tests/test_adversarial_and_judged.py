"""ADV-1..ADV-5 and the judged layer's locks.

The judged tests here make NO model calls. They check the deterministic pre-filters, the
reliability arithmetic, and — most importantly — that nothing in the judged package can be
invoked by an ordinary `pytest` run.
"""

from __future__ import annotations

import pytest

from cat_engine.evaluation.adversarial import (
    adv3_self_report_cannot_infer,
    adv4_evidence_replay,
    adv5_static_bank_probe,
    adv5_structural_graph_probe,
)


@pytest.fixture(scope="module")
def bank():
    from cat_engine.engine.services.orchestrator import registry

    return registry.get_bank("AIE")


@pytest.fixture(scope="module")
def graph():
    from cat_engine.engine.services.orchestrator import registry

    return registry.get_graph_service("AIE")


class TestADV3SelfReportCannotBecomeEvidence:
    def test_the_isolation_is_structural_not_conditional(self):
        verdict = adv3_self_report_cannot_infer()
        assert verdict["inferred_signal_carries_no_score_or_weight"]
        assert verdict["evidence_event_requires_a_graded_score"]
        assert verdict["evidence_event_has_no_inferred_provenance_fields"]


class TestADV4EvidenceReplay:
    def test_a_clean_run_has_no_duplicates(self):
        records = [{"graph": {"direct_evidence_id_count": 10, "unique_direct_evidence_ids": 10}}]
        assert adv4_evidence_replay(records)["passes"]

    def test_a_replayed_event_is_counted(self):
        records = [{"graph": {"direct_evidence_id_count": 12, "unique_direct_evidence_ids": 10}}]
        result = adv4_evidence_replay(records)
        assert result["duplicates"] == 2
        assert not result["passes"]


class TestADV5CorroborationFarming:
    """The two probes point in opposite directions, and both must be reported."""

    def test_the_bank_probe_finds_the_clone_groups(self, bank):
        probe = adv5_static_bank_probe(bank)
        assert probe["max_farmable_k_overall"] >= 1
        assert probe["nodes"] > 0

    def test_the_graph_probe_says_whether_k_is_satisfiable_at_all(self, graph):
        probe = adv5_structural_graph_probe(graph)
        assert probe["prerequisite_edges"] > 0
        # On a graph where every parent has one child, K>=2 cannot be met at depth 1 by
        # any candidate, however they answer.
        if probe["max_direct_children"] < 2:
            assert probe["k_satisfiable_at_depth_1"]["2"] is False

    def test_the_two_probes_answer_different_questions(self, bank, graph):
        """One asks if corroboration is too easy; the other if it is possible.

        Both failures make K useless and they point opposite ways, so reporting only one
        would miss half the ways the knob can be inoperative.
        """
        farmable = adv5_static_bank_probe(bank)["max_farmable_k_overall"]
        satisfiable = adv5_structural_graph_probe(graph)["k_satisfiable_at_depth_1"]["2"]
        assert isinstance(farmable, int)
        assert isinstance(satisfiable, bool)


def _repository_root():
    """The directory holding `pytest.ini`, found by walking up from this file.

    Was `Path(judged.__file__).parents[2]` — a fixed hop count, which is exactly the thing
    that broke when the tree moved under `cat_engine/` and the configuration moved to the
    repository root. These are the locks that stop the suite making a billed model call,
    so a lock that silently starts checking the wrong directory is worse than no lock:
    every assertion below would still pass against a file that was not the one in force.

    Walking up for the marker states what is actually meant — "the configuration pytest is
    reading" — and cannot drift with the layout.
    """
    from pathlib import Path

    here = Path(__file__).resolve()
    for candidate in here.parents:
        if (candidate / "pytest.ini").is_file():
            return candidate
    raise AssertionError(f"no pytest.ini above {here}")


class TestJudgedLayerCannotRunByAccident:
    """Five locks. The test suite must never make a billed call."""

    def test_the_judged_package_is_outside_testpaths(self):
        root = _repository_root()
        config = (root / "pytest.ini").read_text(encoding="utf-8")
        testpaths = config.split("testpaths")[1].split("\n")[0]
        # The suite collects the tests directory and nothing else. `evaluation/` holds the
        # judged layer, so its absence here is what keeps that layer uncollected.
        assert "tests" in testpaths
        assert "evaluation" not in testpaths

    def test_deepeval_is_not_a_runtime_dependency(self):
        root = _repository_root()
        assert "deepeval" not in (root / "requirements.txt").read_text(encoding="utf-8")

    def test_the_modules_import_without_deepeval_installed(self):
        """Importing must never be what triggers a call, or lock 4 is decorative."""
        from cat_engine.evaluation.judged import goldens, metrics, reliability

        assert metrics.DEEPEVAL_PIN
        assert goldens.CANARIES
        assert reliability.MAX_FLIP_RATE <= 0.10

    def test_pytest_ini_excludes_the_deepeval_marker(self):
        root = _repository_root()
        assert 'not deepeval' in (root / "pytest.ini").read_text(encoding="utf-8")


class TestG01Prefilter:
    """The half of G-01 that needs no judge — and catches the failure that matters."""

    def test_a_fabricated_item_citation_is_caught_deterministically(self):
        from cat_engine.evaluation.judged.metrics import g01_prefilter

        verdict = g01_prefilter(
            "As shown by item AIE-9999.",
            {"served_item_ids": ["AIE-0001"], "cited_item_ids": ["AIE-9999"]},
        )
        assert not verdict.passed
        assert "never administered" in verdict.failures[0]

    def test_an_unlabelled_inference_presented_as_demonstrated_is_caught(self):
        """Propagation's characteristic failure, decided without a model."""
        from cat_engine.evaluation.judged.metrics import g01_prefilter

        verdict = g01_prefilter(
            "The candidate demonstrated C6.1.",
            {
                "served_item_ids": [],
                "inferred_nodes": ["C6.1"],
                "nodes_labelled_inferred": [],
                "nodes_claimed_demonstrated": ["C6.1"],
            },
        )
        assert not verdict.passed

    def test_a_correctly_labelled_inference_passes(self):
        from cat_engine.evaluation.judged.metrics import g01_prefilter

        verdict = g01_prefilter(
            "C6.1 is INFERRED from C6.2 and was not directly assessed.",
            {
                "served_item_ids": [],
                "inferred_nodes": ["C6.1"],
                "nodes_labelled_inferred": ["C6.1"],
                "nodes_claimed_demonstrated": ["C6.1"],
            },
        )
        assert verdict.passed


class TestJudgeReliabilityArithmetic:
    def test_a_straddling_case_is_unusable(self):
        from cat_engine.evaluation.judged.reliability import summarise_runs

        result = summarise_runs([0.96, 0.94, 0.96, 0.96, 0.94], 0.95)
        assert result["straddles_threshold"]
        assert not result["usable"]

    def test_the_median_resists_a_single_outlier(self):
        """A mean would move 0.16 for one aberrant judgement; the median moves nothing."""
        from cat_engine.evaluation.judged.reliability import summarise_runs

        assert summarise_runs([0.9, 0.9, 0.9, 0.9, 0.1], 0.5)["median"] == 0.9

    def test_a_missed_canary_invalidates_the_run(self):
        from cat_engine.evaluation.judged.reliability import canary_verdict

        assert not canary_verdict(
            [{"canary_id": "x", "expected": "fail", "verdict": True}]
        )["valid"]
        assert canary_verdict(
            [{"canary_id": "x", "expected": "fail", "verdict": False}]
        )["valid"]

    def test_kappa_is_reported_as_not_computable_with_the_reason(self):
        """Section 6.4's bar is unmeasurable here, and the output must say so itself."""
        from cat_engine.evaluation.judged.reliability import reliability_report

        report = reliability_report({}, {"valid": True, "missed": []})
        assert report["cohens_kappa"] is None
        assert "two rater populations" in report["kappa_note"]
        assert "descriptive" in report["kappa_note"]
