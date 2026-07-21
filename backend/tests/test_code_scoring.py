"""Guards on the scoring seam.

Every test here corresponds to a way a wrong number reached — or nearly reached — a real
candidate. They are regression tests before they are unit tests.
"""

from __future__ import annotations

import pytest

from app.services.code_adaptive import scoring
from app.services.code_adaptive.scoring import CriterionScore
from app.services.code_adaptive.static_analysis import analyse
from app.services.code_adaptive.weights import WeightProfile

QUESTION = {
    "rubric_criteria": [
        {"criterion_id": "functional_correctness", "maximum_score": 8},
        {"criterion_id": "edge_case_handling", "maximum_score": 5},
        {"criterion_id": "algorithm_choice", "maximum_score": 4},
        {"criterion_id": "code_quality", "maximum_score": 3},
    ]
}


class TestAnchoring:
    """The model may never assert correctness that execution disproves."""

    def test_model_cannot_score_above_the_test_result(self):
        score = scoring.combine(
            "functional_correctness", tests=0.25, static=None, llm=1.0, approach="C"
        )
        assert score.score == pytest.approx(0.25)
        assert "LLM_OBJECTIVE_CONFLICT" in score.conflict_flag

    def test_the_clamp_survives_a_tuned_split(self):
        """Even handing the model 90% of the criterion must not let it overrule execution."""
        profile = WeightProfile.from_preset("B", scoring.SOURCE_WEIGHTS).with_llm_shares(
            {"functional_correctness": 0.9}
        )
        score = scoring.combine(
            "functional_correctness", tests=0.25, static=None, llm=1.0, profile=profile
        )
        assert score.score == pytest.approx(0.25)

    def test_a_model_scoring_below_the_tests_is_left_alone(self):
        """Only overclaiming is corrected. A harsher reading is a legitimate judgement."""
        score = scoring.combine(
            "functional_correctness", tests=1.0, static=None, llm=0.4, approach="C"
        )
        assert score.score < 1.0
        assert score.conflict_flag == ""


class TestUnassessable:
    def test_no_source_means_unscored_not_zero_and_not_full_credit(self):
        """Absence of evidence is neither a pass nor a fail.

        Falling back to whichever source happened to exist once let a stub score 1.00 on
        algorithm choice and code quality; scoring 0.0 instead would be the mirror error —
        a claim the candidate demonstrated nothing when the truth is that nobody looked.
        """
        score = scoring.combine("code_quality", tests=None, static=None, llm=None, approach="B")
        assert score.score is None
        assert score.conflict_flag == "NOT_ASSESSED"

    def test_unscored_criteria_redistribute_their_weight(self):
        scores = [
            CriterionScore("functional_correctness", None, {}),
            CriterionScore("code_quality", 0.8, {}),
        ]
        assert scoring.overall_score(scores, QUESTION) == pytest.approx(0.8)

    def test_nothing_assessed_at_all_is_none(self):
        scores = [CriterionScore("functional_correctness", None, {})]
        assert scoring.overall_score(scores, QUESTION) is None


class TestCriterionWeights:
    """maximum_score was declared by every question and read by nothing.

    The overall was a plain mean, so functional correctness could never be worth more than
    a quarter however the bank was authored — a submission passing 1 of 8 tests scored 0.61
    in a live session.
    """

    def test_weights_come_from_the_question(self):
        weights = scoring.criterion_weights(QUESTION)
        assert weights["functional_correctness"] == pytest.approx(0.40)
        assert weights["code_quality"] == pytest.approx(0.15)
        assert sum(weights.values()) == pytest.approx(1.0)

    def test_a_failing_submission_scores_below_the_unweighted_mean(self):
        scores = [
            CriterionScore("functional_correctness", 0.125, {}),
            CriterionScore("edge_case_handling", 1.0, {}),
            CriterionScore("algorithm_choice", 0.85, {}),
            CriterionScore("code_quality", 0.85, {}),
        ]
        weighted = scoring.overall_score(scores, QUESTION)
        unweighted = sum(c.score for c in scores) / len(scores)
        assert weighted < unweighted


class TestIntegrityCap:
    """Averaging cannot express 'this finding invalidates the rest'."""

    def test_hardcoded_output_is_capped(self):
        signals = analyse("def f(xs):\n    return [1, 2, 3]\n", "f")
        cap, reason = scoring.integrity_cap(signals)
        assert cap == pytest.approx(0.25)
        assert "HARDCODED_OUTPUT" in reason

    def test_an_empty_body_caps_lower_than_a_hardcoder(self):
        """Writing nothing is worth less than writing the wrong thing."""
        stub = analyse("def f(xs):\n    pass\n", "f")
        assert scoring.integrity_cap(stub)[0] == pytest.approx(0.0)

    def test_a_working_solution_is_not_capped(self):
        signals = analyse(
            "def two_sum(numbers, target):\n"
            "    seen = {}\n"
            "    for j, n in enumerate(numbers):\n"
            "        if target - n in seen:\n"
            "            return [seen[target - n], j]\n"
            "        seen[n] = j\n"
            "    return []\n",
            "two_sum",
        )
        assert scoring.integrity_cap(signals)[0] is None

    def test_a_branching_predicate_is_not_hardcoding(self):
        """A predicate returns literals by definition — the answer is which branch it reaches.

        The detector previously asked whether PARAMETER NAMES appeared in the return, which
        every correct is_something() fails. It capped three verified-correct submissions.
        """
        signals = analyse(
            "def is_ok(text):\n"
            "    if not text:\n"
            "        return False\n"
            "    return True\n",
            "is_ok",
        )
        assert signals.hard_coded_output_suspected is False
        assert scoring.integrity_cap(signals)[0] is None

    def test_a_constant_return_with_no_branching_is_still_caught(self):
        """The predicate exemption must not become an escape hatch for a stub."""
        signals = analyse("def is_ok(text):\n    return True\n", "is_ok")
        assert signals.hard_coded_output_suspected is True

    def test_the_cap_reaches_criterion_scores_not_just_the_report(self):
        """Competency evidence is projected from criteria, so capping the headline alone
        would leave the learner model updating as though competence had been shown."""
        signals = analyse("def f(xs):\n    return [1, 2, 3]\n", "f")
        scores = [CriterionScore("functional_correctness", 1.0, {}), CriterionScore("code_quality", 0.9, {})]
        capped, reason = scoring.apply_integrity_cap(scores, signals)
        assert all(c.score <= 0.25 for c in capped)
        assert reason


class TestConfidence:
    def test_low_confidence_halves_the_model_rather_than_dropping_it(self):
        """Dropping it would silently change which sources scored the criterion."""
        confident = scoring.combine(
            "code_quality", tests=None, static=0.2, llm=1.0, llm_confidence=0.95, approach="B"
        )
        hedged = scoring.combine(
            "code_quality", tests=None, static=0.2, llm=1.0, llm_confidence=0.1, approach="B"
        )
        assert hedged.score < confident.score
        assert "llm" in hedged.sources_used
