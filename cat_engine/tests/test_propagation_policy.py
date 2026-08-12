"""Three configuration levels, and the rule that the narrowest one wins.

The thing under test is a permission system, so the tests are mostly about what CANNOT be
turned on and by whom. A permission system that is only tested on its happy path is a
permission system that has not been tested.
"""

from __future__ import annotations

import json

import pytest

from cat_engine.engine.services.competency_graph.models import (
    CompetencyEdge,
    CompetencyGraph,
    CompetencyNode,
)
from cat_engine.engine.services.competency_graph.policy import (
    GraphPolicy,
    apply_policy,
    describe,
    resolve_policy,
)
from cat_engine.engine.services.competency_graph.validator import (
    CompetencyGraphValidationError,
    load_and_validate_graph,
)


def _graph(
    *,
    policy: GraphPolicy | None = None,
    allow_inference: bool = True,
    allow_blocking: bool = True,
    status: str | None = None,
) -> CompetencyGraph:
    metadata = {} if status is None else {"validation_status": status}
    nodes = {
        nid: CompetencyNode(competency_id=nid, title=nid, node_type="sub_competency")
        for nid in ("A", "B")
    }
    edge = CompetencyEdge(
        from_id="A",
        to_id="B",
        relation="PREREQUISITE",
        strength=0.8,
        allow_upward_inference=allow_inference,
        allow_downward_blocking=allow_blocking,
        metadata=metadata,
    )
    return CompetencyGraph(
        schema_version="test",
        nodes=nodes,
        edges=(edge,),
        policy=policy or GraphPolicy(),
    )


def _resolve(graph, *, inference=True, blocking=True, failures=2):
    return resolve_policy(
        graph,
        deployment_inference=inference,
        deployment_blocking=blocking,
        deployment_minimum_failures_to_block=failures,
    )


class TestMostRestrictiveWins:
    def test_all_three_permitting_enables_the_edge(self):
        decision = _resolve(_graph(status="validated")).decisions[0]
        assert decision.inference_allowed and decision.blocking_allowed

    def test_the_deployment_can_veto_what_the_bank_and_edge_permit(self):
        graph = _graph(
            policy=GraphPolicy(upward_inference=True, descendant_blocking=True),
            status="validated",
        )
        decision = _resolve(graph, inference=False, blocking=False).decisions[0]
        assert not decision.inference_allowed
        assert not decision.blocking_allowed
        assert "deployment" in decision.inference_blocked_by

    def test_the_bank_can_veto_what_the_deployment_and_edge_permit(self):
        graph = _graph(
            policy=GraphPolicy(upward_inference=False, descendant_blocking=False),
            status="validated",
        )
        decision = _resolve(graph).decisions[0]
        assert not decision.inference_allowed
        assert "bank policy" in decision.inference_blocked_by

    def test_the_edge_can_veto_what_the_deployment_and_bank_permit(self):
        graph = _graph(
            policy=GraphPolicy(upward_inference=True, descendant_blocking=True),
            allow_inference=False,
            allow_blocking=False,
            status="validated",
        )
        decision = _resolve(graph).decisions[0]
        assert not decision.inference_allowed
        assert "edge" in decision.inference_blocked_by

    def test_a_bank_saying_true_cannot_switch_on_a_disabled_deployment(self):
        """There is deliberately no value meaning 'this bank forces it on'."""
        graph = _graph(policy=GraphPolicy(upward_inference=True), status="validated")
        assert not _resolve(graph, inference=False).decisions[0].inference_allowed

    def test_inference_and_blocking_are_decided_independently(self):
        graph = _graph(
            policy=GraphPolicy(upward_inference=True, descendant_blocking=False),
            status="validated",
        )
        decision = _resolve(graph).decisions[0]
        assert decision.inference_allowed
        assert not decision.blocking_allowed


class TestValidationStatus:
    def test_a_declared_unvalidated_edge_is_inert_however_its_flags_read(self):
        decision = _resolve(_graph(status="unvalidated")).decisions[0]
        assert not decision.inference_allowed and not decision.blocking_allowed
        assert "validation_status" in decision.inference_blocked_by

    def test_an_edge_that_states_no_status_defers_to_its_own_flags(self):
        """Additive by design.

        A graph authored before the policy block existed carries no status. Treating that
        silence as 'unvalidated' would have switched off every such graph on upgrade — a
        behaviour change dressed as a safety default. A DECLARED 'unvalidated' is a
        different claim and is still enforced.
        """
        decision = _resolve(_graph(status=None)).decisions[0]
        assert decision.inference_allowed and decision.blocking_allowed
        assert decision.validation_status == "unstated"

    def test_a_bank_may_widen_the_accepted_statuses(self):
        graph = _graph(
            policy=GraphPolicy(accepted_validation_statuses=("validated", "provisional")),
            status="provisional",
        )
        assert _resolve(graph).decisions[0].inference_allowed

    def test_no_bank_may_accept_a_refuted_edge(self):
        with pytest.raises(ValueError, match="refuted"):
            GraphPolicy.from_dict({"accepted_validation_statuses": ["validated", "refuted"]})

    def test_an_unknown_status_is_refused_at_load_rather_than_ignored(self):
        with pytest.raises(ValueError, match="unknown validation status"):
            GraphPolicy.from_dict({"accepted_validation_statuses": ["definitely-fine"]})


class TestBlockingThreshold:
    def test_a_bank_may_demand_more_failures_than_the_deployment(self):
        graph = _graph(policy=GraphPolicy(minimum_failures_to_block=3), status="validated")
        assert _resolve(graph, failures=2).minimum_failures_to_block == 3

    def test_a_bank_may_not_demand_fewer(self):
        """Blocking is the strongest claim the system makes; this is the knob that prices it."""
        graph = _graph(policy=GraphPolicy(minimum_failures_to_block=1), status="validated")
        assert _resolve(graph, failures=2).minimum_failures_to_block == 2


class TestApplyPolicy:
    def test_bank_and_edge_vetoes_are_baked_into_the_edge_flags(self):
        graph = _graph(policy=GraphPolicy(upward_inference=False), status="validated")
        applied = apply_policy(graph, _resolve(graph))
        assert applied.edges[0].allow_upward_inference is False
        assert applied.edges[0].allow_downward_blocking is True

    def test_the_deployment_veto_is_NOT_baked_in(self):
        """It governs enforcement, not validity — the audit mirror has to keep working.

        `graph_delta` applies the deployment switch when it decides what to enforce, so the
        computation still runs and the shadow projection still records what the graph would
        have concluded. Baking it in here would silence the record for exactly the
        deployment that most needs to read it.
        """
        graph = _graph(status="validated")
        applied = apply_policy(graph, _resolve(graph, inference=False, blocking=False))
        assert applied.edges[0].allow_upward_inference is True
        assert applied.edges[0].allow_downward_blocking is True

    def test_non_prerequisite_edges_are_untouched(self):
        nodes = {
            nid: CompetencyNode(competency_id=nid, title=nid, node_type="sub_competency")
            for nid in ("A", "B")
        }
        contributes = CompetencyEdge(from_id="A", to_id="B", relation="CONTRIBUTES_TO", weight=0.4)
        graph = CompetencyGraph(schema_version="t", nodes=nodes, edges=(contributes,))
        applied = apply_policy(graph, _resolve(graph))
        assert applied.edges[0] == contributes


class TestReporting:
    def test_the_reason_names_the_widest_scope_still_refusing(self):
        """An operator flipping one flag needs to know which of the others still holds."""
        graph = _graph(
            policy=GraphPolicy(upward_inference=False),
            allow_inference=False,
            status="unvalidated",
        )
        decision = _resolve(graph, inference=False).decisions[0]
        assert decision.inference_blocked_by.startswith("deployment")

    def test_the_summary_counts_what_is_live(self):
        summary = _resolve(_graph(status="validated")).summary()
        assert summary["prerequisite_edges"] == 1
        assert summary["inference_enabled"] == 1
        assert summary["blocking_enabled"] == 1

    def test_describe_produces_one_line_per_edge_plus_a_header(self):
        lines = list(describe(_resolve(_graph(status="validated"))))
        assert len(lines) == 2
        assert "prerequisite edges: 1" in lines[0]


class TestLoadingFromDisk:
    def test_a_policy_block_round_trips(self, tmp_path):
        path = tmp_path / "graph.json"
        path.write_text(
            json.dumps(
                {
                    "version": "1.1",
                    "policy": {
                        "upward_inference": False,
                        "descendant_blocking": True,
                        "accepted_validation_statuses": ["validated", "provisional"],
                        "minimum_failures_to_block": 3,
                        "notes": "blocking piloted on C1 only",
                    },
                    "nodes": [
                        {"competency_id": "A", "node_type": "sub_competency"},
                        {"competency_id": "B", "node_type": "sub_competency"},
                    ],
                    "edges": [{"from": "A", "to": "B", "relation": "PREREQUISITE", "strength": 0.6}],
                }
            ),
            encoding="utf-8",
        )
        graph = load_and_validate_graph(path)
        assert graph.policy.upward_inference is False
        assert graph.policy.descendant_blocking is True
        assert graph.policy.accepted_validation_statuses == ("validated", "provisional")
        assert graph.policy.minimum_failures_to_block == 3

    def test_a_graph_with_no_policy_block_loads_with_no_opinion(self, tmp_path):
        path = tmp_path / "graph.json"
        path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "nodes": [{"competency_id": "A", "node_type": "sub_competency"}],
                    "edges": [],
                }
            ),
            encoding="utf-8",
        )
        policy = load_and_validate_graph(path).policy
        assert policy.upward_inference is None
        assert policy.descendant_blocking is None

    def test_a_broken_policy_block_fails_the_load_and_names_the_file(self, tmp_path):
        path = tmp_path / "graph.json"
        path.write_text(
            json.dumps(
                {
                    "version": "1.0",
                    "policy": {"accepted_validation_statuses": ["nonsense"]},
                    "nodes": [{"competency_id": "A", "node_type": "sub_competency"}],
                    "edges": [],
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(CompetencyGraphValidationError, match="graph.json"):
            load_and_validate_graph(path)


class TestShippedBanks:
    """What the banks in the registry actually resolve to, as shipped."""

    def test_the_ai_engineer_bank_is_inert_even_with_the_deployment_switched_on(self):
        from cat_engine.engine.services.orchestrator import registry

        graph = registry.get_graph_service("AIE")
        resolved = resolve_policy(
            graph.graph, deployment_inference=True, deployment_blocking=True
        )
        assert resolved.summary()["inference_enabled"] == 0
        assert resolved.summary()["blocking_enabled"] == 0
        # The bank turns propagation off at the BANK level, so a deployment that enables
        # it globally still does not enable it here. That is the point of the level
        # existing: one bank's edge set being validated says nothing about another's.
        reasons = resolved.summary()["inert_because"]
        assert any("bank policy" in reason for reason in reasons)
