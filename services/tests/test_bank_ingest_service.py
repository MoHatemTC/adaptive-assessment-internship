"""bank-ingest: one uploaded file becomes a registered bank.

THE TESTS THAT MATTER HERE ARE IN `TestTheDerivedGraph`.

An author uploads questions and never a graph, so one is derived on their behalf — and a
derivation nobody checks is a modelling decision applied to candidates in the dark. Those
tests assert the derivation against the five checked-in banks rather than against a fixture,
because a fixture proves only that the code does what the fixture says.

`TestAnUploadedBankIsAssessable` is the end of the chain: upload questions, get a bank, run
an assessment against it, with no restart and no rebuild.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from conftest import bank_store_at, load_service, load_services

DATA = "backend/app/data"


def bank_bytes(name: str) -> bytes:
    from app.config.paths import DATA_DIR

    return (DATA_DIR / name).read_bytes()


def upload(api, bank_id: str, raw: bytes, *, dry_run: bool = False):
    """The one write endpoint. Register and replace are the same call, by design."""
    return api.put(
        f"/banks/{bank_id}?dry_run={str(dry_run).lower()}",
        files={"file": ("bank.json", raw, "application/json")},
    )


@pytest.fixture()
def derive(tmp_path):
    """The derivation module, with its service directory on the path.

    Every service here names its package `service`, so the module is only importable inside
    `load_service` — the same reason `conftest` loads them one at a time.
    """
    with bank_store_at(tmp_path / "banks"):
        with load_service("bank-ingest"):
            import service.derive as module

            yield module


@pytest.fixture()
def api(tmp_path):
    with bank_store_at(tmp_path / "banks"):
        with load_service("bank-ingest") as main:
            main.UPLOADS.clear()
            with TestClient(main.app) as http:
                yield http


class TestTheDerivedGraph:
    """Asserted against the real banks. An author uploads questions; this is what they get."""

    @pytest.mark.parametrize(
        "filename",
        [
            "question_bank.json",
            "question_bank_AIE.json",
            "question_bank_AIE_JR_v3.json",
            "question_bank_JAI_2026_600.json",
            "question_bank_PY_20260803_083616.json",
        ],
    )
    def test_every_checked_in_bank_derives_a_graph_that_parses(self, filename, derive):
        """The derivation has to survive the engine's own graph validator — which refuses
        a prerequisite cycle, a dangling edge endpoint and an unknown relation."""
        from app.services.competency_graph.validator import parse_and_validate_graph

        raw = json.loads(bank_bytes(filename))
        items = raw["items"] if isinstance(raw, dict) else raw
        graph = derive.derive_graph(filename, items)
        parse_and_validate_graph(graph, source=filename)

    def test_contributes_to_mass_sums_to_one_per_main(self, derive):
        """The convention every authored graph in this repository follows, and which
        `competency-scope` reads to say how much of a main a scope retained."""
        raw = json.loads(bank_bytes("question_bank_AIE.json"))
        graph = derive.derive_graph("AIE", raw["items"])
        totals: dict[str, float] = {}
        for edge in graph["edges"]:
            if edge["relation"] == "CONTRIBUTES_TO" and "." not in edge["to"]:
                totals[edge["to"]] = totals.get(edge["to"], 0.0) + edge["weight"]
        assert totals
        for main, total in totals.items():
            assert total == pytest.approx(1.0, abs=1e-4), main

    def test_a_shared_node_is_not_derivable_and_the_graph_says_so(self, derive):
        """`C1.6` serves both C1 and C6 in the AUTHORED graph. Membership derived from the
        id prefix cannot express that, and pretending otherwise would be inventing a
        relationship the bank does not state."""
        raw = json.loads(bank_bytes("question_bank_AIE.json"))
        graph = derive.derive_graph("AIE", raw["items"])
        for node in graph["nodes"]:
            if node["node_type"] == "sub_competency":
                assert len(node["main_competencies"]) == 1
                assert node["shared"] is False

    def test_every_derived_prerequisite_edge_ships_inert(self, derive):
        """Co-measurement is weaker evidence than an expert's judgement, and the expert
        edges were themselves measured wrong 22.4% of the time against a 3% gate. A derived
        edge able to infer would be inverting that."""
        raw = json.loads(bank_bytes("question_bank_AIE_JR_v3.json"))
        graph = derive.derive_graph("AIE-JR", raw["items"])
        prerequisites = [e for e in graph["edges"] if e["relation"] == "PREREQUISITE"]
        assert prerequisites, "this bank multi-measures enough to produce some"
        for edge in prerequisites:
            assert edge["allow_upward_inference"] is False
            assert edge["allow_downward_blocking"] is False
            assert edge["metadata"]["validation_status"] == "unvalidated"

    def test_the_relation_threshold_is_what_decides_the_relation(self, derive):
        raw = json.loads(bank_bytes("question_bank_AIE_JR_v3.json"))
        items = raw["items"]
        wide_open = derive.derive_graph("b", items, relation_threshold=0.0, edge_floor=0.0)
        shut = derive.derive_graph("b", items, relation_threshold=1.01, edge_floor=0.0)

        def count(graph, relation):
            return sum(
                1
                for e in graph["edges"]
                if e["relation"] == relation and "." in e["to"]
            )

        assert count(wide_open, "PREREQUISITE") > 0
        assert count(shut, "PREREQUISITE") == 0
        assert count(shut, "CONTRIBUTES_TO") == count(wide_open, "PREREQUISITE")

    def test_only_one_direction_of_a_pair_survives(self, derive):
        """Co-measurement shares a numerator both ways and differs only in denominator, so
        A->B and B->A can both clear the threshold. Two prerequisite edges between one pair
        is a cycle, and `parse_and_validate_graph` refuses the whole bank for it — which
        would make every upload of a multi-measuring bank fail for a reason no author could
        act on."""
        raw = json.loads(bank_bytes("question_bank_AIE.json"))
        graph = derive.derive_graph("AIE", raw["items"], relation_threshold=0.0, edge_floor=0.0)
        pairs = {
            frozenset((e["from"], e["to"]))
            for e in graph["edges"]
            if "." in e["to"] and "." in e["from"]
        }
        directed = [e for e in graph["edges"] if "." in e["to"] and "." in e["from"]]
        assert len(directed) == len(pairs)

    def test_a_retired_item_declares_no_node(self, derive):
        """`JAI-600` retires 64 items. A node only a retired item measures would be a
        coverage requirement no servable question could ever satisfy — the gate would then
        veto convergence for the life of the bank."""
        raw = json.loads(bank_bytes("question_bank_JAI_2026_600.json"))
        items = raw["items"]
        derived = {
            n["competency_id"]
            for n in derive.derive_graph("JAI", items)["nodes"]
            if n["node_type"] == "sub_competency"
        }
        active_vars = {
            m["variable"]
            for i in items
            if i.get("status", "active") == "active"
            for m in i["measures"]
        }
        assert derived == {v for v in active_vars if "." in v}

    def test_main_rollup_agrees_with_the_engines_own(self, derive):
        """`main_of` is copied rather than imported, because the engine's copy is in a slice
        this service may not reach. Copied code that drifts is worse than no copy."""
        from app.services.orchestrator.competency import main_competency

        for variable in ("C1.1", "C1", "DA.10", "T1.4", "PY.2"):
            assert derive.main_of(variable) == main_competency(variable)


class TestUploadingABank:
    def test_one_file_becomes_a_registered_bank(self, api):
        response = upload(api, "UP1", bank_bytes("question_bank.json"))
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["status"] == "registered"
        assert receipt["version"]
        assert receipt["items"] == 34
        assert receipt["validation"]["accepted"] is True

    def test_the_receipt_shows_the_graph_the_author_did_not_upload(self, api):
        receipt = upload(api, "UP1", bank_bytes("question_bank.json")).json()
        derived = receipt["derived_graph"]
        assert derived["mains"] == ["DA"]
        assert len(derived["sub_competencies"]) == 6
        assert derived["prerequisite_edges_are_inert"] is True

    def test_a_bare_list_of_items_is_accepted(self, api):
        raw = json.loads(bank_bytes("question_bank.json"))
        response = upload(api, "UP2", json.dumps(raw["items"]).encode())
        assert response.status_code == 200, response.text

    def test_a_dry_run_writes_nothing(self, api):
        response = upload(api, "UP3", bank_bytes("question_bank.json"), dry_run=True)
        assert response.status_code == 200
        assert response.json()["validation"]["accepted"] is True
        assert response.json()["status"] == "validating"
        assert response.json()["derived_graph"], "the derived graph is the point of a dry run"

        from app.services.orchestrator import registry

        assert not registry.STORE.is_stored("UP3")

    def test_uploading_twice_is_idempotent_rather_than_a_conflict(self, api):
        """A bank is identified by its id and its content, so the same bytes under the same
        id are the same bank. Register and replace were separate verbs and a retried POST
        could fail for having succeeded; one PUT cannot."""
        first = upload(api, "UP4", bank_bytes("question_bank.json")).json()
        again = upload(api, "UP4", bank_bytes("question_bank.json")).json()
        assert again["status"] == "registered"
        assert again["version"] == first["version"], "identical bytes, identical hash"

    def test_uploading_different_content_moves_the_version(self, api):
        first = upload(api, "UP5", bank_bytes("question_bank.json")).json()
        raw = json.loads(bank_bytes("question_bank.json"))
        raw["items"][0]["cat"]["b"] = 1.75
        changed = upload(api, "UP5", json.dumps(raw).encode()).json()
        assert changed["version"] != first["version"]

    def test_deleting_an_uploaded_bank_forgets_it(self, api):
        upload(api, "UP6", bank_bytes("question_bank.json"))
        assert api.delete("/banks/UP6").json()["deleted"] == "UP6"

        from app.services.orchestrator import registry

        assert not registry.STORE.is_stored("UP6")

    def test_a_checked_in_bank_cannot_be_deleted_through_the_api(self, api):
        response = api.delete("/banks/DA")
        assert response.status_code == 404
        assert response.json()["code"] == "bank_not_stored"


class TestARejectionSaysWhatToFix:
    def test_a_file_that_is_not_json(self, api):
        response = upload(api, "BAD1", b"competency,question\nC1,what is 2+2")
        assert response.status_code == 422
        assert "not valid JSON" in response.json()["detail"]

    def test_a_file_with_no_items(self, api):
        response = upload(api, "BAD2", json.dumps({"items": []}).encode())
        assert response.status_code == 422
        assert "no items" in response.json()["detail"]

    def test_an_empty_upload(self, api):
        response = api.put(
            "/banks/BAD3", files={"file": ("b.json", b"", "application/json")}
        )
        assert response.status_code == 422
        assert response.json()["code"] == "upload_empty"

    def test_a_main_with_more_sub_competencies_than_the_budget(self, api):
        """AIE declares sixteen sub-competencies under C6 against a twelve-question cap.

        Every sub-competency of a derived graph is required, so this bank cannot be
        uploaded as-is — and the refusal NAMES the main, at the moment an author can act on
        it, rather than surfacing later as a session that always runs to the cap.
        """
        response = upload(api, "BIG", bank_bytes("question_bank_AIE.json"))
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "coverage_unreachable" in detail
        assert "C6" in detail

    def test_an_item_with_impossible_parameters(self, api):
        raw = json.loads(bank_bytes("question_bank.json"))
        raw["items"][0]["cat"]["a"] = 12.0  # bounded at 3.0 on the schema
        response = upload(api, "BAD4", json.dumps(raw).encode())
        assert response.status_code == 422
        assert "item_invalid" in response.json()["detail"]

    def test_a_rejected_upload_is_still_retrievable(self, api):
        """The raw bytes are kept before anything is parsed. A rejection that cannot be
        reproduced from the original input is a support ticket with no evidence in it."""
        upload(api, "BAD5", b"not json at all")
        listed = api.get("/uploads").json()
        assert listed[0]["status"] == "rejected"
        assert api.get(f"/uploads/{listed[0]['upload_id']}").json()["error"]


class TestTheWritePathCanBeTurnedOff:
    def test_uploads_are_refused_when_disabled(self, api, monkeypatch):
        from service.config import settings

        monkeypatch.setattr(settings, "ingest_api_enabled", False)
        response = upload(api, "OFF", bank_bytes("question_bank.json"))
        assert response.status_code == 503
        assert response.json()["code"] == "ingest_api_disabled"

    def test_health_still_answers_when_disabled(self, api, monkeypatch):
        from service.config import settings

        monkeypatch.setattr(settings, "ingest_api_enabled", False)
        assert api.get("/health").status_code == 200


class TestAnUploadedBankIsAssessable:
    """The end of the chain: questions in, an assessment out, no restart and no rebuild."""

    def test_an_uploaded_bank_can_be_scoped_and_assessed(self, tmp_path):
        from adaptive_clients import BankRegistryClient

        with bank_store_at(tmp_path / "banks"):
            with load_services("bank-ingest", "bank-registry", "competency-scope") as mods:
                ingest, scope = mods["bank-ingest"], mods["competency-scope"]
                ingest.UPLOADS.clear()

                with TestClient(ingest.app) as http:
                    receipt = http.put(
                        "/banks/UPLOADED",
                        files={
                            "file": (
                                "b.json",
                                bank_bytes("question_bank.json"),
                                "application/json",
                            )
                        },
                    ).json()
                assert receipt["status"] == "registered"

                from conftest import asgi_client

                with TestClient(mods["bank-registry"].app) as registry_http:
                    listed = {b["bank_id"] for b in registry_http.get("/banks").json()}
                    assert "UPLOADED" in listed, "the registry sees it with no restart"

                    scope._bank = BankRegistryClient(
                        http=asgi_client(mods["bank-registry"].app)
                    )
                    with TestClient(scope.app) as scope_http:
                        manifest = scope_http.post(
                            "/scopes",
                            json={"bank_id": "UPLOADED", "selected": ["DA.1", "DA.2"]},
                        ).json()

                assert manifest["coverage"]["reachable"] is True
                assert manifest["item_ids"]
                row = next(r for r in manifest["mains"] if r["main"] == "DA")
                assert row["partial"] is True


class TestABankCanDeclareItsOwnCompetencies:
    """The optional `competencies` block, and the thing it exists to unblock.

    Without it a derived graph marks every sub-competency critical, because nothing in a
    file of questions says one matters less than another — and a main with more
    sub-competencies than the budget allows can then never satisfy the coverage gate.
    `question_bank_AIE.json` is such a bank, so the flagship checked-in bank could not be
    uploaded through the service built to upload banks. This is how an author says
    otherwise, in the same one file, without writing a graph.
    """

    def aie_with_declaration(self) -> bytes:
        """AIE, plus the criticality its authored graph already states.

        Taken FROM that graph rather than invented, so this asserts the two routes can
        express the same bank rather than that some hand-picked set happens to fit.
        """
        from app.services.orchestrator import registry

        graph = registry.get_graph_service("AIE").graph
        raw = json.loads(bank_bytes("question_bank_AIE.json"))
        raw["competencies"] = [
            {
                "id": node_id,
                "title": node.title,
                "critical": node.critical,
                "mains": list(node.main_competencies),
            }
            for node_id, node in sorted(graph.nodes.items())
        ]
        return json.dumps(raw).encode()

    def test_aie_is_refused_without_a_declaration(self, api):
        """The state of things before this block existed. Kept as a test because it is the
        reason the block exists, and because a change that silently made it pass would mean
        the coverage requirement had quietly loosened."""
        response = upload(api, "AIE-PLAIN", bank_bytes("question_bank_AIE.json"))
        assert response.status_code == 422
        assert "coverage_unreachable" in response.json()["detail"]

    def test_the_same_bank_is_accepted_once_it_declares_its_critical_set(self, api):
        response = upload(api, "AIE-DECLARED", self.aie_with_declaration())
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "registered"

    def test_the_declared_critical_set_is_what_the_gate_will_require(self, api, derive):
        from app.services.orchestrator import registry

        upload(api, "AIE-DECLARED", self.aie_with_declaration())
        authored = registry.get_graph_service("AIE").graph
        uploaded = registry.get_graph_service("AIE-DECLARED").graph

        assert {n for n, node in uploaded.nodes.items() if node.critical} == {
            n for n, node in authored.nodes.items() if node.critical
        }

    def test_a_shared_node_survives_the_declaration(self, api):
        """`C1.6` serves both C1 and C6. An id prefix can only ever say C1, so before this
        block a derived graph could not represent a shared node at all."""
        from app.services.orchestrator import registry

        upload(api, "AIE-DECLARED", self.aie_with_declaration())
        node = registry.get_graph_service("AIE-DECLARED").graph.nodes["C1.6"]
        assert sorted(node.main_competencies) == ["C1", "C6"]

    def test_contributes_to_still_sums_to_one_with_shared_nodes(self, api, derive):
        """A shared node contributes to every main it serves, with a different weight into
        each. The per-main mass still has to sum to 1.0 or `retained_weight` in a scope
        stops meaning a share."""
        raw = json.loads(self.aie_with_declaration())
        graph = derive.derive_graph(
            "AIE", raw["items"], declared=derive.parse_declaration(raw["competencies"])
        )
        totals: dict[str, float] = {}
        for edge in graph["edges"]:
            if edge["relation"] == "CONTRIBUTES_TO" and "." not in edge["to"]:
                totals[edge["to"]] = totals.get(edge["to"], 0.0) + edge["weight"]
        assert set(totals) == {"C1", "C3", "C6"}
        for main, total in totals.items():
            assert total == pytest.approx(1.0, abs=1e-4), main

    def test_a_bank_that_declares_nothing_behaves_exactly_as_before(self, api, derive):
        """The block is optional, and its absence must change nothing."""
        raw = json.loads(bank_bytes("question_bank.json"))
        without = derive.derive_graph("DA", raw["items"])
        with_empty = derive.derive_graph("DA", raw["items"], declared={})
        assert without == with_empty
        assert all(
            n["critical"] for n in without["nodes"] if n["node_type"] == "sub_competency"
        )

    def test_the_receipt_reports_what_was_derived(self, api):
        receipt = upload(api, "AIE-DECLARED", self.aie_with_declaration()).json()
        derived = receipt["derived_graph"]
        assert derived["mains"] == ["C1", "C3", "C6"]
        assert derived["prerequisite_edges_are_inert"] is True
