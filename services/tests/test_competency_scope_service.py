"""competency-scope over HTTP.

THE TEST THAT MATTERS HERE IS `TestAScopeOverEverythingChangesNothing`.

Every other test checks that a narrowing does what its route says. That one checks the
property the whole mechanism rests on: a scope naming every competency has to produce
exactly the bank — every active item, no partial main, the same coverage requirement. If it
does not, then scoping is not a restriction of the existing behaviour but a second,
slightly different assessment, and every number measured against the unscoped engine stops
applying to a scoped session.

WHAT A SCOPE MUST NOT BE ABLE TO DO

Narrow a pool, and nothing else. There is no score, no weight and no ability estimate
anywhere in `ScopeManifest`, so a compromised or buggy scope service can change which
questions are asked and what coverage requires — both of which are visible in the report —
and cannot change any number. `TestNothingInAScopeCanCarryEvidence` asserts that
structurally rather than by inspection, the same way the graph service's boundary is
asserted.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from conftest import asgi_client, load_services

#: AIE, because it is the only checked-in bank with shared sub-competencies, both edge
#: kinds, and a main whose sub-competency count exceeds the question budget. Every
#: interesting case below is a real property of a real bank rather than a fixture.
BANK = "AIE"


@pytest.fixture()
def stack():
    """competency-scope with a real bank-registry behind it."""
    from adaptive_clients import BankRegistryClient

    with load_services("bank-registry", "competency-scope") as modules:
        registry_main = modules["bank-registry"]
        scope_main = modules["competency-scope"]
        scope_main._bank = BankRegistryClient(http=asgi_client(registry_main.app))
        with TestClient(scope_main.app) as http:
            yield http, scope_main


@pytest.fixture()
def api(stack):
    return stack[0]


def scope(api, selected, **overrides) -> dict:
    body = {"bank_id": BANK, "selected": selected, **overrides}
    response = api.post("/scopes", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def active_item_ids(bank_id: str = BANK) -> set[str]:
    from cat_engine.engine.services.orchestrator import registry

    return {
        item.item_id
        for item in registry.get_bank(bank_id).all_items()
        if item.status == "active"
    }


class TestAScopeOverEverythingChangesNothing:
    """The property that makes every other number still apply."""

    def test_it_returns_every_active_item(self, api):
        manifest = scope(api, ["C1", "C3", "C6"])
        assert set(manifest["item_ids"]) == active_item_ids()

    def test_no_main_is_partial(self, api):
        manifest = scope(api, ["C1", "C3", "C6"])
        assert [row["main"] for row in manifest["mains"]] == ["C1", "C3", "C6"]
        assert not any(row["partial"] for row in manifest["mains"])
        assert all(row["retained_weight"] == 1.0 for row in manifest["mains"])

    def test_no_prerequisite_edge_is_severed(self, api):
        manifest = scope(api, ["C1", "C3", "C6"])
        assert manifest["dangling_prerequisites"] == []

    def test_the_coverage_requirement_is_the_banks_own(self, api):
        """Against the engine's own answer, not against a number copied into this file."""
        from cat_engine.engine.services.competency_graph.coverage import sub_nodes_for_main
        from cat_engine.engine.services.orchestrator import registry

        manifest = scope(api, ["C1", "C3", "C6"])
        graph = registry.get_graph_service(BANK)
        critical_only = manifest["coverage"]["critical_only_applied"]
        for row in manifest["mains"]:
            expected = sub_nodes_for_main(
                graph, row["main"], critical_only=critical_only
            )
            assert set(row["required"]) == set(expected)


class TestNarrowingToSubCompetencies:
    def test_only_items_measuring_the_selection_survive(self, api):
        manifest = scope(api, ["C1.1", "C1.4"])
        retained = {node["node_id"] for node in manifest["nodes"]}
        assert {"C1.1", "C1.4", "C1"} == retained

        from cat_engine.engine.services.orchestrator import registry

        bank = registry.get_bank(BANK)
        for item_id in manifest["item_ids"]:
            item = bank.get(item_id)
            assert {m.variable for m in item.measures} & {"C1.1", "C1.4"}

    def test_the_main_is_reported_partial_with_the_mass_it_kept(self, api):
        manifest = scope(api, ["C1.1", "C1.4"])
        row = next(r for r in manifest["mains"] if r["main"] == "C1")
        assert row["partial"] is True
        # Two of C1's seven children, each authored at 0.1429.
        assert row["retained_weight"] == pytest.approx(0.2858, abs=1e-4)
        assert sorted(row["required"]) == ["C1.1", "C1.4"]
        assert "C1.2" in row["excluded"]

    def test_contributes_to_is_renormalised_and_the_original_kept(self, api):
        manifest = scope(api, ["C1.1", "C1.4"])
        edges = [
            e
            for e in manifest["edges"]
            if e["relation"] == "CONTRIBUTES_TO" and e["to"] == "C1"
        ]
        assert len(edges) == 2
        assert sum(e["weight"] for e in edges) == pytest.approx(1.0)
        # The change has to stay inspectable: renormalising alters what a main's declared
        # mass MEANS, and a change nobody can see is a change nobody can review.
        assert all(e["weight_original"] == pytest.approx(0.1429) for e in edges)

    def test_the_coverage_requirement_shrinks_with_the_scope(self, api):
        whole = scope(api, ["C1"])["coverage"]["required_by_main"]["C1"]
        narrow = scope(api, ["C1.1", "C1.4"])["coverage"]["required_by_main"]["C1"]
        assert narrow < whole


class TestTheAllowlistRespectsTheBank:
    def test_a_retired_item_never_enters_a_scope(self):
        """`JAI-600` retires 64 code items whose hidden tests are incomplete.

        The engine's `shortlist` refuses them; a scope that admitted them would put an
        item the bank knows is broken back into the pool through a different door.
        """
        from adaptive_clients import BankRegistryClient

        with load_services("bank-registry", "competency-scope") as modules:
            modules["competency-scope"]._bank = BankRegistryClient(
                http=asgi_client(modules["bank-registry"].app)
            )
            with TestClient(modules["competency-scope"].app) as http:
                manifest = http.post(
                    "/scopes",
                    json={"bank_id": "JAI-600", "selected": ["C1"]},
                ).json()

        from cat_engine.engine.services.orchestrator import registry

        bank = registry.get_bank("JAI-600")
        retired = {i.item_id for i in bank.all_items() if i.status != "active"}
        assert retired, "this bank is supposed to carry retired items"
        assert not retired & set(manifest["item_ids"])


class TestSharedNodesWidenAScope:
    """C1.6 serves both C1 and C6; C3.9 and C3.10 serve both C3 and C6."""

    def test_selecting_one_main_opens_the_mains_its_shared_nodes_serve(self, api):
        manifest = scope(api, ["C6"])
        opened = {row["main"] for row in manifest["mains"]}
        assert opened == {"C1", "C3", "C6"}

    def test_an_opened_main_nobody_asked_for_says_so(self, api):
        """The main HAS to open — `rollup_outcomes` would otherwise discard a C1.6 outcome
        because C1 is not in the session — but one or two shared items is a thin estimate,
        and a caller is entitled to tell it from a competency they chose."""
        manifest = scope(api, ["C6"])
        by_main = {row["main"]: row for row in manifest["mains"]}
        assert by_main["C6"]["implied"] is False
        assert by_main["C1"]["implied"] is True
        assert by_main["C3"]["implied"] is True

    def test_naming_a_sub_competency_of_a_main_makes_it_chosen(self, api):
        manifest = scope(api, ["C1.1", "C6"])
        by_main = {row["main"]: row for row in manifest["mains"]}
        assert by_main["C1"]["implied"] is False
        assert by_main["C3"]["implied"] is True


class TestPrerequisiteEdgesAtTheBoundary:
    def test_induction_is_strict_by_default(self, api):
        """C1.1 -> C1.2 -> C1.3 -> C1.4 is a chain. Selecting C1.4 alone keeps none of it.

        Strict is right precisely BECAUSE those edges ship inert: a screening study
        measured upward inference wrong 22.4% of the time against a 3% gate, so enlarging
        an assessment on their authority is the one use they were found unfit for.
        """
        manifest = scope(api, ["C1.4"])
        assert {n["node_id"] for n in manifest["nodes"]} == {"C1.4", "C1"}

    def test_a_severed_edge_is_reported_rather_than_dropped(self, api):
        manifest = scope(api, ["C1.4"])
        severed = {
            (d["from"], d["to"], d["direction"])
            for d in manifest["dangling_prerequisites"]
        }
        assert ("C1.3", "C1.4", "enters") in severed
        assert ("C1.4", "C1.5", "leaves") in severed

    def test_the_closure_is_opt_in(self, api):
        manifest = scope(api, ["C1.4"], include_prerequisites=True)
        nodes = {n["node_id"] for n in manifest["nodes"]}
        assert {"C1.1", "C1.2", "C1.3", "C1.4"} <= nodes


class TestASelectionThatCannotBeAssessed:
    def test_an_unknown_competency_is_rejected_not_raised(self, api):
        """A picker needs to know WHICH of the five things the user chose is the problem."""
        manifest = scope(api, ["C1.1", "NOT-A-NODE"])
        assert manifest["rejected"] == ["NOT-A-NODE"]
        assert manifest["item_ids"], "the rest of the selection still stands"

    def test_a_requirement_larger_than_the_budget_names_the_main(self, api):
        """C6 declares nineteen sub-competencies against a twelve-question cap. With
        critical-only off the coverage gate could never be satisfied, and every session
        would end on the budget escape for reasons nobody could reconstruct."""
        manifest = scope(api, ["C6"], critical_only=False)
        assert manifest["coverage"]["reachable"] is False
        assert "C6" in manifest["coverage"]["over_budget"]

    def test_an_unassessable_scope_still_answers(self, api):
        """200 with the reason, not a refusal. The caller is building a selection and the
        useful answer is which competency to drop."""
        response = api.post(
            "/scopes",
            json={"bank_id": BANK, "selected": ["C6"], "critical_only": False},
        )
        assert response.status_code == 200


class TestTheScopeIdIsDeterministicAndVersionBound:
    def test_the_same_selection_gives_the_same_id(self, api):
        assert scope(api, ["C1.1", "C6"])["scope_id"] == scope(api, ["C6", "C1.1"])["scope_id"]

    def test_a_different_selection_gives_a_different_id(self, api):
        assert scope(api, ["C1.1"])["scope_id"] != scope(api, ["C1.2"])["scope_id"]

    def test_the_manifest_names_the_bank_version_it_was_built_against(self, api):
        from cat_engine.engine.services.orchestrator import registry

        manifest = scope(api, ["C1"])
        assert manifest["bank_version"] == registry.version(BANK)


class TestNothingInAScopeCanCarryEvidence:
    """Structural, not by inspection — the same argument as `InferredSignalDTO`.

    A scope decides which questions may be asked. If it could also carry a score or a
    weight, a buggy scope service would be able to move a candidate's estimate, and the
    reason this service is safe to run at all would stop being true.
    """

    def test_the_manifest_declares_no_measurement_field(self):
        from cat_engine.contracts import ScopeManifest, ScopeMainDTO, ScopeNodeDTO

        forbidden = {"score", "weight", "theta", "theta_hat", "standard_error", "posterior"}
        for model in (ScopeManifest, ScopeMainDTO, ScopeNodeDTO):
            assert not forbidden & set(model.model_fields), model.__name__

    def test_a_payload_claiming_a_score_cannot_smuggle_one_through(self):
        from cat_engine.contracts import ScopeNodeDTO

        node = ScopeNodeDTO.model_validate(
            {"node_id": "C1.1", "score": 1.0, "weight": 1.0}
        )
        assert not hasattr(node, "score")
        assert not hasattr(node, "weight")

    def test_retained_weight_is_a_coverage_statement_not_a_loading(self, api):
        """It is the share of a main's AUTHORED CONTRIBUTES_TO mass that survived — and
        those weights are inert in the engine today, which is why renormalising them
        changes no estimate. It belongs to the report, not to the likelihood."""
        manifest = scope(api, ["C1.1", "C1.4"])
        row = next(r for r in manifest["mains"] if r["main"] == "C1")
        assert 0.0 < row["retained_weight"] < 1.0
        assert "score" not in row


class TestTheScopeCanBeDrawn:
    def test_it_renders_mermaid(self, api):
        response = api.post(
            "/scopes/graph", json={"bank_id": BANK, "selected": ["C1.1", "C1.4"]}
        )
        assert response.status_code == 200
        body = response.text
        assert body.startswith("flowchart BT")
        assert "C1.1" in body and "C1.4" in body

    def test_the_excluded_siblings_are_drawn_too(self, api):
        """A picture of only the retained nodes makes two-of-seven look like a whole
        competency."""
        body = api.post(
            "/scopes/graph", json={"bank_id": BANK, "selected": ["C1.1", "C1.4"]}
        ).text
        assert "C1.2 — excluded" in body


class TestOperatorSurface:
    def test_health_reports_the_question_budget_it_gates_on(self, api):
        health = api.get("/health").json()
        assert health["service"] == "competency-scope"
        assert health["detail"]["stateful"] is False
        assert health["detail"]["question_budget"] > 0

    def test_a_bank_without_a_graph_is_refused_rather_than_widened(self, api):
        """Without a graph there are no sub-competency nodes to select, so a scope would be
        an allowlist with nothing behind it — and silently returning the whole bank is not
        what the caller asked for."""
        response = api.post("/scopes", json={"bank_id": "nope", "selected": ["C1"]})
        assert response.status_code == 404
