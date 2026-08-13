"""Tier-1 invariants for the propagation configuration surface (INV-P1..P10).

Deterministic assertions. No alpha, no confidence interval, no sampling: each one either
holds for every input or the configuration surface is unsound and no cell of the study
means anything. The pre-registration says any failure here aborts the run, which is only
a real commitment if the assertions are executable — hence this file.

WHAT THESE ARE FOR

A factorial design assumes its factors are separable and monotone. If `D=2` can infer
something `D=3` cannot, or `K=3` admits something `K=2` refused, the main effects
estimated from the design are not effects of anything. Most of this file is those two
properties, checked directly rather than assumed.
"""

from __future__ import annotations

import dataclasses
from itertools import pairwise

import pytest

from cat_engine.engine.services.competency_graph.config import (
    CorroborationIndependence,
    PropagationConfig,
)
from cat_engine.engine.services.competency_graph.evidence import EvidenceEvent
from cat_engine.engine.services.competency_graph.graph import CompetencyGraphService
from cat_engine.engine.services.competency_graph.models import CompetencyStatus
from cat_engine.engine.services.competency_graph.propagation import apply_events

try:
    from cat_engine.engine.services.competency_graph import load_default_competency_graph

    HAS_GRAPH = True
except ImportError:  # pragma: no cover - Approach B has no graph module
    HAS_GRAPH = False

requires_graph = pytest.mark.skipif(not HAS_GRAPH, reason="Approach B has no competency graph")

pytestmark = requires_graph


@pytest.fixture(scope="module")
def graph() -> CompetencyGraphService:
    """The DA graph with every PREREQUISITE edge live.

    Forced on deliberately. As shipped the edges are inert, and an invariant checked
    against a graph that concludes nothing passes for the wrong reason — it would hold
    equally well if inference were deleted.
    """
    loaded = load_default_competency_graph()
    edges = tuple(
        dataclasses.replace(e, allow_upward_inference=True, allow_downward_blocking=True)
        if e.relation == "PREREQUISITE"
        else e
        for e in loaded.edges
    )
    return CompetencyGraphService(dataclasses.replace(loaded, edges=edges))


def _success(node: str, index: int = 1, *, item: str | None = None) -> EvidenceEvent:
    return EvidenceEvent(
        evidence_id=f"ev-{index}",
        session_id="s",
        item_id=item or f"item-{index}",
        modality="code",
        target_node=node,
        score=0.95,
        weight=1.0,
        confidence=0.95,
        source="test",
    )


def _failure(node: str, index: int = 1) -> EvidenceEvent:
    return dataclasses.replace(_success(node, index), score=0.05)


def _deepest_child(graph: CompetencyGraphService) -> str:
    """A node with the longest chain of prerequisites above it.

    Picked by measurement rather than hardcoded, so the depth invariants exercise a real
    multi-hop path on whatever graph ships rather than a one-hop edge that would make
    monotonicity trivially true.
    """
    best, best_depth = None, -1
    for node in graph.graph.nodes:
        reached = len(
            apply_events(
                graph,
                [_success(node)],
                config=PropagationConfig(maximum_propagation_depth=9),
            )[0][0].inferred_signals
        )
        if reached > best_depth:
            best, best_depth = node, reached
    assert best is not None and best_depth > 0, "no node in this graph infers anything"
    return best


def _inferred_at(graph: CompetencyGraphService, node: str, **config_kwargs) -> set[str]:
    """The set of ancestors CONCLUDED from one strong success under this configuration."""
    results, _state, _ledger = apply_events(
        graph, [_success(node)], config=PropagationConfig(**config_kwargs)
    )
    return set(results[0].corroborated_nodes)


# --- INV-P1 ---------------------------------------------------------------------------


class TestINVP1DepthZeroDrawsNothing:
    """`D = 0` concludes nothing, writes nothing, and leaves no provenance.

    RESTATED FROM THE PRE-REGISTRATION (amendment 13.2). The plan asked for byte-identical
    session records to C-shipped. That is not achievable and asserting it would have
    pinned a defect: C-shipped as it actually shipped wrote INFERRED_MASTERED into node
    state even with the deployment switch off, so `D = 0` is identical to C-shipped's
    INTENDED behaviour and deliberately different from its actual behaviour.

    Asserted here over the observable outputs, excluding audit and provenance fields.
    """

    def test_no_signal_no_status_no_support(self, graph):
        child = _deepest_child(graph)
        results, state, _ = apply_events(
            graph, [_success(child)], config=PropagationConfig(maximum_propagation_depth=0)
        )

        assert results[0].inferred_signals == ()
        assert results[0].corroborated_nodes == frozenset()
        assert results[0].inferred_mastered_nodes == frozenset()
        assert not any(
            node.status is CompetencyStatus.INFERRED_MASTERED for node in state.nodes.values()
        )
        assert not any(node.inferred_support for node in state.nodes.values())

    def test_the_directly_tested_node_is_unaffected(self, graph):
        """D governs propagation only. The response itself still counts."""
        child = _deepest_child(graph)
        _results, state, _ = apply_events(
            graph, [_success(child)], config=PropagationConfig(maximum_propagation_depth=0)
        )
        assert state.nodes[child].status is CompetencyStatus.DIRECT_MASTERED
        assert state.nodes[child].direct_observations == 1

    def test_depth_zero_is_reachable_from_configuration(self):
        """The pydantic floor was 1, so this state could not be configured at all."""
        from cat_engine.engine.config.settings import Settings

        assert Settings(graph_maximum_propagation_depth=0).graph_maximum_propagation_depth == 0


# --- INV-P2 ---------------------------------------------------------------------------


class TestINVP2InferenceIsMonotoneInDepth:
    """Raising `D` may add conclusions and may never remove one.

    Without this the depth main effect is not an effect of depth: a non-monotone factor
    makes `mean(y|high) - mean(y|low)` a difference between two arbitrary configurations.
    """

    def test_each_depth_contains_the_one_below(self, graph):
        child = _deepest_child(graph)
        sets = [_inferred_at(graph, child, maximum_propagation_depth=d) for d in range(6)]
        for shallow, deep in pairwise(sets):
            assert shallow <= deep, f"raising D lost {sorted(shallow - deep)}"

        # Non-vacuity, as for K: a factor that changes nothing is trivially monotone and
        # gives the design nothing to estimate.
        assert sets[0] < sets[1], "raising D from 0 to 1 added no inference"

    def test_distance_never_exceeds_the_configured_depth(self, graph):
        """INV-P2 as the plan words it: no inference recorded at distance > D."""
        child = _deepest_child(graph)
        for depth in range(5):
            results, _state, _ = apply_events(
                graph,
                [_success(child)],
                config=PropagationConfig(maximum_propagation_depth=depth),
            )
            for signal in results[0].inferred_signals:
                assert 1 <= signal.distance <= depth


class TestINVP2bDepthFactorsAreSeparable:
    """Inference depth and blocking depth move independently.

    They shared one field, so a factorial sweeping inference depth also swept the blocking
    frontier and delivered the two effects confounded in one number.
    """

    def test_inference_depth_does_not_move_the_blocking_frontier(self, graph):
        child = _deepest_child(graph)
        wide = PropagationConfig(maximum_inference_depth=4, maximum_blocking_depth=1)
        narrow = PropagationConfig(maximum_inference_depth=1, maximum_blocking_depth=1)
        assert wide.blocking_depth == narrow.blocking_depth == 1
        assert _inferred_at(graph, child, maximum_inference_depth=4) >= _inferred_at(
            graph, child, maximum_inference_depth=1
        )

    def test_both_default_to_the_shared_field(self):
        """The compatibility contract: unset behaves exactly as before the split."""
        config = PropagationConfig(maximum_propagation_depth=3)
        assert config.inference_depth == 3
        assert config.blocking_depth == 3


# --- INV-P3 ---------------------------------------------------------------------------


class TestINVP3CorroborationIsMonotoneAndUnfarmable:
    """Raising `K` may remove conclusions and may never add one — and ADV-5.

    The second half is the one that decides whether `K` is a safety control at all.
    """

    def test_each_k_is_contained_by_the_one_below(self, graph):
        child = _deepest_child(graph)
        sets = [
            _inferred_at(graph, child, minimum_corroborations=k) for k in range(1, 5)
        ]
        for loose, strict in pairwise(sets):
            assert strict <= loose, f"raising K added {sorted(strict - loose)}"

        # NON-VACUITY. Monotonicity alone is satisfied by a factor that does nothing —
        # a constant function is trivially monotone, and an implementation that ignored K
        # entirely would pass the loop above. Screening cannot detect an effect that does
        # not exist, so assert the knob moves something.
        assert sets[0] > sets[1], (
            "raising K from 1 to 2 removed no inference: the factor has no effect and "
            "the design cannot estimate one"
        )

    def test_no_inference_below_the_corroboration_requirement(self, graph):
        """INV-P3 as worded: nothing inferred with fewer than K observations."""
        child = _deepest_child(graph)
        for k in (1, 2, 3):
            _results, state, _ = apply_events(
                graph,
                [_success(child, i, item=f"item-{i}") for i in range(1, 4)],
                config=PropagationConfig(
                    minimum_corroborations=k,
                    corroboration_independence=CorroborationIndependence.EVIDENCE,
                ),
            )
            for node in state.nodes.values():
                if node.status is CompetencyStatus.INFERRED_MASTERED:
                    assert len(node.inferred_support) >= k

    def test_adv5_clones_of_one_item_cannot_farm_corroboration(self, graph):
        """ADV-5. Adding K creates an incentive to satisfy it cheaply.

        Near-identical items measure the same sub-competency, so under the default
        independence rule any number of them collapse to one observation. If K could be
        met by answering clones, the knob would be a decoration — and it would be reported
        as a safety control while providing no safety.
        """
        child = _deepest_child(graph)
        clones = [_success(child, i, item=f"clone-{i}") for i in range(1, 11)]

        _results, state, _ = apply_events(
            graph,
            clones,
            config=PropagationConfig(
                minimum_corroborations=2,
                corroboration_independence=CorroborationIndependence.SOURCE_NODE,
            ),
        )
        ancestors = [n for nid, n in state.nodes.items() if nid != child]
        assert all(len(n.inferred_support) <= 1 for n in ancestors), (
            "ten answers to the same sub-competency counted as more than one observation"
        )
        assert not any(n.status is CompetencyStatus.INFERRED_MASTERED for n in ancestors)

    def test_the_attack_succeeds_against_evidence_keying(self, graph):
        """The control for the test above.

        Without it, `test_adv5_...` passes on a graph that infers nothing at all. This is
        the same ten answers under the naive keying — the obvious implementation, kept as
        a sweepable level precisely so the study can price it.
        """
        child = _deepest_child(graph)
        clones = [_success(child, i, item=f"clone-{i}") for i in range(1, 11)]

        _results, state, _ = apply_events(
            graph,
            clones,
            config=PropagationConfig(
                minimum_corroborations=2,
                corroboration_independence=CorroborationIndependence.EVIDENCE,
            ),
        )
        ancestors = [n for nid, n in state.nodes.items() if nid != child]
        assert any(len(n.inferred_support) >= 2 for n in ancestors)
        assert any(n.status is CompetencyStatus.INFERRED_MASTERED for n in ancestors)


# --- INV-P4 ---------------------------------------------------------------------------


class TestINVP4PosteriorIsolationHoldsAcrossEveryFactor:
    """No propagation factor may touch an ability estimate.

    `test_graph_posterior_isolation.py` covers the structural half — an inferred signal
    has no score and no weight, so it cannot be handed to the CAT update even by mistake.
    This covers the numerical half across the whole configuration surface: sweeping every
    factor must leave the posterior bit-identical, because the graph shapes selection and
    reporting and never measurement.
    """

    def test_no_factor_carries_a_score_or_weight(self):
        from cat_engine.engine.services.competency_graph.inference import InferredNodeSignal

        fields = {f.name for f in dataclasses.fields(InferredNodeSignal)}
        assert "score" not in fields and "weight" not in fields

    def test_every_configuration_leaves_node_mastery_untouched_on_inferred_nodes(self, graph):
        """An inferred ancestor gets a STATUS and never a mastery value.

        Mastery is what a rollup averages. A deduction that wrote one would enter the
        estimate through the back door, which is the double-counting the whole
        signal/outcome split exists to prevent.
        """
        child = _deepest_child(graph)
        for depth in (1, 4):
            for k in (1, 2):
                _r, state, _ = apply_events(
                    graph,
                    [_success(child, i, item=f"i{i}") for i in (1, 2)],
                    config=PropagationConfig(
                        maximum_propagation_depth=depth,
                        minimum_corroborations=k,
                        corroboration_independence=CorroborationIndependence.EVIDENCE,
                    ),
                )
                for nid, node in state.nodes.items():
                    if nid == child or node.status is not CompetencyStatus.INFERRED_MASTERED:
                        continue
                    assert node.mastery == 0.0, f"{nid} carries mastery from an inference"
                    assert node.direct_observations == 0


# --- INV-P11 (added) ---------------------------------------------------------------------------


class TestINVP11InferenceNeverOverwritesAStrongerVerdict:
    """Four statuses outrank an inference, not two.

    BLOCKED and CONTRADICTED were missing from the guard, so inferred mastery silently
    erased a block — losing both the block and the disagreement that produced it, which
    is the opposite of the explicit reopen a DIRECT success performs.
    """

    @pytest.mark.parametrize(
        "status",
        [
            CompetencyStatus.DIRECT_MASTERED,
            CompetencyStatus.DIRECT_NOT_MASTERED,
            CompetencyStatus.BLOCKED,
            CompetencyStatus.CONTRADICTED,
        ],
    )
    def test_a_held_status_survives_an_inference(self, graph, status):
        child = _deepest_child(graph)
        results, _state, _ledger = apply_events(
            graph, [_success(child)], config=PropagationConfig()
        )
        ancestors = sorted(results[0].corroborated_nodes)
        assert ancestors, "fixture graph infers nothing; the test would be vacuous"
        target = ancestors[0]

        # Re-run with the ancestor pre-set to the status under test.
        from cat_engine.engine.services.competency_graph.ledger import EvidenceLedger
        from cat_engine.engine.services.competency_graph.state import CompetencyGraphState

        fresh = CompetencyGraphState()
        fresh.ensure_nodes(set(graph.graph.nodes))
        fresh.nodes[target].status = status

        apply_events(
            graph,
            [_success(child, 2)],
            config=PropagationConfig(),
            graph_state=fresh,
            ledger=EvidenceLedger(),
        )
        assert fresh.nodes[target].status is status, (
            f"an inference overwrote {status.value}"
        )

    def test_support_is_still_recorded_for_a_held_node(self, graph):
        """The verdict is protected; the evidence is not discarded.

        Dropping the provenance would make the disagreement invisible, and disagreement
        between a block and an inference is the most informative thing a session can say
        about an edge.
        """
        from cat_engine.engine.services.competency_graph.ledger import EvidenceLedger
        from cat_engine.engine.services.competency_graph.state import CompetencyGraphState

        child = _deepest_child(graph)
        results, _s, _l = apply_events(graph, [_success(child)], config=PropagationConfig())
        target = sorted(results[0].corroborated_nodes)[0]

        fresh = CompetencyGraphState()
        fresh.ensure_nodes(set(graph.graph.nodes))
        fresh.nodes[target].status = CompetencyStatus.BLOCKED

        apply_events(
            graph,
            [_success(child, 2)],
            config=PropagationConfig(),
            graph_state=fresh,
            ledger=EvidenceLedger(),
        )
        assert fresh.nodes[target].inferred_support, "provenance lost on a held node"


# --- INV-P12 (added) ---------------------------------------------------------------------------


class TestINVP12ProvenanceIsCompleteAndWellFormed:
    """Every inference can say where it came from, by depth AND by edge.

    Without the edge path a wrong-inference rate is attributable to a distance but never
    to an edge — and removing the edges that are wrong is the only remedy that does not
    also remove the edges that are right.
    """

    def test_every_signal_carries_a_path_from_source_to_node(self, graph):
        child = _deepest_child(graph)
        results, _state, _ = apply_events(
            graph, [_success(child)], config=PropagationConfig()
        )
        signals = results[0].inferred_signals
        assert signals, "fixture graph infers nothing; the test would be vacuous"

        for signal in signals:
            assert signal.source_node == child
            assert signal.distance >= 1
            assert signal.edge_path, "no edge path recorded"
            assert signal.edge_path[0] == signal.source_node
            assert signal.edge_path[-1] == signal.node
            # One hop per unit of distance, both endpoints inclusive.
            assert len(signal.edge_path) == signal.distance + 1
            assert len(set(signal.edge_path)) == len(signal.edge_path), "cyclic path"

    def test_every_path_step_is_a_real_prerequisite_edge(self, graph):
        """A path that names edges the graph does not have would misattribute blame."""
        child = _deepest_child(graph)
        results, _state, _ = apply_events(
            graph, [_success(child)], config=PropagationConfig()
        )
        real = {
            (e.from_id, e.to_id) for e in graph.graph.edges if e.relation == "PREREQUISITE"
        }
        for signal in results[0].inferred_signals:
            for parent, node in zip(signal.edge_path[1:], signal.edge_path, strict=False):
                # Traversal runs child -> parent, so the edge is (parent, child).
                assert (parent, node) in real, f"{(parent, node)} is not an edge"

    def test_support_records_carry_the_same_provenance(self, graph):
        child = _deepest_child(graph)
        _r, state, _ = apply_events(graph, [_success(child)], config=PropagationConfig())
        for nid, node in state.nodes.items():
            for record in node.inferred_support.values():
                assert record["source_node"] == child
                assert record["edge_path"][-1] == nid
                assert record["distance"] >= 1


# --- INV-P6, INV-P7 -----------------------------------------------------------------------


class TestINVP6ZeroWeightChangesNothing:
    """An unscorable response is not a wrong answer."""

    def test_it_leaves_the_graph_untouched(self, graph):
        child = _deepest_child(graph)
        event = dataclasses.replace(_success(child), weight=0.0)
        results, state, _ = apply_events(graph, [event], config=PropagationConfig())

        assert results[0].unscorable is True
        assert results[0].changed_nodes == frozenset()
        assert results[0].corroborated_nodes == frozenset()
        assert state.nodes[child].status is CompetencyStatus.UNKNOWN
        assert state.nodes[child].direct_observations == 0


class TestINVP7BlockedIsUntestedNeverFailed:
    """A blocked node is one nobody asked about. It is not a node that failed."""

    def test_a_blocked_node_is_not_in_the_not_mastered_set(self, graph):
        parents = {e.from_id for e in graph.graph.edges if e.relation == "PREREQUISITE"}
        assert parents, "no prerequisite edges to block through"
        parent = sorted(parents)[0]

        results, state, _ = apply_events(
            graph,
            [_failure(parent, 1), _failure(parent, 2)],
            config=PropagationConfig(minimum_failures_to_block=2),
        )
        blocked = set(results[-1].blocked_nodes)
        assert blocked, "two consistent failures blocked nothing"

        for node in blocked:
            assert state.nodes[node].status is CompetencyStatus.BLOCKED
            assert state.nodes[node].status is not CompetencyStatus.DIRECT_NOT_MASTERED
            # And it carries no mastery evidence in either direction.
            assert state.nodes[node].direct_observations == 0


# --- INV-P9 --------------------------------------------------------------------------


class TestINVP9ReplayIsDeterministic:
    """The same evidence produces the same state, including its provenance.

    A sweep cell that cannot be replayed cannot be audited, and `inferred_support` is
    keyed by a configurable rule — so its ordering and contents are exactly the kind of
    thing that drifts silently.
    """

    def test_two_runs_of_one_trace_agree_exactly(self, graph):
        child = _deepest_child(graph)
        events = [_success(child, i, item=f"i{i}") for i in (1, 2, 3)]
        config = PropagationConfig(
            minimum_corroborations=2,
            corroboration_independence=CorroborationIndependence.EVIDENCE,
        )

        _r1, first, _l1 = apply_events(graph, events, config=config, now="2026-01-01T00:00:00Z")
        _r2, second, _l2 = apply_events(graph, events, config=config, now="2026-01-01T00:00:00Z")

        assert first.to_dict() == second.to_dict()

    def test_state_survives_a_round_trip_through_its_serialised_form(self, graph):
        """The session persists this between responses; a lossy round trip loses K."""
        from cat_engine.engine.services.competency_graph.state import CompetencyGraphState

        child = _deepest_child(graph)
        _r, state, _l = apply_events(
            graph,
            [_success(child, i, item=f"i{i}") for i in (1, 2)],
            config=PropagationConfig(
                minimum_corroborations=2,
                corroboration_independence=CorroborationIndependence.EVIDENCE,
            ),
        )
        restored = CompetencyGraphState.from_dict(state.to_dict())
        assert restored.to_dict() == state.to_dict()
        for nid, node in state.nodes.items():
            assert restored.nodes[nid].inferred_support == node.inferred_support


# --- INV-P5 ---------------------------------------------------------------------------


class TestINVP5DuplicateEvidenceIsRefused:
    """One response counts once, however many times it is presented.

    The ledger is the only thing standing between a retry and a doubled likelihood. It
    matters more with corroboration than it did without: `K` counts observations, so an
    evidence event admitted twice does not merely re-weight an estimate, it manufactures
    the independent confirmation the safety requirement is asking for.
    """

    def test_replaying_one_event_raises(self, graph):
        from cat_engine.engine.services.competency_graph.ledger import (
            DuplicateEvidenceError,
            EvidenceLedger,
        )
        from cat_engine.engine.services.competency_graph.state import CompetencyGraphState

        child = _deepest_child(graph)
        state = CompetencyGraphState()
        state.ensure_nodes(set(graph.graph.nodes))
        ledger = EvidenceLedger()
        event = _success(child)

        apply_events(graph, [event], config=PropagationConfig(), graph_state=state, ledger=ledger)
        with pytest.raises(DuplicateEvidenceError):
            apply_events(
                graph, [event], config=PropagationConfig(), graph_state=state, ledger=ledger
            )

    def test_a_duplicate_cannot_manufacture_corroboration(self, graph):
        """The reason this invariant is load-bearing now and was cosmetic before."""
        from cat_engine.engine.services.competency_graph.ledger import (
            DuplicateEvidenceError,
            EvidenceLedger,
        )
        from cat_engine.engine.services.competency_graph.state import CompetencyGraphState

        child = _deepest_child(graph)
        state = CompetencyGraphState()
        state.ensure_nodes(set(graph.graph.nodes))
        ledger = EvidenceLedger()
        config = PropagationConfig(
            minimum_corroborations=2,
            corroboration_independence=CorroborationIndependence.EVIDENCE,
        )
        event = _success(child)

        apply_events(graph, [event], config=config, graph_state=state, ledger=ledger)
        with pytest.raises(DuplicateEvidenceError):
            apply_events(graph, [event], config=config, graph_state=state, ledger=ledger)

        ancestors = [n for nid, n in state.nodes.items() if nid != child]
        assert all(len(n.inferred_support) <= 1 for n in ancestors)
        assert not any(n.status is CompetencyStatus.INFERRED_MASTERED for n in ancestors)

    def test_the_ledger_reports_no_duplicates_for_a_clean_trace(self, graph):
        from cat_engine.engine.services.competency_graph.ledger import EvidenceLedger
        from cat_engine.engine.services.competency_graph.state import CompetencyGraphState

        child = _deepest_child(graph)
        state = CompetencyGraphState()
        state.ensure_nodes(set(graph.graph.nodes))
        ledger = EvidenceLedger()
        events = [_success(child, i, item=f"i{i}") for i in (1, 2, 3)]
        apply_events(graph, events, config=PropagationConfig(), graph_state=state, ledger=ledger)

        processed = ledger.processed_ids()
        assert len(processed) == len(set(processed)) == len(events)


# --- INV-P8 ---------------------------------------------------------------------------


class TestINVP8ManifestRecordsEveryFactor:
    """A cell that cannot name its configuration cannot be reproduced or believed."""

    def test_every_field_of_the_config_appears(self):
        manifest = PropagationConfig().as_manifest()
        missing = [
            f.name for f in dataclasses.fields(PropagationConfig) if f.name not in manifest
        ]
        assert missing == [], f"factors absent from the manifest: {missing}"

    def test_the_resolved_values_are_recorded_not_just_the_raw_ones(self):
        """`maximum_inference_depth: null` does not tell a reader inference went 4 hops."""
        manifest = PropagationConfig(maximum_propagation_depth=3).as_manifest()
        assert manifest["resolved"]["inference_depth"] == 3
        assert manifest["resolved"]["blocking_depth"] == 3
        assert manifest["resolved"]["modalities"] == ["code", "open", "voice"]

    def test_the_manifest_is_json_serialisable(self):
        """It is persisted with the session; a dict that cannot be written is not a record."""
        import json

        json.dumps(PropagationConfig().as_manifest())

    def test_different_configurations_hash_differently(self):
        from cat_engine.engine.services.competency_graph.manifest import (
            manifest_hash,
            propagation_manifest,
        )

        one = propagation_manifest(PropagationConfig(minimum_corroborations=1), bank_id="X")
        two = propagation_manifest(PropagationConfig(minimum_corroborations=2), bank_id="X")
        assert manifest_hash(one) != manifest_hash(two)
        assert manifest_hash(one) == manifest_hash(dict(one))

    def test_a_session_records_its_manifest_and_reports_no_drift(self):
        from cat_engine.tests.conftest import orchestrator_for

        orchestrator = orchestrator_for("AIE")
        state = orchestrator.begin(["C1"])

        assert state.propagation_manifest, "session began with no manifest"
        assert state.propagation_manifest_hash
        assert state.propagation_manifest_drift == []

        report = orchestrator.summarise(state, "all_variables_finalised")
        assert report.graph["manifest"] == state.propagation_manifest
        assert report.graph["manifest_drift"] == []

    def test_moving_a_setting_mid_session_is_recorded_as_drift(self, monkeypatch):
        """The manifest is written once at begin, but the config is rebuilt per response.

        A sweep harness that fails to isolate a cell produces exactly this, silently.

        CHANGED FIELDS ARE DOTTED PATHS. They used to be bare keys, because the diff only
        ever walked `manifest["factors"]` — which is the bug `test_manifest_drift.py` covers:
        a change under `policy` moved the hash and named nothing. Now the diff spans the same
        object the hash digests, so a field has to say which section it came from.
        """
        from cat_engine.engine.config.settings import settings
        from cat_engine.engine.services.orchestrator import registry
        from cat_engine.tests.conftest import orchestrator_for

        orchestrator = orchestrator_for("AIE")
        state = orchestrator.begin(["C1"])
        before = state.propagation_manifest_hash

        monkeypatch.setattr(settings, "graph_upward_decay", 0.3)
        registry.reset_caches()
        orchestrator._note_manifest_drift(state, evidence_marker="item-x#1")

        assert state.propagation_manifest_drift, "a changed factor was not recorded"
        entry = state.propagation_manifest_drift[0]
        assert entry["from_hash"] == before
        assert entry["at_evidence"] == "item-x#1"
        assert entry["changed"] == ["factors.upward_decay"], entry["changed"]


# --- INV-P10 --------------------------------------------------------------------------


class TestINVP10TurningPropagationOffLeavesCommittedEvidenceAlone:
    """A switch flipped mid-run governs what happens NEXT, never what already happened.

    An operator disabling inference during an incident must not find that the disabling
    silently rewrote sessions in flight — the evidence was collected under the old
    configuration and re-interpreting it retroactively would make the manifest a lie about
    the very sessions it was written to describe.
    """

    def test_already_written_status_and_support_survive(self, graph):
        from cat_engine.engine.services.competency_graph.ledger import EvidenceLedger
        from cat_engine.engine.services.competency_graph.state import CompetencyGraphState

        child = _deepest_child(graph)
        state = CompetencyGraphState()
        state.ensure_nodes(set(graph.graph.nodes))
        ledger = EvidenceLedger()

        apply_events(
            graph,
            [_success(child, 1)],
            config=PropagationConfig(enforce_inference=True),
            graph_state=state,
            ledger=ledger,
        )
        committed = {
            nid: (node.status, dict(node.inferred_support))
            for nid, node in state.nodes.items()
            if node.status is CompetencyStatus.INFERRED_MASTERED
        }
        assert committed, "nothing was inferred; the test would be vacuous"

        # Same session, inference now off. A different node, so new work happens.
        other = next(n for n in sorted(graph.graph.nodes) if n != child)
        apply_events(
            graph,
            [_success(other, 2)],
            config=PropagationConfig(enforce_inference=False),
            graph_state=state,
            ledger=ledger,
        )

        for nid, (status, support) in committed.items():
            assert state.nodes[nid].status is status, f"{nid} was rewritten by the switch"
            for key, record in support.items():
                assert state.nodes[nid].inferred_support[key] == record

    def test_nothing_new_is_written_after_the_switch(self, graph):
        from cat_engine.engine.services.competency_graph.ledger import EvidenceLedger
        from cat_engine.engine.services.competency_graph.state import CompetencyGraphState

        child = _deepest_child(graph)
        state = CompetencyGraphState()
        state.ensure_nodes(set(graph.graph.nodes))

        results, _s, _l = apply_events(
            graph,
            [_success(child, 1)],
            config=PropagationConfig(enforce_inference=False),
            graph_state=state,
            ledger=EvidenceLedger(),
        )
        # Concluded, recorded as provenance, and written nowhere.
        assert results[0].corroborated_nodes
        assert results[0].inferred_mastered_nodes == frozenset()
        assert not any(
            n.status is CompetencyStatus.INFERRED_MASTERED for n in state.nodes.values()
        )
