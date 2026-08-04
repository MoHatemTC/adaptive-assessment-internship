"""The harness's own tests. A measuring instrument needs one more than the thing measured.

Every number in the B/C report is produced by `backend/evaluation`. If the response
generator is order-dependent the pairing is broken, if the DGP does not respect its own
prerequisite structure the DAG arms measure nothing, and if the statistics are wrong the
gates are decorative. None of that would show up as an error — it would show up as a
plausible result.
"""

from __future__ import annotations

import numpy as np
import pytest

from evaluation import responder, stats
from evaluation.bands import COMMON_CUTS, band_of, band_probabilities, cuts_for
from evaluation.clock import VirtualClock
from evaluation.dgp import DGP_ARMS, build_cohort, read_graph_prerequisites

try:
    from app.services.orchestrator import registry

    HAS_REGISTRY = True
except ImportError:
    HAS_REGISTRY = False

requires_registry = pytest.mark.skipif(not HAS_REGISTRY, reason="Approach B has no bank registry")


@pytest.fixture(scope="module")
def bank():
    if HAS_REGISTRY:
        return registry.get_bank("AIE")
    from pathlib import Path

    from app.services.orchestrator.bank import JsonUnifiedBank

    root = Path(__file__).resolve().parents[1]
    return JsonUnifiedBank(root / "app" / "data" / "question_bank_AIE.json")


@pytest.fixture(scope="module")
def graph_edges():
    if not HAS_REGISTRY:
        return []
    profile = registry.profile("AIE")
    return read_graph_prerequisites(profile.graph_path)


@pytest.fixture(scope="module")
def cohort(bank, graph_edges):
    return build_cohort(
        dgp="DGP-1",
        n=200,
        seed=7,
        bank=bank,
        bank_id="AIE",
        mains=bank.variables(),
        graph_prerequisites=graph_edges,
    )


# --- the pairing rule ---------------------------------------------------------------


class TestResponsePurity:
    """A response depends on (simulee, item) and on nothing else."""

    def test_the_same_pair_gives_the_same_plan_however_often_it_is_asked(self, cohort, bank):
        simulee = cohort.simulees[17]
        items = bank.shortlist("C1", exclude=set())[:5]
        first = [responder.plan_for(simulee, i, "C1") for i in items]
        # Ask again in the opposite order, interleaved with other simulees' draws.
        for other in cohort.simulees[:3]:
            for item in items:
                responder.plan_for(other, item, "C1")
        second = [responder.plan_for(simulee, i, "C1") for i in reversed(items)]
        assert first == list(reversed(second))

    def test_two_simulees_at_the_same_ability_are_not_the_same_person(self, cohort, bank):
        item = bank.shortlist("C1", exclude=set())[0]
        a, b = cohort.simulees[0], cohort.simulees[1]
        # Same draw would mean the seed ignored the simulee id.
        plans = {
            responder.plan_for(s, item, "C1").correct for s in cohort.simulees[:40]
        }
        assert len(plans) == 2, "every simulee answered identically — the seed is not per-person"

    def test_ability_raises_the_success_probability(self, cohort, bank):
        from evaluation.dgp import Simulee

        item = next(i for i in bank.shortlist("C1", exclude=set()) if i.modality == "mcq")
        weak = Simulee("w", "monotonic", 0, {"C1": -2.0}, {})
        strong = Simulee("s", "monotonic", 7, {"C1": 2.0}, {})
        assert responder.success_probability(weak, item, "C1") < responder.success_probability(
            strong, item, "C1"
        )

    def test_node_mastery_moves_the_probability_in_the_right_direction(self, bank):
        from evaluation.dgp import Simulee

        item = next(i for i in bank.shortlist("C1", exclude=set()) if i.modality == "mcq")
        node = responder.primary_node(item)
        theta = {"C1": 0.0}
        masters = Simulee("m", "monotonic", 4, theta, {node: 1})
        lacks = Simulee("l", "monotonic", 4, theta, {node: 0})
        assert responder.success_probability(masters, item, "C1") > responder.success_probability(
            lacks, item, "C1"
        )


# --- the data-generating process ------------------------------------------------------


@requires_registry
class TestDGP:
    def test_the_null_arm_has_no_structure_and_no_node_truth(self, bank, graph_edges):
        null = build_cohort(
            dgp="DGP-0",
            n=40,
            seed=1,
            bank=bank,
            bank_id="AIE",
            mains=bank.variables(),
            graph_prerequisites=graph_edges,
        )
        assert null.true_prerequisites == []
        assert all(s.nodes == {} for s in null.simulees)

    def test_the_matched_arm_reproduces_the_graphs_edges_exactly(self, cohort, graph_edges):
        assert sorted(cohort.true_prerequisites) == sorted(graph_edges)

    def test_the_partial_arm_both_drops_and_adds_edges(self, bank, graph_edges):
        partial = build_cohort(
            dgp="DGP-2",
            n=40,
            seed=1,
            bank=bank,
            bank_id="AIE",
            mains=bank.variables(),
            graph_prerequisites=graph_edges,
            wrong_edge_fraction=0.25,
        )
        assert partial.wrong_edges, "no graph edge was dropped — DGP-2 is DGP-1"
        assert partial.missing_edges, "no true edge was hidden — missed blocking is unmeasurable"
        assert set(partial.wrong_edges) & set(partial.true_prerequisites) == set()

    def test_a_child_is_rarely_mastered_when_its_true_prerequisite_is_not(self, cohort):
        """The structure has to be IN the data, or the DAG arms measure nothing.

        Checked as a rate rather than absolutely: `edge_strength` is 0.85 on purpose, and
        the contradictory profile family inverts a quarter of its draws. A deterministic
        gate would make every inference trivially right.
        """
        violations = total = 0
        for simulee in cohort.simulees:
            if simulee.family == "contradictory":
                continue
            for parent, child in cohort.true_prerequisites:
                if parent in simulee.nodes and child in simulee.nodes:
                    if simulee.nodes[parent] == 0:
                        total += 1
                        violations += simulee.nodes[child]
        assert total > 0
        assert violations / total < 0.25, "the prerequisite structure is not in the data"

    def test_every_arm_produces_a_balanced_design(self, bank, graph_edges):
        for dgp in DGP_ARMS:
            built = build_cohort(
                dgp=dgp,
                n=40,
                seed=3,
                bank=bank,
                bank_id="AIE",
                mains=bank.variables(),
                graph_prerequisites=graph_edges,
            )
            counts = {}
            for simulee in built.simulees:
                counts[(simulee.stratum, simulee.family)] = (
                    counts.get((simulee.stratum, simulee.family), 0) + 1
                )
            assert len(set(counts.values())) == 1, f"{dgp} is unbalanced"

    def test_a_cohort_round_trips_through_disk(self, cohort, tmp_path):
        from evaluation.dgp import Cohort

        path = cohort.save(tmp_path / "c.json")
        reloaded = Cohort.load(path)
        assert reloaded.dgp == cohort.dgp
        assert reloaded.true_prerequisites == cohort.true_prerequisites
        assert [s.simulee_id for s in reloaded.simulees] == [s.simulee_id for s in cohort.simulees]
        assert reloaded.simulees[5].nodes == cohort.simulees[5].nodes


# --- the common band scale --------------------------------------------------------------


class TestCommonBands:
    def test_the_common_scale_is_the_engines_own_five_band_scale(self):
        from app.services.adaptive.irt import ability_band

        if not hasattr(__import__("app.services.adaptive.irt", fromlist=["x"]), "BAND_CUTS"):
            pytest.skip("this branch has no explicit cut points to agree with")
        for theta in np.arange(-3.5, 3.6, 0.25):
            assert band_of(float(theta)) == ability_band(float(theta))[0]

    def test_bands_are_monotone_in_theta(self):
        levels = [band_of(float(t)) for t in np.arange(-4.0, 4.01, 0.1)]
        assert levels == sorted(levels)

    def test_the_band_count_parameter_produces_that_many_bands(self):
        for n in (4, 5, 8):
            cuts = cuts_for(n)
            assert len(cuts) == n - 1
            assert band_of(-3.999, cuts) == 1
            assert band_of(3.999, cuts) == n

    def test_band_probabilities_sum_to_one_and_agree_with_the_point_rule(self):
        from app.services.adaptive.irt import THETA_GRID

        posterior = np.exp(-0.5 * ((THETA_GRID - 0.7) / 0.6) ** 2)
        posterior /= posterior.sum()
        probabilities = band_probabilities(posterior)
        assert sum(probabilities.values()) == pytest.approx(1.0)
        # The band holding the most mass must be the band the mean falls in, for a
        # posterior this concentrated.
        assert max(probabilities, key=probabilities.get) == band_of(0.7, COMMON_CUTS)


# --- the virtual clock ---------------------------------------------------------------------


class TestVirtualClock:
    def test_it_only_moves_when_advanced(self):
        clock = VirtualClock()
        start = clock.time()
        assert clock.time() == start
        clock.advance(75.0)
        assert clock.time() == start + 75.0

    def test_a_session_records_the_duration_the_items_cost(self, bank):
        """`elapsed_minutes` must equal the modelled duration, or E-04 measures nothing."""
        import asyncio

        from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository
        from app.services.orchestrator.grader import GraderAgent
        from app.services.orchestrator.orchestrator import Orchestrator

        from evaluation.dgp import Simulee
        from evaluation.session_runner import run_session

        responder.install_stubs()
        grader = GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository()))
        if HAS_REGISTRY:
            orchestrator = Orchestrator(
                bank,
                grader,
                graph=registry.get_graph_service("AIE"),
                coverage_critical_only=registry.profile("AIE").coverage_critical_only,
            )
        else:
            orchestrator = Orchestrator(bank, grader)

        record = asyncio.run(
            run_session(
                orchestrator=orchestrator,
                bank=bank,
                simulee=Simulee("clock", "monotonic", 4, {"C1": 0.3}, {}),
                mains=["C1"],
                arm="t",
            )
        )
        assert record["items_administered"] > 0
        assert record["modelled_duration_minutes"] == pytest.approx(
            record["elapsed_minutes"], abs=0.01
        )
        assert record["modelled_duration_minutes"] > 0


# --- the statistics -------------------------------------------------------------------------


class TestStats:
    def test_the_rule_of_three_holds_for_zero_observed_failures(self):
        for n in (100, 300, 1000):
            assert stats.clopper_pearson_upper(0, n) == pytest.approx(3.0 / n, rel=0.05)

    def test_no_sample_size_demonstrates_a_rate_that_equals_its_gate(self):
        assert stats.events_needed_for_upper_bound(0.03, 0.03) == 0
        assert stats.events_needed_for_upper_bound(0.03, 0.0) > 90

    def test_the_sample_size_formula_matches_its_closed_form(self):
        from scipy.stats import norm

        z = norm.ppf(0.95) + norm.ppf(0.80)
        assert stats.n_for_paired_binary(0.02, 0.20, 0.80) == pytest.approx(
            np.ceil(z**2 * 0.20 / 0.02**2), rel=1e-6
        )

    def test_the_detectable_margin_inverts_the_sample_size(self):
        n = stats.n_for_paired_binary(0.02, 0.20, 0.80)
        assert stats.detectable_margin(n, 0.20, 0.80) == pytest.approx(0.02, rel=0.01)

    def test_tango_puts_the_upper_bound_above_the_point_estimate(self):
        interval = stats.tango_score_interval(b=20, c=40, n=500)
        assert interval.point == pytest.approx((40 - 20) / 500)
        assert interval.upper > interval.point

    def test_non_inferiority_declares_it_when_the_arms_are_identical(self):
        rng = np.random.default_rng(0)
        ref = rng.random(4000) < 0.65
        result = stats.non_inferiority_binary(ref, ref.copy(), 0.02)
        assert result["discordance_psi"] == 0.0
        assert result["non_inferior"] is True

    def test_non_inferiority_refuses_it_when_the_test_arm_is_clearly_worse(self):
        rng = np.random.default_rng(1)
        ref = rng.random(4000) < 0.65
        test = ref.copy()
        # Flip 8% of the correct answers to wrong: far outside a 2pp margin.
        flip = np.flatnonzero(ref)[: int(0.08 * 4000)]
        test[flip] = False
        result = stats.non_inferiority_binary(ref, test, 0.02)
        assert result["non_inferior"] is False

    def test_the_bootstrap_interval_covers_a_known_mean(self):
        rng = np.random.default_rng(2)
        values = rng.normal(0.0, 1.0, 800)
        strata = np.array(["a", "b", "c", "d", "e"] * 160)
        interval = stats.bca_bootstrap(values, strata, resamples=2000, one_sided=True)
        assert interval.point == pytest.approx(values.mean())
        assert interval.upper > interval.point
        assert interval.upper < 0.5

    def test_fast_delong_agrees_with_the_naive_auc(self):
        rng = np.random.default_rng(3)
        y = (rng.random(400) < 0.4).astype(int)
        p_ref = np.clip(y * 0.3 + rng.random(400) * 0.7, 0, 1)
        p_test = np.clip(y * 0.5 + rng.random(400) * 0.5, 0, 1)
        result = stats.delong_auc_difference(p_ref, p_test, y)
        # 1e-5 because the reported AUCs are rounded for the result file; the two
        # implementations agree exactly before rounding.
        assert result["auc_ref"] == pytest.approx(stats.auc(p_ref, y), abs=1e-5)
        assert result["auc_test"] == pytest.approx(stats.auc(p_test, y), abs=1e-5)
        assert result["se"] > 0

    def test_marginal_reliability_is_one_when_there_is_no_error(self):
        theta = np.linspace(-2, 2, 100)
        assert stats.marginal_reliability(theta, np.zeros(100)) == pytest.approx(1.0)

    def test_decision_consistency_is_one_for_a_certain_posterior(self):
        assert stats.decision_consistency([{"3": 1.0}, {"4": 1.0}]) == pytest.approx(1.0)
        # A coin flip between two bands is the least consistent two-band case.
        assert stats.decision_consistency([{"3": 0.5, "4": 0.5}]) == pytest.approx(0.5)

    def test_cliffs_delta_is_signed_and_bounded(self):
        a = np.arange(100.0)
        assert stats.cliffs_delta(a, a) == pytest.approx(0.0, abs=1e-9)
        assert stats.cliffs_delta(a + 1000, a) == pytest.approx(1.0)
        assert stats.cliffs_delta(a - 1000, a) == pytest.approx(-1.0)


class TestGateEvaluation:
    def test_a_missing_value_is_never_a_pass(self):
        from evaluation.gates import PRIMARY, verdict

        assert verdict(PRIMARY, None) == "NOT MEASURED"
        assert verdict(PRIMARY, float("nan")) == "NOT MEASURED"

    def test_the_family_wise_error_matches_the_documents_table(self):
        from evaluation.gates import family_wise_error

        assert family_wise_error(5) == pytest.approx(0.226, abs=0.001)
        assert family_wise_error(20) == pytest.approx(0.642, abs=0.001)
        assert family_wise_error(26) == pytest.approx(0.736, abs=0.001)
