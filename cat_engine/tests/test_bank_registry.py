"""Invariants that must hold for EVERY registered bank.

These are the tests that fail when someone adds a bank, a graph, or a sub-competency
without checking the arithmetic. Behaviour tests belong beside their own bank.
"""

from __future__ import annotations

import pytest

from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.competency_graph.coverage import sub_nodes_for_main
from cat_engine.engine.services.orchestrator import registry


def test_every_registered_bank_loads_and_yields_items(any_profile) -> None:
    bank = registry.get_bank(any_profile.bank_id)
    items = bank.all_items()

    assert items, f"{any_profile.bank_id} has no items"
    assert bank.variables(), f"{any_profile.bank_id} measures nothing"


def test_the_declared_mains_match_what_the_bank_actually_measures(any_profile) -> None:
    """The profile row is a claim about the file; a silent content change breaks it."""
    if not any_profile.mains:
        pytest.skip(f"{any_profile.bank_id} declares no mains")

    assert set(registry.get_bank(any_profile.bank_id).variables()) == set(any_profile.mains)


def test_a_bank_is_parsed_once_and_shared(any_profile) -> None:
    bank_id = any_profile.bank_id
    assert registry.get_bank(bank_id) is registry.get_bank(bank_id)
    assert registry.get_graph_service(bank_id) is registry.get_graph_service(bank_id)


def test_distinct_banks_are_distinct_objects() -> None:
    assert registry.get_bank("DA") is not registry.get_bank("AIE")


def test_the_default_resolves_to_a_registered_bank() -> None:
    assert registry.resolve_bank_id(None) == settings.active_bank
    assert settings.active_bank in registry.REGISTRY


def test_an_unknown_bank_names_the_valid_ones() -> None:
    with pytest.raises(KeyError, match="AIE"):
        registry.resolve_bank_id("not-a-bank")


def test_every_graph_node_id_the_bank_measures_exists_in_its_graph(any_profile) -> None:
    """A measured variable with no node is invisible to coverage and to blocking."""
    graph = registry.get_graph_service(any_profile.bank_id)
    if graph is None:
        pytest.skip(f"{any_profile.bank_id} declares no graph")

    bank = registry.get_bank(any_profile.bank_id)
    measured = {m.variable for item in bank.all_items() for m in item.measures}
    missing = sorted(measured - set(graph.graph.nodes))

    assert not missing, f"{any_profile.bank_id}: measured but absent from the graph: {missing}"


def test_the_coverage_requirement_is_reachable_within_the_question_budget(
    any_profile,
) -> None:
    """The guard the AI Engineer bank needed.

    Every AIE item measures exactly one sub-competency, so covering R required nodes costs
    R questions. C6 declares sixteen; the cap is twelve. Without a per-bank coverage policy
    the gate could never be satisfied and every C6 session would end on the budget escape,
    reporting a stop it never actually chose.

    The two-question margin leaves room for the corroboration item that a precision stop
    demands on top of coverage.
    """
    graph = registry.get_graph_service(any_profile.bank_id)
    if graph is None:
        pytest.skip(f"{any_profile.bank_id} declares no graph")

    critical_only = registry.coverage_policy(any_profile.bank_id)
    budget = settings.cat_max_questions

    for main in registry.get_bank(any_profile.bank_id).variables():
        required = sub_nodes_for_main(graph, main, critical_only=critical_only)
        assert len(required) <= budget - 2, (
            f"{any_profile.bank_id}/{main}: {len(required)} required sub-competencies "
            f"({'critical only' if critical_only else 'all'}) against a "
            f"{budget}-question cap — the coverage gate can never be satisfied"
        )


def test_every_required_coverage_node_has_at_least_one_item(any_profile) -> None:
    """A required node with no item is an unsatisfiable gate by another route."""
    graph = registry.get_graph_service(any_profile.bank_id)
    if graph is None:
        pytest.skip(f"{any_profile.bank_id} declares no graph")

    bank = registry.get_bank(any_profile.bank_id)
    measured = {
        m.variable
        for item in bank.all_items()
        if item.status == "active"
        for m in item.measures
    }
    critical_only = registry.coverage_policy(any_profile.bank_id)

    for main in bank.variables():
        for node in sub_nodes_for_main(graph, main, critical_only=critical_only):
            assert node in measured, (
                f"{any_profile.bank_id}: {node} is required for coverage of {main} "
                "but no active item measures it"
            )


class TestAIEBank:
    def test_it_carries_three_mains_and_thirty_three_sub_competencies(self, aie_bank) -> None:
        assert aie_bank.variables() == ["C1", "C3", "C6"]
        measured = {m.variable for i in aie_bank.all_items() for m in i.measures}
        assert len(measured) == 33

    def test_it_offers_all_three_modalities_under_every_main(self, aie_bank) -> None:
        coverage = aie_bank.coverage()
        for main in ("C1", "C3", "C6"):
            assert set(coverage[main]) == {"mcq", "code", "voice"}

    def test_code_and_voice_items_carry_an_authored_time_estimate(self, aie_bank) -> None:
        """Information per minute divides by this; a default would hide a 900s item."""
        for item in aie_bank.all_items():
            if item.modality in ("code", "voice"):
                assert item.estimated_time_seconds is not None, item.item_id
                assert item.expected_seconds == item.estimated_time_seconds

    def test_every_prerequisite_edge_is_inert_and_marked_unvalidated(self, aie_graph) -> None:
        """The edges encode a numbering order, not measured dependency.

        Enabling one is `scripts/validate_prerequisite_edges.py --apply`'s decision, made
        against real response data. Until then they must not be able to infer or block.
        """
        prerequisites = [e for e in aie_graph.graph.edges if e.relation == "PREREQUISITE"]
        assert len(prerequisites) == 30

        for edge in prerequisites:
            assert not edge.allow_upward_inference, f"{edge.from_id}->{edge.to_id}"
            assert not edge.allow_downward_blocking, f"{edge.from_id}->{edge.to_id}"
            assert edge.metadata.get("validation_status") == "unvalidated"

    def test_contributions_into_each_main_sum_to_one(self, aie_graph) -> None:
        totals: dict[str, float] = {}
        for edge in aie_graph.graph.edges:
            if edge.relation == "CONTRIBUTES_TO":
                totals[edge.to_id] = totals.get(edge.to_id, 0.0) + edge.weight

        assert set(totals) == {"C1", "C3", "C6"}
        for main, total in totals.items():
            assert total == pytest.approx(1.0), f"{main} contributions sum to {total}"
