"""bank-registry over HTTP: the two read paths, and the write path that is new.

WHAT IS WORTH TESTING HERE RATHER THAN IN THE ENGINE SUITE

`backend/tests/test_bank_store.py` already proves the validation rules. What only exists at
this layer is whether the HTTP surface preserves the properties the store has:

    that the ranking endpoint cannot leak a question
    that a refused bank is a 422 with the reason in it, not a 500
    that a POST cannot overwrite and a PUT can
    that turning the write path off leaves the read path working
"""

from __future__ import annotations

import pytest

from conftest import bank_store_at


def item(item_id: str, variable: str, *, a: float = 1.0) -> dict:
    return {
        "item_id": item_id,
        "modality": "mcq",
        "status": "active",
        "competency": "Example",
        "sub_competency": variable,
        "measures": [{"variable": variable, "weight": 1.0}],
        "cat": {"a": a, "b": 0.0, "c": 0.25},
        "payload": {"stem": "2 + 2?", "options": ["3", "4"], "answer_index": 1},
    }


def graph(*variables: str, main: str = "X") -> dict:
    return {
        "schema_version": "1.0",
        "nodes": [{"competency_id": main, "title": main, "node_type": "main"}]
        + [
            {
                "competency_id": v,
                "title": v,
                "node_type": "sub_competency",
                "main_competencies": [main],
                "critical": True,
            }
            for v in variables
        ],
        # `from`/`to`, exactly as a checked-in graph file spells them. Posting the same
        # JSON an author would commit is the point of the aliases on `GraphEdgeDTO`.
        "edges": [
            {
                "from": v,
                "to": main,
                "relation": "CONTRIBUTES_TO",
                "weight": round(1.0 / len(variables), 4),
            }
            for v in variables
        ],
    }


def submission(bank_id: str = "TEST", *variables: str) -> dict:
    variables = variables or ("X.1",)
    return {
        "bank_id": bank_id,
        "title": f"{bank_id} bank",
        "items": [item(f"q{n}", v) for n, v in enumerate(variables)],
        "graph": graph(*variables),
        "coverage_critical_only": False,
    }


@pytest.fixture()
def registry_client(client_factory, tmp_path):
    """A bank-registry whose write path lands in a temporary directory.

    Without this a test that posts a bank writes into the checked-in data directory, and
    the next run of the engine suite discovers a sixth bank it knows nothing about.
    """
    with bank_store_at(tmp_path / "banks"):
        with client_factory("bank-registry") as client:
            yield client


class TestTheRankingViewCannotLeakAQuestion:
    """ADR-0001's second boundary, asserted on the wire rather than on a type.

    The orchestrator ranks the whole eligible pool on every step. If this endpoint carried
    payloads, the service that must never read a question would receive every one of them
    on every decision.
    """

    def test_items_carry_parameters_and_nothing_else(self, registry_client):
        items = registry_client.get("/banks/DA/items").json()
        assert items
        for entry in items:
            assert set(entry) == {
                "item_id",
                "modality",
                "measures",
                "cat",
                "estimated_time_seconds",
                "minimum_success_confidence",
            }

    def test_no_stem_option_or_answer_appears_anywhere_in_the_response(
        self, registry_client
    ):
        """Asserted on the serialised body, so a future field that happens to embed a
        payload fails here rather than in a candidate's browser."""
        body = registry_client.get("/banks/DA/items").text
        for forbidden in ("answer_index", "stem", "options", "reference_solution", "tests"):
            assert forbidden not in body

    def test_the_rendering_view_does_carry_the_payload(self, registry_client):
        first = registry_client.get("/banks/DA/items").json()[0]["item_id"]
        full = registry_client.get(f"/banks/DA/items/{first}").json()
        assert full["payload"]
        assert full["item_id"] == first

    def test_an_unknown_item_is_a_404_naming_the_bank(self, registry_client):
        response = registry_client.get("/banks/DA/items/not-an-item")
        assert response.status_code == 404
        assert response.json()["code"] == "item_unknown"


class TestVersionsAreServedSoACacheCanBeKeyedOnThem:
    def test_the_item_list_carries_an_etag(self, registry_client):
        assert registry_client.get("/banks/DA/items").headers["etag"]

    def test_the_etag_matches_the_version_in_the_catalogue(self, registry_client):
        version = registry_client.get("/banks/DA").json()["version"]
        assert registry_client.get("/banks/DA/items").headers["etag"] == f'"{version}"'

    def test_distinct_banks_have_distinct_versions(self, registry_client):
        banks = {b["bank_id"]: b["version"] for b in registry_client.get("/banks").json()}
        assert len(set(banks.values())) == len(banks)


class TestTheCatalogue:
    def test_it_lists_the_checked_in_banks_as_seeds(self, registry_client):
        banks = registry_client.get("/banks").json()
        assert {b["bank_id"] for b in banks} == {
            "DA",
            "PY",
            "AIE",
            "AIE-JR-V3",
            "JAI-600",
        }
        assert all(b["source"] == "seed" for b in banks)

    def test_an_unknown_bank_is_a_404_that_names_the_valid_ones(self, registry_client):
        response = registry_client.get("/banks/nope/items")
        assert response.status_code == 404
        assert "AIE" in response.json()["detail"]


class TestTheGraphAndPolicyViews:
    def test_the_graph_is_served_with_nodes_and_edges(self, registry_client):
        graph_body = registry_client.get("/banks/AIE/graph").json()
        assert len(graph_body["nodes"]) > 30
        assert graph_body["edges"]

    def test_the_policy_says_why_each_edge_is_inert(self, registry_client):
        """The AI Engineer graph declares thirty prerequisite edges and every one is
        marked unvalidated, so none may infer or block. An operator asking why should get
        the answer here rather than by reading three files."""
        policy = registry_client.get("/banks/AIE/policy").json()
        assert policy["prerequisite_edges"] == 30
        assert policy["inference_enabled"] == 0
        assert policy["blocking_enabled"] == 0
        assert policy["inert_because"]
        assert all(not e["inference_allowed"] for e in policy["edges"])

    def test_a_bank_without_a_graph_says_so_rather_than_erroring(self, registry_client):
        registry_client.post(
            "/banks",
            json={**submission("NOGRAPH"), "graph": None},
        )
        response = registry_client.get("/banks/NOGRAPH/graph")
        assert response.status_code == 404
        assert response.json()["code"] == "graph_absent"


class TestRegisteringABank:
    def test_a_valid_bank_is_accepted_and_becomes_assessable(self, registry_client):
        response = registry_client.post("/banks", json=submission("NEW"))
        assert response.status_code == 201, response.text
        report = response.json()
        assert report["accepted"] is True
        assert report["mains"] == ["X"]
        assert report["version"]

        assert registry_client.get("/banks/NEW").json()["source"] == "stored"
        assert len(registry_client.get("/banks/NEW/items").json()) == 1
        assert registry_client.get("/banks/NEW/graph").json()["nodes"]

    def test_the_posted_bank_appears_in_the_catalogue_beside_the_seeds(
        self, registry_client
    ):
        registry_client.post("/banks", json=submission("NEW"))
        banks = {b["bank_id"]: b["source"] for b in registry_client.get("/banks").json()}
        assert banks["NEW"] == "stored"
        assert banks["DA"] == "seed"

    def test_a_post_cannot_overwrite_an_existing_bank(self, registry_client):
        registry_client.post("/banks", json=submission("NEW"))
        again = registry_client.post("/banks", json=submission("NEW"))
        assert again.status_code == 409
        assert again.json()["code"] == "bank_exists"

    def test_a_post_cannot_overwrite_a_checked_in_bank_either(self, registry_client):
        response = registry_client.post("/banks", json=submission("DA"))
        assert response.status_code == 409

    def test_a_put_replaces_and_moves_the_version(self, registry_client):
        registry_client.post("/banks", json=submission("NEW"))
        first = registry_client.get("/banks/NEW").json()["version"]

        bigger = submission("NEW")
        bigger["items"].append(item("q9", "X.1"))
        response = registry_client.put("/banks/NEW", json=bigger)
        assert response.status_code == 200, response.text
        assert registry_client.get("/banks/NEW").json()["version"] != first
        assert len(registry_client.get("/banks/NEW/items").json()) == 2

    def test_a_put_whose_body_names_a_different_bank_is_refused(self, registry_client):
        response = registry_client.put("/banks/NEW", json=submission("OTHER"))
        assert response.status_code == 409
        assert response.json()["code"] == "bank_id_mismatch"

    def test_deleting_a_shadow_restores_the_checked_in_bank(self, registry_client):
        registry_client.put("/banks/DA", json=submission("DA"))
        assert registry_client.get("/banks/DA").json()["source"] == "stored"
        assert len(registry_client.get("/banks/DA/items").json()) == 1

        response = registry_client.delete("/banks/DA")
        assert response.json() == {"deleted": "DA", "seed_restored": True}
        assert registry_client.get("/banks/DA").json()["source"] == "seed"
        assert len(registry_client.get("/banks/DA/items").json()) > 1

    def test_a_checked_in_bank_cannot_be_deleted_outright(self, registry_client):
        response = registry_client.delete("/banks/AIE")
        assert response.status_code == 404
        assert response.json()["code"] == "bank_not_stored"


class TestARefusedBankSaysWhy:
    """A 422 with the reason beats a 500, and beats a 201 that stores something unusable."""

    def test_an_out_of_range_discrimination_is_refused_by_the_schema(
        self, registry_client
    ):
        body = submission("BAD")
        body["items"][0]["cat"]["a"] = 12.0
        response = registry_client.post("/banks", json=body)
        assert response.status_code == 422
        assert response.json()["code"] == "request_invalid"

    def test_a_graph_that_pairs_with_a_different_bank_is_refused_with_the_reason(
        self, registry_client
    ):
        body = submission("BAD")
        body["graph"] = graph("Y.1", main="Y")
        response = registry_client.post("/banks", json=body)
        assert response.status_code == 422
        assert "graph_bank_mismatch" in response.json()["detail"]

    def test_a_bank_id_that_could_escape_the_store_is_refused(self, registry_client):
        response = registry_client.post("/banks", json=submission("../escape"))
        assert response.status_code == 422

    def test_a_refused_bank_is_not_registered(self, registry_client):
        body = submission("BAD")
        body["graph"] = graph("Y.1", main="Y")
        registry_client.post("/banks", json=body)
        assert registry_client.get("/banks/BAD").status_code == 404

    def test_validate_checks_without_writing(self, registry_client):
        report = registry_client.post("/banks/validate", json=submission("DRYRUN")).json()
        assert report["accepted"] is True
        assert registry_client.get("/banks/DRYRUN").status_code == 404

    def test_validate_reports_findings_for_a_bank_it_would_refuse(self, registry_client):
        body = submission("DRYRUN")
        body["graph"] = graph("X.1", "X.2")  # X.2 required, nothing measures it
        report = registry_client.post("/banks/validate", json=body).json()
        assert report["accepted"] is False
        assert any(f["code"] == "required_node_unmeasured" for f in report["findings"])

    def test_a_bank_with_no_graph_is_accepted_with_a_warning(self, registry_client):
        body = {**submission("NOGRAPH2"), "graph": None}
        report = registry_client.post("/banks", json=body).json()
        assert report["accepted"] is True
        assert any(f["code"] == "no_graph" for f in report["findings"])


class TestTheWritePathCanBeTurnedOff:
    """A read-only registry and one that can replace a live bank are different
    propositions. There is no authentication here — see ADR-0002 — so a deployment that
    does not need the write path should be able to not have it."""

    @pytest.fixture()
    def read_only(self, client_factory, tmp_path, monkeypatch):
        monkeypatch.setenv("ADMIN_API_ENABLED", "false")
        with bank_store_at(tmp_path / "banks"):
            with client_factory("bank-registry") as client:
                yield client

    def test_writes_are_refused_with_a_reason(self, read_only):
        for call in (
            lambda: read_only.post("/banks", json=submission("X")),
            lambda: read_only.put("/banks/X", json=submission("X")),
            lambda: read_only.delete("/banks/X"),
            lambda: read_only.post("/banks/validate", json=submission("X")),
        ):
            response = call()
            assert response.status_code == 503
            assert response.json()["code"] == "admin_api_disabled"

    def test_the_read_path_still_works(self, read_only):
        assert read_only.get("/banks").status_code == 200
        assert read_only.get("/banks/DA/items").status_code == 200

    def test_health_reports_that_it_is_off(self, read_only):
        assert read_only.get("/health").json()["detail"]["admin_api_enabled"] is False
