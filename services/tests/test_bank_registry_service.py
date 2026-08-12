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
                # `status` is a SELECTION fact, not a payload: it says whether this item
                # may be administered at all. Omitting it was a defect — `BankItemRef`
                # defaults it to "active" exactly as `BankItem` does, so a retired item
                # came back over the wire indistinguishable from a live one.
                "status",
            }

    def test_a_retired_item_says_so(self, registry_client):
        """The HTTP path must refuse the same items the in-process engine refuses.

        `JAI-600` retires 64 code items as `inactive_missing_hidden_tests` — their test
        suite is incomplete, so grading against them scores a candidate on a question the
        bank knows is broken. Before `status` travelled, the orchestrator reconstructed
        every one of them as active and ranked them.
        """
        items = registry_client.get("/banks/JAI-600/items").json()
        retired = [i for i in items if i["status"] != "active"]
        assert retired, "this bank is supposed to carry retired items"
        assert all(i["status"] == "inactive_missing_hidden_tests" for i in retired)

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
        """Built through the store rather than over HTTP, because no endpoint can produce
        one any more: the write path is retired and `bank-ingest` always derives a graph. A
        graphless bank is now only reachable by deploying a seed configured without one, and
        the endpoint still has to answer for it."""
        from cat_engine.engine.services.orchestrator import registry
        from cat_engine.engine.services.orchestrator.bank_store import Validation

        items = [
            {
                "item_id": "n1",
                "modality": "mcq",
                "measures": [{"variable": "N.1", "weight": 1.0}],
                "cat": {"a": 1.0, "b": 0.0, "c": 0.25},
                "mcq": {"stem": "?", "options": ["a", "b"], "answer_index": 1},
            }
        ]
        registry.STORE.save(
            bank_id="NOGRAPH",
            title="no graph",
            items=items,
            graph=None,
            coverage_critical_only=None,
            validation=Validation(bank_id="NOGRAPH", items=1, mains=["N"]),
        )
        registry.reset_caches()

        response = registry_client.get("/banks/NOGRAPH/graph")
        assert response.status_code == 404
        assert response.json()["code"] == "graph_absent"


class TestTheWritePathIsGone:
    """Writing a bank moved to `bank-ingest`. These four endpoints are what it replaced.

    410 rather than 404, and the routes kept rather than deleted, because the difference is
    what a reader does next. A 404 says "wrong URL" and sends somebody hunting for a typo; a
    410 naming the replacement is the only thing a client integrated against the old path
    will actually read.

    The behaviour these used to assert has not been lost — it moved to
    `test_bank_ingest_service.py`, against the endpoint that now owns it.
    """

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("post", "/banks/validate"),
            ("post", "/banks"),
            ("put", "/banks/DA"),
            ("delete", "/banks/DA"),
        ],
    )
    def test_every_old_write_endpoint_is_gone(self, registry_client, method, path):
        call = getattr(registry_client, method)
        response = call(path) if method == "delete" else call(path, json={})
        assert response.status_code == 410
        assert response.json()["code"] == "write_path_moved"

    def test_the_refusal_says_where_writing_went(self, registry_client):
        detail = registry_client.post("/banks", json={}).json()["detail"]
        assert "bank-ingest" in detail
        assert "PUT /banks/{bank_id}" in detail

    def test_the_read_paths_are_untouched(self, registry_client):
        assert registry_client.get("/banks").status_code == 200
        assert registry_client.get("/banks/DA/items").status_code == 200
        assert registry_client.get("/banks/DA/graph").status_code == 200

    def test_a_checked_in_bank_survives_a_refused_write(self, registry_client):
        """The endpoint refusing is not the same as the endpoint doing nothing."""
        before = registry_client.get("/banks/DA").json()["version"]
        registry_client.put("/banks/DA", json={"bank_id": "DA", "items": []})
        assert registry_client.get("/banks/DA").json()["version"] == before
