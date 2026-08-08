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


class TestCohortIdentityIsNeverChosenByFilenameOrder:
    """Persona cohorts share a DGP label and must not overwrite each other in analysis."""

    @staticmethod
    def _write_cohort(path, *, persona: str) -> None:
        payload = {
            "dgp": "DGP-2",
            "seed": 42,
            "bank_id": "AIE",
            "mains": ["C1"],
            "simulees": [
                {
                    "simulee_id": "sim-1",
                    "family": "monotonic",
                    "stratum": 0,
                    "theta": {"C1": 0.0},
                    "persona": persona,
                }
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_an_exact_cohort_file_is_supported(self, tmp_path):
        from evaluation.analyse import load_cohorts

        path = tmp_path / "cohort_DGP-2_P01.json"
        self._write_cohort(path, persona="P01")

        assert load_cohorts(path)["DGP-2"].simulees[0].persona == "P01"

    def test_a_directory_with_two_personas_is_rejected(self, tmp_path):
        from evaluation.analyse import load_cohorts

        self._write_cohort(tmp_path / "cohort_DGP-2_P01.json", persona="P01")
        self._write_cohort(tmp_path / "cohort_DGP-2_P09.json", persona="P09")

        with pytest.raises(ValueError, match="exact cohort JSON"):
            load_cohorts(tmp_path)


class TestSeededSubsamplesDifferInMembership:
    """A seeded cell must draw DIFFERENT candidates, not the same ones reordered.

    Reshuffling one sample returns the same aggregate and estimates the same zero pure
    error, so order alone is not enough. The first implementation shifted the stride by a
    fraction of a step, which gives only `step` distinct samples — three of the four
    centre seeds landed in the same bucket and drew identical cohorts.
    """

    @staticmethod
    def _sample(population, limit, sample_seed, cohort_seed=42):
        import numpy as np

        simulees = list(population)
        rng_seed = int(sample_seed) if sample_seed else cohort_seed
        if limit and limit < len(simulees):
            step = len(simulees) / limit
            if sample_seed:
                picker = np.random.default_rng(rng_seed)
                chosen = []
                for i in range(limit):
                    low = int(i * step)
                    high = min(len(simulees), int((i + 1) * step))
                    chosen.append(simulees[int(picker.integers(low, max(high, low + 1)))])
                simulees = chosen
            else:
                simulees = [simulees[int(i * step)] for i in range(limit)]
        order = np.random.default_rng(rng_seed).permutation(len(simulees))
        return [simulees[int(i)] for i in order]

    def test_different_seeds_give_substantially_different_members(self):
        from evaluation.design import CENTRE_SAMPLE_SEEDS

        population = list(range(2000))
        samples = [set(self._sample(population, 200, s)) for s in CENTRE_SAMPLE_SEEDS]

        assert all(len(s) == 200 for s in samples)
        assert len({frozenset(s) for s in samples}) == len(samples), "two seeds drew the same set"
        for i, a in enumerate(samples):
            for b in samples[i + 1:]:
                # Independent stratified draws of 200 from 2000 overlap ~10% by chance.
                # Anything near 200 means the seeds are not separating the samples.
                assert len(a & b) < 60, f"overlap {len(a & b)} is far above chance"

    def test_seed_zero_is_the_deterministic_shared_sample(self):
        population = list(range(2000))
        assert self._sample(population, 200, 0) == self._sample(population, 200, 0)

    def test_stratification_survives_the_random_draw(self):
        """One candidate per stride block, blocks unchanged — the strata are preserved."""
        from evaluation.design import CENTRE_SAMPLE_SEEDS

        population = list(range(2000))
        for seed in CENTRE_SAMPLE_SEEDS:
            drawn = sorted(self._sample(population, 200, seed))
            # Exactly one member from each block of 10.
            blocks = {value // 10 for value in drawn}
            assert len(blocks) == 200, f"seed {seed} drew twice from one block"


class TestTheConfidenceGateHasNothingToDiscriminate:
    """The `C` factor is a modality filter on DGP-2, not a confidence gate.

    Confidence reaching `is_strong_success` takes exactly two values: 1.00 from MCQ and
    code, 0.90 from voice/open. `grader_error_sd` is 0 on DGP-2 — only DGP-3 carries
    grader noise — so there is no spread for a threshold to cut into.

    Consequences, both load-bearing for how the study reads:
      - no threshold below 0.9 changes anything, and one above 0.9 removes voice
        wholesale, so `C` is aliased onto the modality allowlist `M`;
      - P09, defined as a candidate who is confidently graded and wrong, has no gate to
        attack — its +0.15 moves 0.90 to 1.00 and clears what P01 already cleared.

    Pinned because it is a claim about the harness that would silently stop being true if
    the DGP or the voice responder changed, and the study's reading of `C` depends on it.
    """

    def test_dgp2_gives_every_simulee_zero_grader_error(self):
        from app.services.orchestrator import registry
        from evaluation.dgp import build_cohort, read_graph_prerequisites

        bank = registry.get_bank("AIE")
        edges = read_graph_prerequisites(registry.profile("AIE").graph_path)
        cohort = build_cohort(
            dgp="DGP-2", n=40, seed=5, bank=bank, bank_id="AIE",
            mains=bank.variables(), graph_prerequisites=edges,
        )
        assert {s.grader_error_sd for s in cohort.simulees} == {0.0}

    def test_voice_confidence_is_a_point_mass_without_grader_error(self):
        from app.services.orchestrator import registry
        from evaluation import responder
        from evaluation.dgp import Simulee

        voice = next(
            i for i in registry.get_bank("AIE").all_items() if i.modality in ("voice", "open")
        )
        main = voice.measures[0].variable.split(".")[0]

        def confidences(persona_id: str) -> set[float]:
            simulee = Simulee(
                simulee_id="sim-1", family="monotonic", stratum=4,
                theta={main: 0.0}, persona=persona_id, grader_error_sd=0.0,
            )
            plan = responder.plan_for(simulee, voice, main)
            graded = responder.voice_response(voice, plan, simulee)
            return {round(e.confidence, 6) for e in graded.evaluation.criterion_evidence}

        assert confidences("P01") == {0.90}
        # P09's +0.15 clips at 1.0 — the same side of any threshold P01 already cleared.
        assert confidences("P09") == {1.00}

    def test_no_confidence_lands_between_the_gate_and_the_point_mass(self):
        """A threshold in [0.0, 0.9) filters nothing; one in (0.9, 1.0] filters voice."""
        from app.services.competency_graph.config import PropagationConfig

        observed = {0.90, 1.00}
        default_gate = PropagationConfig().minimum_propagation_confidence
        assert all(c >= default_gate for c in observed), "the default gate rejects nothing"

        tightened = 0.95
        rejected = {c for c in observed if c < tightened}
        assert rejected == {0.90}, (
            "tightening C removes exactly the voice/open evidence and nothing else, which "
            "makes it a modality filter rather than a confidence filter"
        )


class TestTheShippedConfidenceGateIsNearlyInertEvenUnderGraderNoise:
    """DGP-3 gives confidence a real distribution. The gate still barely acts.

    Measured over real sessions, the confidence values reaching `is_strong_success`:

        DGP-2 / P01   2 distinct values, min 0.900   C=0.80 rejects 0.0%
        DGP-3 / P01  16 distinct values, min 0.776   C=0.80 rejects 0.8%
        DGP-3 / P09  12 distinct values, min 0.924   C=0.80 rejects 0.0%

    So grader noise does make the distribution non-degenerate — that half of Finding 4 is
    specific to DGP-2 — but the SHIPPED threshold of 0.80 sits below almost the whole
    distribution either way. It starts discriminating only at 0.95, and there it separates
    the personas as designed: P01 rejected 11.7% of the time against P09's 3.1%.

    Which is the useful form of the result. The gate that licenses propagation is not
    calibrated against the confidences it receives; it is set where nothing arrives.
    """

    def test_grader_noise_is_what_makes_the_distribution_non_degenerate(self):
        import numpy as np

        # The voice responder's formula: 0.90 + offset - |N(0, grader_error_sd)|.
        for sd, expected_distinct in ((0.0, 1), (0.10, 50)):
            rng = np.random.default_rng(3)
            values = np.clip(0.90 - np.abs(rng.normal(0.0, sd, 200)), 0.0, 1.0)
            assert len(set(np.round(values, 3))) >= expected_distinct or sd == 0.0

    def test_the_shipped_threshold_sits_below_the_distribution(self):
        """0.80 against a distribution whose 5th percentile is ~0.70 on the voice tail only.

        Voice is a minority of evidence and MCQ/code report 1.00, so the share of ALL
        evidence the shipped gate rejects stays near zero even with grader noise.
        """
        import numpy as np

        from app.services.competency_graph.config import PropagationConfig

        gate = PropagationConfig().minimum_propagation_confidence
        rng = np.random.default_rng(11)
        voice = np.clip(0.90 - np.abs(rng.normal(0.0, 0.10, 20000)), 0.0, 1.0)
        # ~12% of gate calls are voice; the rest are MCQ/code at 1.00.
        share_voice = 0.12
        rejected_overall = float((voice < gate).mean()) * share_voice

        assert rejected_overall < 0.05, (
            f"the shipped gate would reject {rejected_overall:.1%} of evidence; it is set "
            "where almost nothing arrives"
        )

    def test_a_tightened_gate_separates_the_personas(self):
        """P09's premise is only visible at 0.95, not at the shipped 0.80."""
        import numpy as np

        rng = np.random.default_rng(5)
        p01 = np.clip(0.90 - np.abs(rng.normal(0.0, 0.10, 20000)), 0.0, 1.0)
        p09 = np.clip(0.90 + 0.15 - np.abs(rng.normal(0.0, 0.10, 20000)), 0.0, 1.0)

        assert float((p01 < 0.80).mean()) > float((p09 < 0.80).mean())
        # And the separation is far larger at the tightened threshold.
        assert float((p01 < 0.95).mean()) > 3 * float((p09 < 0.95).mean())
