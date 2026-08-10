"""Release-spec invariants for the C-shipped assessment configuration.

These tests are intentionally outside ``backend/pytest.ini``'s normal ``testpaths``.
They evaluate the release contract without changing production behavior. A failure is a
release finding, not a request to weaken the assertion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# The request models moved from `app.main` — which no longer exists — into the shared wire
# contracts when the CAT API became a service. The path insert keeps these invariants
# runnable from a checkout that has not pip-installed the packages: they are release
# evidence, and evidence that depends on install state is weaker evidence.
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "services" / "contracts"))

from adaptive_contracts import AnswerRequest, CreateAssessmentRequest  # noqa: E402

from app.config.settings import settings  # noqa: E402
from app.services.adaptive import convergence  # noqa: E402
from app.services.adaptive.irt import posterior_update, uniform_prior  # noqa: E402
from app.services.competency_graph.coverage import (  # noqa: E402
    coverage_allows_convergence,
)
from app.services.competency_graph.inference import InferredNodeSignal  # noqa: E402
from app.services.orchestrator.competency import rollup_outcomes  # noqa: E402
from app.services.orchestrator.outcome import (  # noqa: E402
    GradedOutcome,
    graded_posterior_update,
)
from app.services.orchestrator.variables import (  # noqa: E402
    apply_outcome,
    seed_variable,
)


def test_inv_01_zero_weight_is_a_complete_measurement_noop() -> None:
    before = seed_variable("C1", self_rating=3)
    for score in (0.0, 0.2, 0.5, 0.9, 1.0):
        after = apply_outcome(
            before,
            GradedOutcome("C1", score=score, weight=0.0),
            1.5,
            0.0,
            0.25,
            "AIE-zero-weight",
        )
        assert after.model_dump(mode="json") == before.model_dump(mode="json")


@pytest.mark.parametrize("correct", [False, True])
def test_inv_02_binary_update_is_bit_identical(correct: bool) -> None:
    prior = uniform_prior()
    canonical = posterior_update(prior, 1.2, 0.4, 0.25, correct)
    generic = graded_posterior_update(
        prior, 1.2, 0.4, 0.25, float(correct), weight=1.0
    )
    assert np.array_equal(canonical[0], generic[0])
    assert canonical[1:] == generic[1:]


def test_inv_03_graph_signals_cannot_become_psychometric_evidence() -> None:
    signal = InferredNodeSignal(
        node="C1.1",
        source_node="C1.2",
        distance=1,
        strength=0.5,
        source_evidence_id="evidence",
        modality="code",
    )
    assert not hasattr(signal, "score")
    assert not hasattr(signal, "weight")
    assert settings.graph_upward_inference_enabled is False
    assert settings.graph_descendant_blocking_enabled is False
    assert settings.graph_filtering_enabled is False
    assert settings.graph_utility_enabled is False


def test_inv_04_one_response_rolls_up_to_one_observation() -> None:
    rolled = rollup_outcomes(
        [
            {"variable": "C1.1", "score": 0.8, "weight": 0.7},
            {"variable": "C1.2", "score": 0.6, "weight": 0.6},
        ],
        {"C1"},
    )
    assert len(rolled) == 1
    state = apply_outcome(seed_variable("C1"), rolled[0], 1.2, 0.0, 0.0, "shared")
    assert state.observations == 1


def test_inv_05_inferred_nodes_do_not_satisfy_direct_coverage(aie_graph) -> None:
    required = sorted(aie_graph.critical_nodes_for_main("C1"))
    assert required
    # Inferred nodes are deliberately not an argument to the coverage function.
    assert not coverage_allows_convergence(
        aie_graph,
        "C1",
        measured=[],
        mastered=[],
        not_mastered=[],
        critical_only=True,
    )


def test_inv_06_modality_blueprint_matches_release_contract() -> None:
    assert settings.modality_minimums() == {"code": 1, "voice": 1}


def test_inv_07_precision_requires_six_observations() -> None:
    early = convergence.evaluate(0.1, [3, 3, 3], 5, 100, band_probability=1.0)
    ready = convergence.evaluate(0.1, [3, 3, 3], 6, 100, band_probability=1.0)
    assert not early.should_stop
    assert ready.should_stop and ready.converged


def test_inv_08_question_cap_is_twelve_and_exhaustion_is_not_convergence() -> None:
    assert settings.cat_max_questions == 12
    decision = convergence.evaluate(2.0, [3] * 12, 12, 100)
    assert decision.should_stop
    assert decision.reason == "question_budget"
    assert not decision.converged


def test_inv_09_normal_convergence_is_fully_conjunctive() -> None:
    """Release spec: precision alone must not certify an uncertain reported band."""
    decision = convergence.evaluate(
        settings.cat_se_target,
        [3, 3, 3],
        settings.cat_precision_min_questions,
        100,
        difficulty_corroborated=True,
        band_probability=0.0,
    )
    assert not decision.should_stop, (
        "precision convergence fired without satisfying the P(band) threshold"
    )


def test_inv_10_session_rng_is_not_constant_by_default() -> None:
    """A caller that omits a seed must not put every candidate on RNG(0).

    The answer-level seed is now GONE rather than merely defaulted. The monolith kept it
    "for wire compatibility" and logged a warning that it was ignored — a field a client
    can send, that does nothing, and whose name says it controls exposure. Removing it is
    the stronger form of this invariant: it cannot be misused because it cannot be sent.
    """
    assert CreateAssessmentRequest.model_fields["seed"].default is None
    assert "seed" not in AnswerRequest.model_fields


@pytest.fixture
def aie_graph():
    from app.services.orchestrator import registry

    graph = registry.get_graph_service("AIE")
    assert graph is not None
    return graph
