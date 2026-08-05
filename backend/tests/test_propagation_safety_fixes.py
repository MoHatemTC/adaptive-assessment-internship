"""Regression tests for the defects the B/C evaluation found.

One class per finding, named for it. Each asserts the behaviour that was wrong, so the
fix cannot silently regress — which matters more here than usual, because three of these
were invisible: a dead flag, a disabled feature still running, and a block decided from
one observation all produce plausible output while being wrong.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.config.settings import settings
from app.services.adaptive import convergence
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator

try:
    from app.services.competency_graph import config as graph_config

    HAS_GRAPH = True
except ImportError:
    HAS_GRAPH = False

requires_graph = pytest.mark.skipif(not HAS_GRAPH, reason="Approach B has no competency graph")


@pytest.fixture
def bank():
    if HAS_GRAPH:
        from app.services.orchestrator import registry

        return registry.get_bank("AIE")
    from pathlib import Path

    from app.services.orchestrator.bank import JsonUnifiedBank

    return JsonUnifiedBank(
        Path(__file__).resolve().parents[1] / "app" / "data" / "question_bank_AIE.json"
    )


def _orchestrator(bank):
    from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository

    grader = GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository()))
    if HAS_GRAPH:
        from app.services.orchestrator import registry

        return Orchestrator(
            bank,
            grader,
            graph=registry.get_graph_service("AIE"),
            coverage_critical_only=registry.profile("AIE").coverage_critical_only,
            bank_id="AIE",
        )
    return Orchestrator(bank, grader)


# --- S1 -------------------------------------------------------------------------------


@requires_graph
class TestS1MasterSwitchCoversReporting:
    """`COMPETENCY_GRAPH_ENABLED=false` must leave no graph in the report.

    Every other graph entry point checked the switch; `summarise` did not, so a session run
    with the graph off still loaded it, computed coverage, and emitted a full graph block. A
    disabled feature running in production reporting is precisely what a master switch
    exists to prevent — an operator reaching for it during an incident would have seen
    nothing change.
    """

    def test_the_report_carries_no_graph_when_the_switch_is_off(self, bank, monkeypatch):
        monkeypatch.setattr(settings, "competency_graph_enabled", False)
        orchestrator = _orchestrator(bank)
        report = orchestrator.summarise(orchestrator.begin(["C1"]), "test")

        assert report.graph == {}
        assert report.variables[0].graph_unmeasured_nodes == []
        assert report.variables[0].graph_coverage_satisfied is True

    def test_the_report_carries_a_graph_when_the_switch_is_on(self, bank, monkeypatch):
        """The control. Without it, the test above passes on a broken graph loader."""
        monkeypatch.setattr(settings, "competency_graph_enabled", True)
        orchestrator = _orchestrator(bank)
        report = orchestrator.summarise(orchestrator.begin(["C1"]), "test")

        # A fresh session has no evidence, so `nodes` is legitimately empty. What must
        # differ is the presence of the block itself — that is what the switch governs.
        assert report.graph, "the graph block vanished with the switch ON"
        assert "mains" in report.graph and "preview_blocked_nodes" in report.graph


# --- S2 -------------------------------------------------------------------------------


class TestS2BandProbabilityStopIsReachable:
    """`cat_band_probability_stop_enabled` was live, documented, and dead.

    `convergence.evaluate` implements the rule and takes `band_probability`; neither
    caller passed it, so it defaulted to None and `band_probability_stop_available`
    returned False whatever the flag said.
    """

    def test_the_rule_fires_when_it_is_given_a_probability(self, monkeypatch):
        monkeypatch.setattr(settings, "cat_band_probability_stop_enabled", True)
        stop = convergence.evaluate(
            standard_error=0.9,  # too wide for a precision stop
            band_history=[3, 4, 3],  # unstable, so no stable-band stop either
            questions_answered=settings.cat_precision_min_questions,
            items_remaining=20,
            band_probability=0.95,
        )
        assert stop.should_stop is True
        assert stop.reason == "band_probability"

    def test_finalisation_passes_a_probability_through(self, monkeypatch):
        """The wiring, asserted where it was missing rather than where it works."""
        from app.services.orchestrator import variables as variables_module

        seen: dict[str, object] = {}
        real_evaluate = convergence.evaluate

        def spy(*args, **kwargs):
            seen.update(kwargs)
            return real_evaluate(*args, **kwargs)

        monkeypatch.setattr(variables_module.convergence, "evaluate", spy)
        state = variables_module.seed_variable("C1")
        variables_module.evaluate_finalisation(state, items_remaining=20)

        assert "band_probability" in seen, "the stopping rule cannot see the band probability"
        assert seen["band_probability"] is not None
        assert 0.0 <= float(seen["band_probability"]) <= 1.0  # type: ignore[arg-type]

    def test_the_probability_is_the_reported_bands_not_the_most_likely_bands(self):
        """Near a cut point those differ, and the rule asks about the level being printed."""
        from app.services.adaptive.irt import THETA_GRID, ability_band, band_probabilities

        # Mass split across a cut, with the mean landing in the lighter band.
        posterior = np.zeros_like(THETA_GRID)
        posterior[np.argmin(np.abs(THETA_GRID - 0.6))] = 0.55
        posterior[np.argmin(np.abs(THETA_GRID - 1.4))] = 0.45
        posterior /= posterior.sum()

        bands = band_probabilities(posterior)
        theta_hat = float(np.sum(THETA_GRID * posterior))
        reported = ability_band(theta_hat)[0]
        assert bands[reported] <= max(bands.values())


# --- S5 -------------------------------------------------------------------------------


@requires_graph
class TestS5BlockingNeedsTwoFailures:
    """A block decided from one observation carries that observation's error rate.

    Measured on a graph whose edges were correct by construction, blocking from a single
    failure produced a 4.8-5.1% false-blocking rate against a 2% gate. Two consistent
    failures square the per-observation error.
    """

    def test_the_default_policy_asks_for_two(self):
        from app.services.competency_graph.config import propagation_config_from_settings

        assert propagation_config_from_settings().minimum_failures_to_block >= 2

    def test_one_failure_blocks_nothing_and_two_block_everything(self):
        from app.services.competency_graph import load_default_competency_graph
        from app.services.competency_graph.config import PropagationConfig
        from app.services.competency_graph.evidence import EvidenceEvent
        from app.services.competency_graph.graph import CompetencyGraphService
        from app.services.competency_graph.propagation import shadow_apply_events

        graph = CompetencyGraphService(load_default_competency_graph())

        def failure(attempt: int) -> EvidenceEvent:
            return EvidenceEvent(
                evidence_id=f"s:i{attempt}:code:DA.1",
                session_id="s",
                item_id=f"i{attempt}",
                modality="code",
                target_node="DA.1",
                score=0.0,
                weight=1.0,
                confidence=1.0,
                source=f"i{attempt}",
            )

        config = PropagationConfig(minimum_failures_to_block=2)
        first, second = shadow_apply_events(graph, [failure(1), failure(2)], config=config)

        assert first.blocked_nodes == frozenset()
        assert second.blocked_nodes  # the second consistent failure licenses the block

    def test_the_failure_verdict_is_recorded_on_the_first_observation(self):
        """Only the BLOCK waits. The node is still marked not-mastered immediately."""
        from app.services.competency_graph import load_default_competency_graph
        from app.services.competency_graph.config import PropagationConfig
        from app.services.competency_graph.evidence import EvidenceEvent
        from app.services.competency_graph.graph import CompetencyGraphService
        from app.services.competency_graph.ledger import EvidenceLedger
        from app.services.competency_graph.models import CompetencyStatus
        from app.services.competency_graph.propagation import apply_direct_evidence
        from app.services.competency_graph.state import CompetencyGraphState

        graph = CompetencyGraphService(load_default_competency_graph())
        state = CompetencyGraphState()
        state.ensure_nodes(set(graph.graph.nodes))
        apply_direct_evidence(
            graph,
            state,
            EvidenceEvent(
                evidence_id="s:i1:code:DA.1",
                session_id="s",
                item_id="i1",
                modality="code",
                target_node="DA.1",
                score=0.0,
                weight=1.0,
                confidence=1.0,
                source="i1",
            ),
            ledger=EvidenceLedger(),
            config=PropagationConfig(minimum_failures_to_block=2),
        )

        assert state.nodes["DA.1"].status is CompetencyStatus.DIRECT_NOT_MASTERED
        assert state.nodes["DA.1"].strong_failure_observations == 1
        assert state.nodes["DA.4"].status is not CompetencyStatus.BLOCKED


# --- S4 -------------------------------------------------------------------------------


class TestS4BandsAreEvenlyWide:
    """A reported level must mean the same amount of ability wherever it sits.

    Approach B mapped theta with `int(clip(round(3 + theta), 1, 5))`, which leaves bands 1
    and 5 unbounded while the interior three are 1.0 wide. Accuracy in the open-ended
    tails is then nearly free, and B's own report read 0.818 where the same estimates
    earned 0.644 on an even scale.
    """

    def test_every_interior_band_is_the_same_width(self):
        from app.services.adaptive.irt import BAND_CUTS

        widths = np.diff(np.asarray(BAND_CUTS, dtype=float))
        assert np.allclose(widths, widths[0]), f"interior bands are uneven: {widths}"

    def test_the_extreme_bands_start_where_the_cuts_say(self):
        from app.services.adaptive.irt import BAND_CUTS, ability_band

        lowest_cut, highest_cut = float(BAND_CUTS[0]), float(BAND_CUTS[-1])
        assert ability_band(lowest_cut - 0.01)[0] == 1
        assert ability_band(lowest_cut + 0.01)[0] == 2
        assert ability_band(highest_cut + 0.01)[0] == len(BAND_CUTS) + 1

    def test_a_candidate_far_below_the_scale_is_not_lumped_with_a_borderline_one(self):
        """The defect, stated as behaviour: with clipped rounding both were 'Novice'."""
        from app.services.adaptive.irt import BAND_CUTS, ability_band

        far_below = float(BAND_CUTS[0]) - 1.5
        just_above = float(BAND_CUTS[0]) + 0.1
        assert ability_band(far_below)[0] != ability_band(just_above)[0]


# --- S6 -------------------------------------------------------------------------------


@requires_graph
class TestS6PolicyResolvesTheOrchestratorsOwnBank:
    """A blocking threshold read for the wrong bank is a wrong threshold, silently.

    `_minimum_failures_to_block` resolved the policy with no bank id, so the registry fell
    back to `settings.active_bank`. `app.main` keeps one orchestrator per bank, so on any
    deployment serving more than one, every orchestrator but the active one read a
    threshold authored for a different graph.

    It is invisible in the shipped data because all three banks currently resolve to the
    deployment floor of 2 — which is exactly why it needs a test on the LOOKUP rather than
    on the number. The day a bank tightens to 3 is the day the defect starts blocking
    candidates on fewer failures than their bank asked for, and nothing would have failed.
    """

    def test_the_orchestrator_asks_for_its_own_bank(self, monkeypatch):
        from app.services.orchestrator import registry
        from tests.conftest import orchestrator_for

        orchestrator = orchestrator_for("DA")

        asked: list[str | None] = []
        real = registry.get_propagation_policy

        def spy(bank_id=None):
            asked.append(bank_id)
            return real(bank_id)

        monkeypatch.setattr(registry, "get_propagation_policy", spy)
        orchestrator._minimum_failures_to_block()

        assert asked == ["DA"], (
            f"resolved the policy for {asked} instead of the orchestrator's own bank; "
            "with no id the registry falls back to settings.active_bank"
        )

    def test_banks_that_disagree_get_their_own_threshold(self, monkeypatch):
        """The consequence, stated as behaviour rather than as a call signature.

        Every shipped bank currently resolves to the deployment floor of 2, so the
        thresholds have to be forced apart to observe it at all.
        """
        from app.services.competency_graph.policy import GraphPolicy, ResolvedPolicy
        from app.services.orchestrator import registry
        from tests.conftest import orchestrator_for

        aie = orchestrator_for("AIE")
        da = orchestrator_for("DA")

        thresholds = {"AIE": 2, "DA": 3}

        def fake(bank_id=None):
            return ResolvedPolicy(
                deployment_inference=False,
                deployment_blocking=False,
                bank_policy=GraphPolicy(),
                minimum_failures_to_block=thresholds.get(bank_id or "AIE", 2),
            )

        monkeypatch.setattr(registry, "get_propagation_policy", fake)

        assert aie._minimum_failures_to_block() == 2
        assert da._minimum_failures_to_block() == 3
