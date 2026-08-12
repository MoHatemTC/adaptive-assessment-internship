"""Reading banks: what is registered, what it measures, and what it declares.

WHAT SURVIVED FROM `test_bank_registry_service.py`, AND WHAT DID NOT

The read properties survived, because they are about which fields cross a boundary and that
boundary still exists — it is a return type now instead of an endpoint.

`TestTheWritePathIsGone` did not. It asserted that four deprecated endpoints returned 410
naming `bank-ingest`. There are no endpoints, and `upload_bank` being the only way a bank
enters the system is a property of the surface rather than something a test can catch a
regression in. What it was really protecting — that a refused write leaves the previous bank
intact — is asserted in `test_ingest.py` and `test_sql_backed_registry.py`, against the
code that actually writes.

The ETag assertions became version assertions. An ETag was how a version reached a client
over HTTP; the version itself is what mattered, and `item_refs` still returns it beside the
parameters because a caller caching them has to know which bank state they describe.
"""

from __future__ import annotations

import json

import pytest

from cat_engine import catalogue
from cat_engine.errors import BankUnknown


@pytest.fixture()
def store_at(tmp_path):
    """A bank store in a temporary directory, for the tests that write one."""
    from cat_engine.engine.services.orchestrator import registry
    from cat_engine.engine.services.orchestrator.bank_store import (
        BankStore,
        _seed_profiles,
    )

    previous = registry.STORE
    registry.use_store(BankStore(seeds=_seed_profiles(), store_dir=tmp_path / "banks"))
    try:
        yield registry.STORE
    finally:
        registry.use_store(previous)


class TestTheRankingViewCannotLeakAQuestion:
    """The second boundary of the bank surface, asserted on the type.

    Selection ranks the whole eligible pool on every step. If `item_refs` carried payloads,
    the path that must never read a question would receive every one of them on every
    decision. Over HTTP this was two endpoints with different authorisation; in one process
    it is two functions with two return types, and `BankItemRef` has no payload FIELD to
    populate — which is a stronger boundary than the one it replaces, not a weaker one.
    """

    def test_items_carry_parameters_and_nothing_else(self):
        _version, items = catalogue.item_refs("DA")
        assert items
        for entry in items:
            assert set(entry.model_dump()) == {
                "item_id",
                "modality",
                "measures",
                "cat",
                "estimated_time_seconds",
                "minimum_success_confidence",
                # `status` is a SELECTION fact, not a payload: it says whether this item may
                # be administered at all. Omitting it was a defect — a retired item came
                # back indistinguishable from a live one.
                "status",
            }

    def test_a_retired_item_says_so(self):
        """`JAI-600` retires 64 code items as `inactive_missing_hidden_tests` — their test
        suite is incomplete, so grading against them scores a candidate on a question the
        bank knows is broken."""
        items = catalogue.items("JAI-600")
        retired = [i for i in items if i.status != "active"]
        assert len(retired) == 64
        assert all(i.status == "inactive_missing_hidden_tests" for i in retired)

    def test_no_stem_option_or_answer_appears_anywhere_in_the_ranking_view(self):
        """Asserted on the serialised form, so a field that happens to embed a payload
        fails here rather than wherever it is eventually rendered."""
        _version, items = catalogue.item_refs("DA")
        body = json.dumps([i.model_dump() for i in items])
        for forbidden in ("stem", "options", "answer_index", "reference_solution"):
            assert forbidden not in body

    def test_the_rendering_view_does_carry_the_payload(self):
        first = catalogue.items("DA")[0]
        full = catalogue.item("DA", first.item_id)
        assert full.payload
        assert full.competency

    def test_an_unknown_item_is_refused_naming_the_bank(self):
        with pytest.raises(BankUnknown) as caught:
            catalogue.item("DA", "not-an-item")
        assert "DA" in str(caught.value)


class TestVersionsAreServedSoACacheCanBeKeyedOnThem:
    def test_the_parameters_travel_with_their_version(self):
        version, items = catalogue.item_refs("DA")
        assert version
        assert items

    def test_the_version_matches_the_one_in_the_catalogue(self):
        version, _items = catalogue.item_refs("DA")
        assert version == catalogue.summary("DA").version

    def test_distinct_banks_have_distinct_versions(self):
        versions = {b.bank_id: b.version for b in catalogue.banks()}
        assert len(set(versions.values())) == len(versions), versions


class TestTheCatalogue:
    def test_it_lists_the_checked_in_banks_as_seeds(self):
        banks = {b.bank_id: b for b in catalogue.banks()}
        for bank_id in ("DA", "PY", "AIE", "AIE-JR-V3", "JAI-600"):
            assert banks[bank_id].source == "seed", bank_id
            assert banks[bank_id].items > 0
            assert banks[bank_id].mains

    def test_an_unknown_bank_is_refused_naming_the_valid_ones(self):
        with pytest.raises(BankUnknown) as caught:
            catalogue.item_refs("nope")
        message = str(caught.value)
        assert "DA" in message and "AIE" in message


class TestTheGraphAndPolicyViews:
    def test_the_graph_is_served_with_nodes_and_edges(self):
        graph = catalogue.graph("AIE")
        assert len(graph.nodes) == 36
        assert len(graph.edges) == 67

    def test_the_policy_says_why_each_edge_is_inert(self):
        """Propagation ships inert. An operator asking "why is this edge doing nothing?"
        should not have to read three files and do the AND in their head."""
        policy = catalogue.policy("AIE")
        assert policy.edges
        assert not any(e.inference_allowed for e in policy.edges)
        assert policy.inert_because

    def test_a_bank_without_a_graph_says_so_rather_than_erroring(self, store_at):
        """A bank with no graph is legal — the coverage gate simply does not run for it —
        so this returns None rather than raising. Built through the store because no write
        path can produce one: ingest always derives a graph."""
        from cat_engine.engine.services.orchestrator import registry
        from cat_engine.engine.services.orchestrator.bank_store import Validation

        registry.STORE.save(
            bank_id="NOGRAPH",
            title="no graph",
            items=[
                {
                    "item_id": "n1",
                    "modality": "mcq",
                    "measures": [{"variable": "N.1", "weight": 1.0}],
                    "cat": {"a": 1.0, "b": 0.0, "c": 0.25},
                    "mcq": {"stem": "?", "options": ["a", "b"], "answer_index": 1},
                }
            ],
            graph=None,
            coverage_critical_only=None,
            validation=Validation(bank_id="NOGRAPH", items=1, mains=["N"]),
        )
        registry.reset_caches()

        assert catalogue.graph("NOGRAPH") is None
        assert catalogue.policy("NOGRAPH") is None
        assert catalogue.summary("NOGRAPH").has_graph is False
