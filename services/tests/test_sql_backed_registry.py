"""bank-registry, served out of Postgres, behaving exactly as it does out of files.

WHAT THIS TEST ACTUALLY DOES

It runs the bank-registry service against `SqlBackedBankStore` and re-asserts the same
properties `test_bank_registry_service.py` asserts against the file store — the catalogue,
both read paths, the ETag, the write path, shadowing, and the refusals. Not a paraphrase of
them: the same claims, so a divergence shows up as a failing test rather than as a service
that works and answers differently.

WHY THAT IS THE BAR

Swapping the store underneath `bank-registry` changes where every item, graph and version in
the system comes from. A store that merely worked would still be free to differ in the parts
nothing checks — a version computed a new way, an item whose parameters round-tripped
differently, a shadowed seed that no longer reappears — and each of those produces a system
that is internally consistent and not the one anything was measured against.

SKIPPED WITHOUT A DATABASE, like `test_sql_store_parity.py`, so the rest of the suite keeps
running in seconds with no infrastructure.

AND RUN AS ITS OWN SESSION. `BANK_DATABASE_URL` is read by `Settings` at import, so with it
set EVERY load of `bank-registry` or `bank-ingest` resolves banks from Postgres — including
loads by test files that meant to exercise the file store. Correct for a deployment, wrong
for a mixed session, and the same argument `pytest.ini` already makes for keeping the engine
and service suites apart:

    cd services && BANK_DATABASE_URL=... python -m pytest tests/test_sql_store_parity.py \
                                                          tests/test_sql_backed_registry.py
"""

from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from conftest import load_services

DSN = os.environ.get("BANK_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not DSN, reason="BANK_DATABASE_URL is unset; this file needs a live Postgres"
)


def bank_file() -> dict:
    """A minimal valid bank, as a file. No graph — `bank-ingest` derives one."""
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


def upload(writer, bank_id: str, body: dict | None = None):
    """The one write endpoint. A file, not a JSON submission."""
    return writer.put(
        f"/banks/{bank_id}",
        files={
            "file": (
                "bank.json",
                json.dumps(body or bank_file()).encode(),
                "application/json",
            )
        },
    )


@pytest.fixture()
def stack(tmp_path):
    """bank-registry, with Postgres underneath it instead of two directories."""
    from adaptive_store import SqlBankStore
    from adaptive_store.backed import install
    from app.services.orchestrator import registry

    file_store = registry.STORE
    seeds = dict(file_store.profiles())

    sql = SqlBankStore(DSN)
    sql.ensure_schema()
    with sql.connect() as conn:
        # DELETE, not DROP. Dropping a relation takes an AccessExclusiveLock, and any other
        # connection — a service loaded by another test file, which `BANK_DATABASE_URL`
        # makes go through Postgres too — deadlocks against it. Cascading a delete from
        # `bank` clears every dependent row and takes no exclusive lock.
        conn.execute("DELETE FROM bank")
        conn.execute("DELETE FROM upload")
        conn.commit()

    install(DSN, seed_from=seeds, cache_dir=tmp_path / "cache")
    try:
        # BOTH SERVICES, over one store. That is the deployment: `bank-ingest` writes and
        # `bank-registry` reads, and the property worth testing is that a bank written by
        # one is immediately, identically readable by the other.
        with load_services("bank-ingest", "bank-registry") as modules:
            modules["bank-ingest"].UPLOADS.clear()
            with TestClient(modules["bank-registry"].app) as reader, TestClient(
                modules["bank-ingest"].app
            ) as writer:
                yield reader, writer
    finally:
        # Every other test in this suite resolves banks through the file store.
        registry.use_store(file_store)


class TestTheCatalogueIsTheSameCatalogue:
    def test_the_checked_in_banks_are_all_there_and_still_seeds(self, stack):
        reader, writer = stack
        banks = {b["bank_id"]: b for b in reader.get("/banks").json()}
        for bank_id in ("DA", "PY", "AIE", "AIE-JR-V3", "JAI-600"):
            assert banks[bank_id]["source"] == "seed", bank_id

    def test_the_version_is_the_one_the_file_store_computes(self, stack):
        """The number every orchestrator cache is keyed on and every session pins."""
        reader, writer = stack
        from app.services.orchestrator.bank_store import BankStore, _seed_profiles

        files = BankStore(seeds=_seed_profiles())
        for bank_id in ("DA", "AIE", "JAI-600"):
            served = reader.get(f"/banks/{bank_id}").json()["version"]
            assert served == files.version(bank_id), bank_id

    def test_profile_claims_survive(self, stack):
        reader, writer = stack
        aie = reader.get("/banks/AIE").json()
        assert aie["mains"] == ["C1", "C3", "C6"]
        assert aie["coverage_critical_only"] is True

    def test_an_unknown_bank_is_still_a_404(self, stack):
        reader, writer = stack
        assert reader.get("/banks/NOPE").status_code == 404


class TestBothReadPathsStillDiffer:
    def test_the_ranking_view_carries_no_question(self, stack):
        reader, writer = stack
        body = reader.get("/banks/DA/items").text
        for forbidden in ("answer_index", "stem", "options", "reference_solution"):
            assert forbidden not in body

    def test_the_rendering_view_does(self, stack):
        reader, writer = stack
        items = reader.get("/banks/DA/items").json()
        full = reader.get(f"/banks/DA/items/{items[0]['item_id']}").json()
        assert full["payload"]
        assert "answer_index" in full["payload"]

    def test_a_retired_item_is_still_marked_retired(self, stack):
        reader, writer = stack
        items = reader.get("/banks/JAI-600/items").json()
        retired = [i for i in items if i["status"] != "active"]
        assert len(retired) == 64

    def test_the_etag_matches_the_catalogue_version(self, stack):
        reader, writer = stack
        etag = reader.get("/banks/AIE/items").headers["etag"].strip('"')
        assert etag == reader.get("/banks/AIE").json()["version"]


class TestTheGraphSurvivesTheRoundTrip:
    def test_nodes_and_edges_are_served(self, stack):
        reader, writer = stack
        graph = reader.get("/banks/AIE/graph").json()
        assert len(graph["nodes"]) == 36
        assert len(graph["edges"]) == 67

    def test_the_policy_still_explains_why_each_edge_is_inert(self, stack):
        reader, writer = stack
        policy = reader.get("/banks/AIE/policy").json()
        assert policy["edges"]
        assert not any(e["inference_allowed"] for e in policy["edges"])

    def test_coverage_counts_are_unchanged(self, stack):
        """Against the file store's own answer rather than a literal. The total exceeds the
        item count because an item measuring two mains is counted under both — which is the
        kind of detail a hand-written expectation gets wrong and a comparison cannot."""
        reader, writer = stack
        from app.services.orchestrator.bank_store import BankStore, _seed_profiles

        expected = BankStore(seeds=_seed_profiles()).bank("AIE").coverage()
        assert reader.get("/banks/AIE/coverage").json() == expected


class TestWritingThroughIngestAndReadingThroughTheRegistry:
    """The deployment, end to end: one service writes rows, another serves them.

    `bank-registry` no longer writes anything — its four write endpoints return 410 naming
    `bank-ingest`. So the property worth asserting is no longer "the registry can write",
    it is that a bank written by the writer is immediately and identically readable by the
    reader, with no restart and nothing shared but the database.
    """

    def test_an_uploaded_bank_is_readable_through_the_registry_at_once(self, stack):
        reader, writer = stack
        assert upload(writer, "SQLBANK").status_code == 200
        assert reader.get("/banks/SQLBANK").status_code == 200
        assert len(reader.get("/banks/SQLBANK/items").json()) == 3

    def test_uploading_twice_is_idempotent(self, stack):
        reader, writer = stack
        first = upload(writer, "SQLBANK").json()["version"]
        assert upload(writer, "SQLBANK").json()["version"] == first
        assert reader.get("/banks/SQLBANK").json()["version"] == first

    def test_different_content_moves_the_version(self, stack):
        reader, writer = stack
        first = upload(writer, "SQLBANK").json()["version"]
        changed = bank_file()
        changed["items"][0]["cat"]["b"] = 1.75
        assert upload(writer, "SQLBANK", changed).json()["version"] != first
        assert reader.get("/banks/SQLBANK").json()["version"] != first

    def test_a_refused_bank_is_not_written(self, stack):
        """The rollback the file store buys with a staging directory and a rename."""
        reader, writer = stack
        broken = bank_file()
        broken["items"][0]["cat"]["a"] = 12.0  # bounded at 3.0 on the schema
        assert upload(writer, "SQLBAD", broken).status_code == 422
        assert reader.get("/banks/SQLBAD").status_code == 404

    def test_deleting_an_uploaded_bank_forgets_it(self, stack):
        reader, writer = stack
        upload(writer, "SQLBANK")
        assert writer.delete("/banks/SQLBANK").json()["deleted"] == "SQLBANK"
        assert reader.get("/banks/SQLBANK").status_code == 404

    def test_the_registrys_own_write_path_is_gone(self, stack):
        reader, _ = stack
        response = reader.post("/banks", json={})
        assert response.status_code == 410
        assert "bank-ingest" in response.json()["detail"]


class TestShadowingStillWorks:
    """`stored` over `seed` was two directories; it is now a column on the version. The
    behaviour a deployment depends on — an accidental overwrite is recoverable without a
    redeploy — has to be identical."""

    def test_an_upload_over_a_seed_shadows_it(self, stack):
        reader, writer = stack
        before = reader.get("/banks/DA").json()["version"]
        assert upload(writer, "DA").status_code == 200

        after = reader.get("/banks/DA").json()
        assert after["version"] != before
        assert after["source"] == "stored"

    def test_deleting_the_shadow_restores_the_checked_in_bank(self, stack):
        reader, writer = stack
        before = reader.get("/banks/DA").json()["version"]
        upload(writer, "DA")

        assert writer.delete("/banks/DA").json()["seed_restored"] is True

        restored = reader.get("/banks/DA").json()
        assert restored["version"] == before
        assert restored["source"] == "seed"
        assert len(reader.get("/banks/DA/items").json()) == 34
