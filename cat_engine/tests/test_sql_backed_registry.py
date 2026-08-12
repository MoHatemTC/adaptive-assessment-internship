"""Banks served out of Postgres, behaving exactly as they do out of files.

WHAT THIS TEST ACTUALLY DOES

It runs the module against `SqlBackedBankStore` and re-asserts the same properties
`test_catalogue.py` asserts against the file store — the catalogue, both read paths, the
graph, the write path, shadowing, and the refusals. Not a paraphrase of them: the same
claims, so a divergence shows up as a failing test rather than as a module that works and
answers differently.

WHY THAT IS THE BAR

Swapping the store underneath changes where every item, graph and version in the system
comes from. A store that merely worked would still be free to differ in the parts nothing
checks — a version computed a new way, an item whose parameters round-tripped differently, a
shadowed seed that no longer reappears — and each of those produces a system that is
internally consistent and not the one anything was measured against.

WHAT THE COLLAPSE CHANGED HERE

This was two services over one database: `bank-ingest` wrote rows and `bank-registry` served
them, and the property was that a bank written by one was immediately readable by the other.
There is one object now, so that property is no longer about two processes agreeing — it is
that a write is visible through the read path with no cache reset anybody has to remember,
which is the same failure with the network taken out.

The one assertion that did NOT survive is `test_the_registrys_own_write_path_is_gone`. It
checked that four deprecated endpoints returned 410 pointing at `bank-ingest`. There are no
endpoints; `upload_bank` is the only way a bank enters the system, and that is now a fact
about the surface rather than something a test can catch a regression in.

SKIPPED WITHOUT A DATABASE, so the rest of the suite keeps running in seconds with no
infrastructure:

    BANK_DATABASE_URL=postgresql://... python -m pytest cat_engine/tests/test_sql_backed_registry.py
"""

from __future__ import annotations

import json
import os

import pytest

DSN = os.environ.get("BANK_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not DSN, reason="BANK_DATABASE_URL is unset; this file needs a live Postgres"
)


def bank_file() -> dict:
    """A minimal valid bank, as a file. No graph — ingest derives one."""
    return {
        "schema_version": 2,
        "competency": "a bank stored in postgres",
        "competencies": [{"id": "Z.1", "title": "Z one", "critical": True}],
        "items": [
            {
                "item_id": f"q{n}",
                "modality": "mcq",
                "competency": "Z",
                "sub_competency": "Z.1 · Z one",
                "measures": [{"variable": "Z.1", "weight": 1.0}],
                "cat": {"a": 1.2, "b": 0.0, "c": 0.25},
                "mcq": {"stem": "2 + 2?", "options": ["3", "4"], "answer_index": 1},
            }
            for n in range(3)
        ],
    }


def upload(cat, bank_id: str, body: dict | None = None):
    """The one write path. A file, not a JSON submission."""
    return cat.upload_bank(bank_id, json.dumps(body or bank_file()).encode())


@pytest.fixture()
def cat(tmp_path):
    """The module, with Postgres underneath it instead of two directories.

    The module installs the store itself — `AssessmentModule.__init__` calls the same
    `install` a deployment does, seeding idempotently. That is deliberate here rather than
    incidental: doing it in the fixture instead would test a path production does not take.
    """
    from cat_engine import AssessmentModule, CatConfig
    from cat_engine.config import _reset_for_tests
    from cat_engine.engine.services.orchestrator import registry
    from cat_engine.stores.sql import SqlBankStore

    file_store = registry.STORE

    sql = SqlBankStore(DSN)
    sql.ensure_schema()
    with sql.connect() as conn:
        # DELETE, not DROP. Dropping a relation takes an AccessExclusiveLock, and any other
        # connection deadlocks against it. Cascading a delete from `bank` clears every
        # dependent row and takes no exclusive lock.
        conn.execute("DELETE FROM bank")
        conn.execute("DELETE FROM upload")
        conn.commit()

    _reset_for_tests()
    try:
        yield AssessmentModule(CatConfig(bank_database_url=DSN))
    finally:
        _reset_for_tests()
        # Every other test in this suite resolves banks through the file store.
        registry.use_store(file_store)


class TestTheCatalogueIsTheSameCatalogue:
    def test_the_checked_in_banks_are_all_there_and_still_seeds(self, cat):
        banks = {b.bank_id: b for b in cat.banks()}
        for bank_id in ("DA", "PY", "AIE", "AIE-JR-V3", "JAI-600"):
            assert banks[bank_id].source == "seed", bank_id

    def test_the_version_is_the_one_the_file_store_computes(self, cat):
        """The number every orchestrator cache is keyed on and every session pins."""
        from cat_engine.engine.services.orchestrator.bank_store import (
            BankStore,
            _seed_profiles,
        )

        files = BankStore(seeds=_seed_profiles())
        for bank_id in ("DA", "AIE", "JAI-600"):
            assert cat.bank(bank_id).version == files.version(bank_id), bank_id

    def test_profile_claims_survive(self, cat):
        aie = cat.bank("AIE")
        assert aie.mains == ["C1", "C3", "C6"]
        assert aie.coverage_critical_only is True

    def test_an_unknown_bank_is_still_refused(self, cat):
        from cat_engine.errors import BankUnknown

        with pytest.raises(BankUnknown):
            cat.bank("NOPE")


class TestBothReadPathsStillDiffer:
    """The security boundary of the whole bank surface, over a different store.

    `item_refs` is what selection ranks over and has no payload FIELD to leak one; `items`
    carries the answer key. Two functions with two return types, and swapping Postgres in
    underneath must not blur them.
    """

    def test_the_ranking_view_carries_no_question(self, cat):
        from cat_engine import catalogue

        _version, refs = catalogue.item_refs("DA")
        body = json.dumps([r.model_dump() for r in refs])
        for forbidden in ("answer_index", "stem", "options", "reference_solution"):
            assert forbidden not in body

    def test_the_rendering_view_does(self, cat):
        items = cat.items("DA")
        full = cat.item(items[0].item_id, "DA")
        assert full.payload
        assert "answer_index" in full.payload

    def test_a_retired_item_is_still_marked_retired(self, cat):
        retired = [i for i in cat.items("JAI-600") if i.status != "active"]
        assert len(retired) == 64

    def test_the_ranking_view_reports_the_catalogue_version(self, cat):
        """Was an ETag. The version still travels with the parameters, because a caller
        caching them has to know which bank state they describe."""
        from cat_engine import catalogue

        version, _refs = catalogue.item_refs("AIE")
        assert version == cat.bank("AIE").version


class TestTheGraphSurvivesTheRoundTrip:
    def test_nodes_and_edges_are_served(self, cat):
        graph = cat.graph("AIE")
        assert len(graph.nodes) == 36
        assert len(graph.edges) == 67

    def test_the_policy_still_explains_why_each_edge_is_inert(self, cat):
        policy = cat.policy("AIE")
        assert policy.edges
        assert not any(e.inference_allowed for e in policy.edges)

    def test_coverage_counts_are_unchanged(self, cat):
        """Against the file store's own answer rather than a literal. The total exceeds the
        item count because an item measuring two mains is counted under both — which is the
        kind of detail a hand-written expectation gets wrong and a comparison cannot."""
        from cat_engine.engine.services.orchestrator import registry
        from cat_engine.engine.services.orchestrator.bank_store import (
            BankStore,
            _seed_profiles,
        )

        expected = BankStore(seeds=_seed_profiles()).bank("AIE").coverage()
        assert registry.get_bank("AIE").coverage() == expected


class TestWritingAndReadingThroughOneStore:
    def test_an_uploaded_bank_is_readable_at_once(self, cat):
        assert upload(cat, "SQLBANK").status == "registered"
        assert cat.bank("SQLBANK").bank_id == "SQLBANK"
        assert len(cat.items("SQLBANK")) == 3

    def test_uploading_twice_is_idempotent(self, cat):
        first = upload(cat, "SQLBANK").version
        assert upload(cat, "SQLBANK").version == first
        assert cat.bank("SQLBANK").version == first

    def test_different_content_moves_the_version(self, cat):
        first = upload(cat, "SQLBANK").version
        changed = bank_file()
        changed["items"][0]["cat"]["b"] = 1.75
        assert upload(cat, "SQLBANK", changed).version != first
        assert cat.bank("SQLBANK").version != first

    def test_a_refused_bank_is_not_written(self, cat):
        """The rollback the file store buys with a staging directory and a rename."""
        from cat_engine.errors import BankUnknown

        broken = bank_file()
        broken["items"][0]["cat"]["a"] = 12.0  # bounded at 3.0 on the schema
        assert upload(cat, "SQLBAD", broken).status == "rejected"
        with pytest.raises(BankUnknown):
            cat.bank("SQLBAD")

    def test_deleting_an_uploaded_bank_forgets_it(self, cat):
        from cat_engine.errors import BankUnknown

        upload(cat, "SQLBANK")
        assert cat.delete_bank("SQLBANK") is True
        with pytest.raises(BankUnknown):
            cat.bank("SQLBANK")


class TestShadowingStillWorks:
    """`stored` over `seed` was two directories; it is now a column on the version. The
    behaviour a deployment depends on — an accidental overwrite is recoverable without a
    redeploy — has to be identical."""

    def test_an_upload_over_a_seed_shadows_it(self, cat):
        before = cat.bank("DA").version
        assert upload(cat, "DA").status == "registered"

        after = cat.bank("DA")
        assert after.version != before
        assert after.source == "stored"

    def test_deleting_the_shadow_restores_the_checked_in_bank(self, cat):
        before = cat.bank("DA").version
        upload(cat, "DA")

        assert cat.delete_bank("DA") is True

        restored = cat.bank("DA")
        assert restored.version == before
        assert restored.source == "seed"
        assert len(cat.items("DA")) == 34
