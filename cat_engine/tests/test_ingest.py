"""One uploaded file becomes a registered bank.

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

from cat_engine.errors import BankInvalid, WritesDisabled


def bank_bytes(name: str) -> bytes:
    from cat_engine.engine.config.paths import DATA_DIR

    return (DATA_DIR / name).read_bytes()


def upload(api, bank_id: str, raw: bytes, *, dry_run: bool = False):
    """The one write path. Register and replace are the same call, by design.

    Returns a RECEIPT, not a response: a rejected bank comes back with `status="rejected"`
    and the findings that caused it, because the caller is usually an authoring tool and the
    useful answer is which item is at fault.
    """
    return api.register(bank_id, raw, write=not dry_run)


@pytest.fixture()
def store_at(tmp_path):
    """Point the process-wide bank store at a temporary directory for the duration.

    Any test that writes a bank needs this. Without it the write lands in the checked-in
    `engine/data/banks`, and a later run discovers a registered bank that no fixture created
    and no assertion expects — a failure in a different file, hours later, with nothing
    pointing back here.
    """
    from cat_engine.engine.services.orchestrator import registry
    from cat_engine.engine.services.orchestrator.bank_store import BankStore, _seed_profiles

    previous = registry.STORE
    registry.use_store(BankStore(seeds=_seed_profiles(), store_dir=tmp_path / "banks"))
    try:
        yield registry.STORE
    finally:
        registry.use_store(previous)


@pytest.fixture()
def derive():
    """The derivation module itself, for the tests that assert on a graph directly."""
    from cat_engine.ingest import derive as module

    return module


@pytest.fixture()
def api(store_at):
    from cat_engine.ingest import Ingest

    return Ingest()


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
        from cat_engine.engine.services.competency_graph.validator import parse_and_validate_graph

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
        from cat_engine.engine.services.orchestrator.competency import main_competency

        for variable in ("C1.1", "C1", "DA.10", "T1.4", "PY.2"):
            assert derive.main_of(variable) == main_competency(variable)


class TestUploadingABank:
    def test_one_file_becomes_a_registered_bank(self, api):
        receipt = upload(api, "UP1", bank_bytes("question_bank.json"))
        assert receipt.status == "registered"
        assert receipt.version
        assert receipt.items == 34
        assert receipt.validation.accepted is True

    def test_the_receipt_shows_the_graph_the_author_did_not_upload(self, api):
        derived = upload(
            api, "UP1", bank_bytes("question_bank.json")
        ).derived_graph.model_dump()
        assert derived["mains"] == ["DA"]
        assert len(derived["sub_competencies"]) == 6
        assert derived["prerequisite_edges_are_inert"] is True

    def test_a_bare_list_of_items_is_accepted(self, api):
        raw = json.loads(bank_bytes("question_bank.json"))
        assert upload(api, "UP2", json.dumps(raw["items"]).encode()).status == "registered"

    def test_a_dry_run_writes_nothing(self, api):
        response = upload(api, "UP3", bank_bytes("question_bank.json"), dry_run=True)
        assert response.validation.accepted is True
        assert response.status == "validating"
        assert response.derived_graph, "the derived graph is the point of a dry run"

        from cat_engine.engine.services.orchestrator import registry

        assert not registry.STORE.is_stored("UP3")

    def test_uploading_twice_is_idempotent_rather_than_a_conflict(self, api):
        """A bank is identified by its id and its content, so the same bytes under the same
        id are the same bank. Register and replace were separate verbs and a retried POST
        could fail for having succeeded; one PUT cannot."""
        first = upload(api, "UP4", bank_bytes("question_bank.json"))
        again = upload(api, "UP4", bank_bytes("question_bank.json"))
        assert again.status == "registered"
        assert again.version == first.version, "identical bytes, identical hash"

    def test_uploading_different_content_moves_the_version(self, api):
        first = upload(api, "UP5", bank_bytes("question_bank.json"))
        raw = json.loads(bank_bytes("question_bank.json"))
        raw["items"][0]["cat"]["b"] = 1.75
        changed = upload(api, "UP5", json.dumps(raw).encode())
        assert changed.version != first.version

    def test_deleting_an_uploaded_bank_forgets_it(self, api):
        upload(api, "UP6", bank_bytes("question_bank.json"))
        assert api.delete("UP6") is True

        from cat_engine.engine.services.orchestrator import registry

        assert not registry.STORE.is_stored("UP6")

    def test_a_checked_in_bank_cannot_be_deleted_through_the_api(self, api):
        assert api.delete("DA") is False, "a checked-in bank is not the store's to remove"


class TestARejectionSaysWhatToFix:
    def test_a_file_that_is_not_json(self, api):
        receipt = upload(api, "BAD1", b"competency,question\nC1,what is 2+2")
        assert receipt.status == "rejected"
        assert "not valid JSON" in receipt.error

    def test_a_file_with_no_items(self, api):
        receipt = upload(api, "BAD2", json.dumps({"items": []}).encode())
        assert receipt.status == "rejected"
        assert "no items" in receipt.error

    def test_an_empty_upload(self, api):
        with pytest.raises(BankInvalid) as caught:
            upload(api, "BAD3", b"")
        assert caught.value.code == "upload_empty"

    def test_a_main_with_more_sub_competencies_than_the_budget(self, api):
        """AIE declares sixteen sub-competencies under C6 against a twelve-question cap.

        Every sub-competency of a derived graph is required, so this bank cannot be
        uploaded as-is — and the refusal NAMES the main, at the moment an author can act on
        it, rather than surfacing later as a session that always runs to the cap.
        """
        receipt = upload(api, "BIG", bank_bytes("question_bank_AIE.json"))
        assert receipt.status == "rejected"
        assert "coverage_unreachable" in receipt.error
        assert "C6" in receipt.error

    def test_an_item_with_impossible_parameters(self, api):
        raw = json.loads(bank_bytes("question_bank.json"))
        raw["items"][0]["cat"]["a"] = 12.0  # bounded at 3.0 on the schema
        receipt = upload(api, "BAD4", json.dumps(raw).encode())
        assert receipt.status == "rejected"
        assert "item_invalid" in receipt.error

    def test_a_rejected_upload_is_still_retrievable(self, api):
        """The raw bytes are kept before anything is parsed. A rejection that cannot be
        reproduced from the original input is a support ticket with no evidence in it."""
        upload(api, "BAD5", b"not json at all")
        listed = api.uploads.recent()
        assert listed[0].raw == b"not json at all", "the original bytes are kept"
        assert listed[0].receipt.status == "rejected"
        assert listed[0].receipt.error


class TestTheWritePathCanBeTurnedOff:
    def test_uploads_are_refused_when_disabled(self, api, monkeypatch):
        monkeypatch.setattr(api.settings, "ingest_api_enabled", False)
        with pytest.raises(WritesDisabled) as caught:
            upload(api, "OFF", bank_bytes("question_bank.json"))
        assert caught.value.status_code == 403

    def test_reading_still_works_when_writing_is_disabled(self, api, monkeypatch):
        """The switch stops uploads, not the module. Was `/health` still answering."""
        from cat_engine import catalogue

        monkeypatch.setattr(api.settings, "ingest_api_enabled", False)
        assert catalogue.banks()


class TestAnUploadedBankIsAssessable:
    """The end of the chain: questions in, an assessment out, no restart and no rebuild."""

    async def test_an_uploaded_bank_can_be_scoped_and_assessed(self, api, module):
        """Three services and two HTTP hops once; three method calls now.

        The chain is the assertion: an author uploads questions, the catalogue sees the
        bank with no restart, a scope can be built over it, and an assessment runs against
        it. Every link was a deployment concern and none of them is one any more.
        """
        receipt = upload(api, "UPLOADED", bank_bytes("question_bank.json"))
        assert receipt.status == "registered"

        assert "UPLOADED" in {b.bank_id for b in module.banks()}, (
            "the catalogue sees it with no restart"
        )

        manifest = module.scope(["DA.1", "DA.2"], bank_id="UPLOADED")
        assert manifest.coverage.reachable is True
        assert manifest.item_ids
        row = next(r for r in manifest.mains if r.main == "DA")
        assert row.partial is True

        state = await module.begin(
            bank_id="UPLOADED", scope=["DA.1", "DA.2"], use_llm=False, seed=7
        )
        assert state.presenting is not None
        assert state.presenting.item.item_id in set(manifest.item_ids)


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
        from cat_engine.engine.services.orchestrator import registry

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
        receipt = upload(api, "AIE-PLAIN", bank_bytes("question_bank_AIE.json"))
        assert receipt.status == "rejected"
        assert "coverage_unreachable" in receipt.error

    def test_the_same_bank_is_accepted_once_it_declares_its_critical_set(self, api):
        assert upload(api, "AIE-DECLARED", self.aie_with_declaration()).status == (
            "registered"
        )

    def test_the_declared_critical_set_is_what_the_gate_will_require(self, api, derive):
        from cat_engine.engine.services.orchestrator import registry

        upload(api, "AIE-DECLARED", self.aie_with_declaration())
        authored = registry.get_graph_service("AIE").graph
        uploaded = registry.get_graph_service("AIE-DECLARED").graph

        assert {n for n, node in uploaded.nodes.items() if node.critical} == {
            n for n, node in authored.nodes.items() if node.critical
        }

    def test_a_shared_node_survives_the_declaration(self, api):
        """`C1.6` serves both C1 and C6. An id prefix can only ever say C1, so before this
        block a derived graph could not represent a shared node at all."""
        from cat_engine.engine.services.orchestrator import registry

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
        derived = upload(
            api, "AIE-DECLARED", self.aie_with_declaration()
        ).derived_graph.model_dump()
        assert derived["mains"] == ["C1", "C3", "C6"]
        assert derived["prerequisite_edges_are_inert"] is True
