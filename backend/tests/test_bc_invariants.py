"""Phase 1 — the deterministic invariants of the B/C test plan, INV-01 through INV-06.

TIER 1 OF THE RESTRUCTURED GATE TABLE

These are the eight gates the validation document's B4 separates out: binary, no sampling
error, no alpha, no confidence interval. A violation count of zero means zero in this
suite, not "zero within the margin of error". They gate CI, and they carry no statistical
weight in the comparison — which is the point of separating them, because presenting a
unit test and a 4,000-simulee estimate as the same species of claim is what produced the
26-gate multiplicity problem.

Every test here runs on BOTH branches. Approach C's additional assertions are skipped, not
failed, where the module they exercise does not exist — a branch without a competency graph
cannot violate a graph invariant, and recording that as a pass would be a lie of the same
kind as recording it as a failure.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from app.schemas.orchestration import BankItem
from app.services.adaptive.irt import THETA_GRID, posterior_update, uniform_prior
from app.services.orchestrator.grader import GraderAgent
from app.services.orchestrator.orchestrator import Orchestrator
from app.services.orchestrator.outcome import GradedOutcome, graded_posterior_update

try:
    from app.services.competency_graph import config as graph_config  # noqa: F401

    HAS_GRAPH = True
except ImportError:
    HAS_GRAPH = False

requires_graph = pytest.mark.skipif(not HAS_GRAPH, reason="Approach B has no competency graph")


# --- fixtures that work on either branch ---------------------------------------------


@pytest.fixture
def bank():
    """The AI Engineer bank, addressed the way this branch addresses banks."""
    if HAS_GRAPH:
        from app.services.orchestrator import registry

        return registry.get_bank("AIE")
    from pathlib import Path

    from app.services.orchestrator.bank import JsonUnifiedBank

    root = Path(__file__).resolve().parents[1]
    return JsonUnifiedBank(root / "app" / "data" / "question_bank_AIE.json")


@pytest.fixture
def orchestrator(bank):
    """Wired with the code engine, because the AI Engineer bank contains code items.

    The sandbox behind it is stubbed by `responder.install_stubs`; without the engine at
    all the grader refuses a code item outright and the invariant under test is never
    reached.
    """
    from app.services.code_adaptive import CodeAdaptiveSession, JsonQuestionRepository

    grader = GraderAgent(code_engine=CodeAdaptiveSession(JsonQuestionRepository()))
    if HAS_GRAPH:
        from app.services.orchestrator import registry

        return Orchestrator(
            bank,
            grader,
            graph=registry.get_graph_service("AIE"),
            coverage_critical_only=registry.profile("AIE").coverage_critical_only,
        )
    return Orchestrator(bank, grader)


def _mcq(bank, variable: str = "C1") -> BankItem:
    return next(i for i in bank.shortlist(variable, exclude=set()) if i.modality == "mcq")


# --- INV-01 posterior update reproducibility -------------------------------------------


class TestINV01PosteriorReproducibility:
    """Identical inputs produce an identical posterior, to 1e-10."""

    def test_the_same_outcome_twice_gives_bit_identical_posteriors(self, bank):
        item = _mcq(bank)
        prior = uniform_prior()
        first = graded_posterior_update(prior, item.cat.a, item.cat.b, item.cat.c, 1.0, 1.0)
        second = graded_posterior_update(prior, item.cat.a, item.cat.b, item.cat.c, 1.0, 1.0)
        assert float(np.max(np.abs(first[0] - second[0]))) <= 1e-10
        assert abs(first[1] - second[1]) <= 1e-10
        assert abs(first[2] - second[2]) <= 1e-10

    def test_the_fractional_likelihood_reduces_exactly_to_the_bernoulli_one(self, bank):
        """At score in {0,1} and weight 1 the graded update IS the MCQ update.

        Asserted as float equality rather than a tolerance: the reduction is by
        construction, so any drift at all is a change in what an MCQ answer means.
        """
        item = _mcq(bank)
        prior = uniform_prior()
        for score, correct in ((1.0, True), (0.0, False)):
            graded = graded_posterior_update(
                prior, item.cat.a, item.cat.b, item.cat.c, score, 1.0
            )
            binary = posterior_update(prior, item.cat.a, item.cat.b, item.cat.c, correct)
            assert np.array_equal(graded[0], binary[0])
            assert graded[1] == binary[1]
            assert graded[2] == binary[2]

    def test_a_whole_session_replays_to_the_same_estimate(self, orchestrator, bank):
        """Same responses in the same order, twice, same theta. No hidden state."""
        from evaluation import responder
        from evaluation.dgp import Simulee
        from evaluation.session_runner import run_session

        responder.install_stubs()
        simulee = Simulee(
            simulee_id="inv01", family="monotonic", stratum=4, theta={"C1": 0.4}, nodes={}
        )
        first = asyncio.run(
            run_session(orchestrator=orchestrator, bank=bank, simulee=simulee, mains=["C1"], arm="t")
        )
        second = asyncio.run(
            run_session(orchestrator=orchestrator, bank=bank, simulee=simulee, mains=["C1"], arm="t")
        )
        assert first["served_item_ids"] == second["served_item_ids"]
        assert first["variables"][0]["estimated_theta"] == second["variables"][0]["estimated_theta"]
        assert first["variables"][0]["standard_error"] == second["variables"][0]["standard_error"]


# --- INV-02 zero-weight no-op -----------------------------------------------------------


class TestINV02ZeroWeightNoOp:
    """A zero-weight outcome changes nothing — including the observation count.

    The observation-count half is the one worth asserting. The architecture never states
    whether an unscorable event increments `observations`, and three separate guards read
    that number: the precision floor, the stable-band floor, and the modality blueprint's
    urgency calculation. If an audio failure incremented it, an infrastructure fault would
    move a candidate closer to a convergence stop.
    """

    def test_a_zero_weight_outcome_leaves_the_posterior_untouched(self, bank):
        item = _mcq(bank)
        prior = uniform_prior()
        posterior, theta, se = graded_posterior_update(
            prior, item.cat.a, item.cat.b, item.cat.c, 1.0, 0.0
        )
        assert np.array_equal(posterior, prior)
        assert theta == pytest.approx(float(np.sum(THETA_GRID * prior)))
        assert se == pytest.approx(float(np.sqrt(np.sum((THETA_GRID - theta) ** 2 * prior))))

    def test_a_zero_weight_outcome_does_not_count_as_an_observation(self, bank):
        from app.services.orchestrator import variables as variables_module

        item = _mcq(bank)
        state = variables_module.seed_variable("C1")
        outcome = GradedOutcome(variable="C1", score=1.0, weight=0.0)
        after = variables_module.apply_outcome(
            state, outcome, item.cat.a, item.cat.b, item.cat.c, item.item_id
        )
        assert after.observations == state.observations == 0
        assert after.served_item_ids == state.served_item_ids
        assert after.band_history == state.band_history

    @requires_graph
    def test_an_unscorable_event_produces_no_graph_effect(self):
        from app.services.competency_graph.config import PropagationConfig
        from app.services.competency_graph.evidence import EvidenceEvent
        from app.services.competency_graph.ledger import EvidenceLedger
        from app.services.competency_graph.propagation import apply_direct_evidence
        from app.services.competency_graph.state import CompetencyGraphState
        from app.services.orchestrator import registry

        graph = registry.get_graph_service("AIE")
        state = CompetencyGraphState()
        state.ensure_nodes(set(graph.graph.nodes))
        result = apply_direct_evidence(
            graph,
            state,
            EvidenceEvent(
                evidence_id="inv02",
                session_id="s",
                item_id="i",
                modality="code",
                target_node="C1.2",
                score=1.0,
                weight=0.0,
                confidence=0.0,
                source="i",
            ),
            ledger=EvidenceLedger(),
            config=PropagationConfig(),
        )
        assert result.unscorable
        assert not result.inferred_signals
        assert not result.blocked_nodes
        assert not result.changed_nodes
        assert not result.affected_mains


# --- INV-03 stop ownership ---------------------------------------------------------------


class TestINV03StopOwnership:
    """A model cannot end an assessment, and cannot finalise a graph node.

    Asserted structurally rather than by injection: `pick` returns a queued candidate and
    has no return path that reaches a stop decision, and `should_stop` reads only session
    state. A model output claiming `{"stop": true}` has nowhere to be read.
    """

    def test_the_picker_contract_carries_no_stop_field(self):
        from app.schemas.orchestration import QueuedCandidate

        assert "stop" not in QueuedCandidate.model_fields
        assert "finalize_node" not in QueuedCandidate.model_fields

    def test_should_stop_reads_only_session_state(self, orchestrator):
        state = orchestrator.begin(["C1"])
        stop, reason = orchestrator.should_stop(state)
        # An assessment with an unfilled queue has no candidate to present, which is a
        # budget outcome and never a convergence one.
        assert stop is True
        assert reason == "no_candidates_available"
        assert not state.variables["C1"].converged


# --- INV-04 candidate eligibility integrity ------------------------------------------------


class TestINV04Eligibility:
    def test_a_served_item_is_never_offered_again(self, orchestrator, bank):
        from evaluation import responder
        from evaluation.dgp import Simulee
        from evaluation.session_runner import run_session

        responder.install_stubs()
        simulee = Simulee(
            simulee_id="inv04", family="monotonic", stratum=5, theta={"C1": 1.0, "C3": 0.5}, nodes={}
        )
        record = asyncio.run(
            run_session(
                orchestrator=orchestrator, bank=bank, simulee=simulee, mains=["C1", "C3"], arm="t"
            )
        )
        served = record["served_item_ids"]
        assert len(served) == len(set(served)), "an item was administered twice"

    def test_a_finalised_variable_is_never_picked_for(self, orchestrator, bank):
        state = orchestrator.begin(["C1"])
        state = asyncio.run(orchestrator.fill_queue(state, use_llm=False))
        assert "C1" in state.queue

        from app.services.orchestrator import variables as variables_module

        finalised = variables_module.mark_exhausted(state.variables["C1"])
        state = state.model_copy(update={"variables": {"C1": finalised}, "queue": {}})
        state = asyncio.run(orchestrator.fill_queue(state, use_llm=False))
        assert state.queue == {}
        assert orchestrator.choose_variable(state) is None


# --- INV-05 stale queue ---------------------------------------------------------------------


class TestINV05StaleQueue:
    def test_a_response_releases_the_queue_slot_of_every_main_it_evidences(
        self, orchestrator, bank
    ):
        from evaluation import responder
        from evaluation.dgp import Simulee

        responder.install_stubs()
        simulee = Simulee(
            simulee_id="inv05", family="monotonic", stratum=5, theta={"C1": 0.5, "C3": 0.5}, nodes={}
        )
        state = orchestrator.begin(["C1", "C3"])
        state = asyncio.run(orchestrator.fill_queue(state, use_llm=False))
        state = orchestrator.ensure_presenting(state)
        item, candidate = orchestrator.next_item(state)

        plan = responder.plan_for(simulee, item, candidate.variable)
        responder.CURRENT = plan
        try:
            state, _graded = orchestrator.record_response(
                state, item, responder.response_for(item, plan, simulee)
            )
        finally:
            responder.CURRENT = None

        # The competency just answered must not still hold a queue slot chosen against the
        # estimate that response has now moved.
        assert candidate.variable not in state.queue


# --- INV-06 unknown item rejection ------------------------------------------------------------


class TestINV06UnknownItem:
    def test_an_unknown_item_id_yields_no_item_rather_than_an_exception(self, orchestrator, bank):
        from app.schemas.orchestration import QueuedCandidate

        state = orchestrator.begin(["C1"])
        state = state.model_copy(
            update={
                "presenting": QueuedCandidate(
                    variable="C1",
                    item_id="does-not-exist",
                    modality="mcq",
                    criterion="E[Fisher]",
                    information=1.0,
                    utility=1.0,
                    best_information=1.0,
                    normalized_regret=0.0,
                    engine_top_pick="does-not-exist",
                    chosen_by_llm=False,
                )
            }
        )
        assert orchestrator.next_item(state) is None

    def test_the_bank_does_not_resolve_an_unknown_id(self, bank):
        assert bank.get("does-not-exist") is None


# --- posterior isolation: the invariant the whole DAG design rests on ---------------------------


@requires_graph
class TestPosteriorIsolation:
    """Inferred evidence must never reach a likelihood.

    This is the single assumption that makes Approach C safe to compare on measurement at
    all: a deduction FROM a response is not a second response. `InferredNodeSignal` is the
    structural guarantee — it carries no score and no weight, so there is nothing for a
    graded outcome to be built from.
    """

    def test_an_inferred_signal_carries_neither_score_nor_weight(self):
        from app.services.competency_graph.inference import InferredNodeSignal

        fields = set(InferredNodeSignal.__dataclass_fields__)
        assert "score" not in fields
        assert "weight" not in fields

    def test_the_step_trace_records_equal_direct_and_total_main_weight(self, orchestrator, bank):
        """w_M_total must equal w_M_direct on every step, in a session that infers.

        The validation document asks for both fields precisely so rollup inflation is
        measured rather than asserted. They are equal by construction today; this is what
        fails if that ever stops being true.
        """
        from evaluation import responder
        from evaluation.dgp import Simulee
        from evaluation.session_runner import run_session

        responder.install_stubs()
        simulee = Simulee(
            simulee_id="iso", family="monotonic", stratum=6, theta={"C1": 1.8}, nodes={}
        )
        record = asyncio.run(
            run_session(
                orchestrator=orchestrator,
                bank=bank,
                simulee=simulee,
                mains=["C1"],
                arm="t",
                keep_trace=True,
            )
        )
        assert record["trace"], "no steps traced"
        for step in record["trace"]:
            assert step["w_m_total"] == step["w_m_direct"]
