"""The SQL store and the file store answer identically. That is the whole claim.

WHY THIS IS THE ONLY TEST THAT MATTERS HERE

A store that "works" is not the bar. `BankStore` is what every service resolves a bank
through today, and every orchestrator cache is keyed on the version it returns. A SQL store
that answered *almost* identically would produce sessions that are internally consistent,
plausible, and not the ones the evaluation harness measured — which is the same failure
`test_parity_inprocess_vs_services.py` exists to catch one layer up.

So this asserts, for all five checked-in banks: the same version, the same items, the same
parameters, the same graph, the same profile.

SKIPPED WITHOUT A DATABASE, ON PURPOSE

`BANK_DATABASE_URL` unset means skip. The rest of this suite runs with no external service
at all and takes seconds, and a database requirement would make the whole thing conditional
on infrastructure rather than just this file. Run it with:

    docker run --rm -d --name pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=adaptive \\
        -p 55432:5432 postgres:16-alpine
    BANK_DATABASE_URL=postgresql://postgres:test@127.0.0.1:55432/adaptive \\
        python -m pytest tests/test_sql_store_parity.py
"""

from __future__ import annotations

import json
import os

import pytest

DSN = os.environ.get("BANK_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not DSN, reason="BANK_DATABASE_URL is unset; this file needs a live Postgres"
)

BANKS = ["DA", "PY", "AIE", "AIE-JR-V3", "JAI-600"]


@pytest.fixture(scope="module")
def stores():
    """A freshly built database beside the file store the whole system uses today."""
    from adaptive_store import SqlBankStore, seed_from_profiles
    from app.services.orchestrator import registry

    sql = SqlBankStore(DSN)
    sql.ensure_schema()
    with sql.connect() as conn:
        # A clean slate per run, by DELETE rather than DROP: dropping a relation takes an
        # AccessExclusiveLock and deadlocks against any other connection holding it open.
        # The seeder is idempotent, but a test inheriting rows from a previous run would be
        # asserting against whatever happened to be there.
        conn.execute("DELETE FROM bank")
        conn.execute("DELETE FROM upload")
        conn.commit()
    seed_from_profiles(sql, dict(registry.STORE.profiles()))
    return sql, registry.STORE


class TestTheVersionIsTheSameNumber:
    """If this fails, nothing else in this file matters — and neither does any cache."""

    def test_every_seeded_bank_keeps_its_version(self, stores):
        from adaptive_store import assert_versions_match

        sql, files = stores
        assert assert_versions_match(sql, files) == []

    @pytest.mark.parametrize("bank_id", BANKS)
    def test_bank_by_bank(self, stores, bank_id):
        sql, files = stores
        assert sql.version(bank_id) == files.version(bank_id)

    def test_the_versions_are_distinct(self, stores):
        sql, _ = stores
        versions = {b: sql.version(b) for b in BANKS}
        assert len(set(versions.values())) == len(BANKS), versions


class TestTheItemsAreTheSameItems:
    @pytest.mark.parametrize("bank_id", BANKS)
    def test_the_same_ids_in_the_same_order(self, stores, bank_id):
        sql, files = stores
        expected = sorted(i.item_id for i in files.bank(bank_id).all_items())
        assert [i["item_id"] for i in sql.load(bank_id).items] == expected

    @pytest.mark.parametrize("bank_id", BANKS)
    def test_every_item_round_trips_through_the_engines_own_schema(self, stores, bank_id):
        """Not "the fields look right" — `BankItem` validates it, which is the same gate a
        bank file passes. Parameter bounds, payload matching the modality, at least one
        measured variable."""
        from app.schemas.orchestration import BankItem

        sql, _ = stores
        for raw in sql.load(bank_id).items:
            BankItem.model_validate(raw)

    @pytest.mark.parametrize("bank_id", BANKS)
    def test_parameters_measures_and_status_agree_exactly(self, stores, bank_id):
        """Float equality, not a tolerance. `a`, `b` and `c` go into an information
        calculation on every ranking step; a value that round-tripped differently would
        reorder shortlists rather than fail anything."""
        from app.schemas.orchestration import BankItem

        sql, files = stores
        from_files = {i.item_id: i for i in files.bank(bank_id).all_items()}
        for raw in sql.load(bank_id).items:
            mine = BankItem.model_validate(raw)
            theirs = from_files[mine.item_id]
            assert (mine.cat.a, mine.cat.b, mine.cat.c) == (
                theirs.cat.a,
                theirs.cat.b,
                theirs.cat.c,
            )
            assert mine.status == theirs.status
            assert mine.modality == theirs.modality
            assert sorted((m.variable, m.weight) for m in mine.measures) == sorted(
                (m.variable, m.weight) for m in theirs.measures
            )

    def test_a_retired_item_is_still_retired(self, stores):
        """`JAI-600` retires 64 code items whose hidden tests are incomplete. A store that
        lost `status` would put every one of them back into the pool."""
        sql, _ = stores
        retired = [i for i in sql.load("JAI-600").items if i["status"] != "active"]
        assert len(retired) == 64

    def test_the_payload_survives_intact(self, stores):
        """The answer key, the options, the hidden tests. Stored as jsonb and compared
        against the file, because a payload that lost a field would be a question that
        cannot be graded."""
        sql, files = stores
        from_files = {i.item_id: i for i in files.bank("DA").all_items()}
        for raw in sql.load("DA").items:
            item = from_files[raw["item_id"]]
            assert raw[raw["modality"]] == item.payload


class TestTheGraphIsTheSameGraph:
    @pytest.mark.parametrize("bank_id", BANKS)
    def test_nodes_and_edges_match_the_file(self, stores, bank_id):
        sql, files = stores
        stored = sql.load(bank_id).graph
        on_disk = files.profile(bank_id).graph_path
        if on_disk is None:
            assert stored is None
            return
        assert stored == json.loads(on_disk.read_bytes())

    @pytest.mark.parametrize("bank_id", BANKS)
    def test_it_still_parses_as_a_competency_graph(self, stores, bank_id):
        from app.services.competency_graph.validator import parse_and_validate_graph

        sql, _ = stores
        graph = sql.load(bank_id).graph
        if graph is not None:
            parse_and_validate_graph(graph, source=bank_id)

    def test_the_critical_set_is_preserved(self, stores):
        """The set the coverage gate requires. AIE marks thirteen nodes critical, and a
        store that lost the flag would silently change what a competency has to demonstrate
        before it may converge."""
        sql, files = stores
        stored = {
            n["competency_id"] for n in sql.load("AIE").graph["nodes"] if n.get("critical")
        }
        graph = files.graph("AIE")
        expected = {nid for nid, node in graph.nodes.items() if node.critical}
        assert stored == expected

    def test_shared_nodes_keep_every_main_they_serve(self, stores):
        """`C1.6` serves both C1 and C6. One node holds one authoritative state that every
        main it serves reads, so dropping a membership would drop half of what it evidences."""
        sql, _ = stores
        nodes = {n["competency_id"]: n for n in sql.load("AIE").graph["nodes"]}
        assert sorted(nodes["C1.6"]["main_competencies"]) == ["C1", "C6"]


class TestTheProfileSurvives:
    @pytest.mark.parametrize("bank_id", BANKS)
    def test_mains_and_coverage_policy_agree(self, stores, bank_id):
        sql, files = stores
        stored, profile = sql.load(bank_id), files.profile(bank_id)
        assert stored.mains == profile.mains
        assert stored.coverage_critical_only == profile.coverage_critical_only
        assert stored.title == profile.title

    def test_a_seed_is_labelled_a_seed(self, stores):
        """`stored` shadows `seed` on the same id, and deleting the shadow restores the
        seed. A store that could not tell them apart would make an accidental overwrite
        unrecoverable without a redeploy."""
        sql, _ = stores
        assert sql.load("AIE").source == "seed"


class TestTheQueryTheSchemaExistsFor:
    def test_items_measuring_returns_what_a_scan_would(self, stores):
        """The point of `item_measure`: a membership test instead of a scan of 600 items."""
        sql, files = stores
        nodes = ["C1.1", "C1.4"]
        expected = sorted(
            i.item_id
            for i in files.bank("AIE").all_items()
            if i.status == "active" and {m.variable for m in i.measures} & set(nodes)
        )
        assert sql.items_measuring("AIE", nodes) == expected

    def test_it_excludes_retired_items(self, stores):
        sql, files = stores
        retired = {
            i.item_id for i in files.bank("JAI-600").all_items() if i.status != "active"
        }
        every_node = sorted(
            {m.variable for i in files.bank("JAI-600").all_items() for m in i.measures}
        )
        assert not retired & set(sql.items_measuring("JAI-600", every_node))


class TestWritesAreAllOrNothing:
    def test_a_failed_write_leaves_the_previous_bank_intact(self, stores):
        """The property the file store buys with a staging directory and a rename, and the
        reason to be in a database at all: here it is a rollback rather than a dance."""
        sql, _ = stores
        before = sql.version("DA")

        from adaptive_store import StoreIntegrityError

        broken = [{"item_id": "x", "modality": "mcq"}]  # no cat, no measures
        with pytest.raises(StoreIntegrityError):
            sql.save(
                bank_id="DA",
                title="broken",
                items=broken,
                graph=None,
                coverage_critical_only=None,
                mains=["DA"],
            )
        assert sql.version("DA") == before

    def test_registering_identical_bytes_twice_is_the_same_version(self, stores):
        sql, files = stores
        items = [
            json.loads(i.model_dump_json()) for i in files.bank("DA").all_items()
        ]
        first = sql.save(
            bank_id="RT",
            title="round trip",
            items=items,
            graph=None,
            coverage_critical_only=None,
            mains=["DA"],
        )
        second = sql.save(
            bank_id="RT",
            title="round trip",
            items=items,
            graph=None,
            coverage_critical_only=None,
            mains=["DA"],
        )
        assert first == second
        assert sql.version("RT") == first
