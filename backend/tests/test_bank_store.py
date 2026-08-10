"""Banks arrive over HTTP now. This is what stops a bad one measuring anybody.

THE THING THIS SUITE IS REALLY ABOUT

Before, a bank was added by editing a Python dict and redeploying, so every bank that ever
reached the engine had been read by a person and had passed `test_bank_registry.py` in CI.
Neither is true of a bank posted by another service. The validation here is those same
invariants, moved from "asserted of the five checked-in banks" to "enforced at the door" —
otherwise the engine's own suite is testing the seeds rather than the system.
"""

from __future__ import annotations

import json

import pytest

from app.services.orchestrator import registry
from app.services.orchestrator.bank_store import (
    BankStore,
    BankStoreError,
    UnknownBankError,
    _seed_profiles,
)

QUESTION_BUDGET = 12


def item(item_id: str, variable: str, *, active: bool = True, a: float = 1.0) -> dict:
    return {
        "item_id": item_id,
        "modality": "mcq",
        "status": "active" if active else "retired",
        "competency": "Example",
        "sub_competency": variable,
        "measures": [{"variable": variable, "weight": 1.0}],
        "cat": {"a": a, "b": 0.0, "c": 0.25},
        "mcq": {"stem": "2 + 2?", "options": ["3", "4"], "answer_index": 1},
    }


def graph(*variables: str, main: str = "X", critical: bool = True) -> dict:
    nodes = [{"competency_id": main, "title": main, "node_type": "main"}]
    nodes += [
        {
            "competency_id": v,
            "title": v,
            "node_type": "sub_competency",
            "main_competencies": [main],
            "critical": critical,
        }
        for v in variables
    ]
    return {
        "version": "1.0",
        "nodes": nodes,
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


@pytest.fixture
def store(tmp_path) -> BankStore:
    """A store over the real seeds with an empty writable overlay."""
    return BankStore(seeds=_seed_profiles(), store_dir=tmp_path / "banks")


def validate(store: BankStore, bank_id: str, items: list[dict], graph_raw: dict | None):
    return store.validate(
        bank_id=bank_id,
        items=items,
        graph=graph_raw,
        coverage_critical_only=False,
        question_budget=QUESTION_BUDGET,
        deployment_critical_only=False,
    )


def register(store: BankStore, bank_id: str, items: list[dict], graph_raw: dict | None):
    report = validate(store, bank_id, items, graph_raw)
    store.save(
        bank_id=bank_id,
        title=bank_id,
        items=items,
        graph=graph_raw,
        coverage_critical_only=False,
        validation=report,
    )
    return report


class TestTheSeedsStillWork:
    """The overlay is additive. Nothing a deployment already had may move."""

    def test_the_five_checked_in_banks_are_registered(self, store):
        assert set(store.profiles()) == {"DA", "PY", "AIE", "AIE-JR-V3", "JAI-600"}

    def test_the_live_registry_view_behaves_like_the_dict_it_replaced(self):
        assert "AIE" in registry.REGISTRY
        assert registry.REGISTRY["AIE"].bank_id == "AIE"
        assert sorted(registry.REGISTRY) == sorted(registry.STORE.profiles())
        assert len(registry.REGISTRY) == len(registry.STORE.profiles())

    def test_an_unknown_bank_still_names_the_valid_ones(self, store):
        with pytest.raises(UnknownBankError, match="AIE"):
            store.profile("not-a-bank")


class TestVersioning:
    """The version is what stops a posted bank changing the pool under a live session."""

    def test_it_is_stable_across_reads(self, store):
        assert store.version("DA") == store.version("DA")

    def test_distinct_banks_have_distinct_versions(self, store):
        assert store.version("DA") != store.version("AIE")

    def test_it_survives_a_cache_reset(self, store):
        """It is a content hash, not a session token. A restart must not invalidate every
        orchestrator cache."""
        before = store.version("DA")
        store.reset()
        assert store.version("DA") == before

    def test_posting_a_bank_moves_its_version(self, store):
        register(store, "X", [item("q1", "X.1")], graph("X.1"))
        first = store.version("X")
        register(store, "X", [item("q1", "X.1"), item("q2", "X.1")], graph("X.1"))
        assert store.version("X") != first

    def test_an_unchanged_bank_is_parsed_once_and_shared(self, store):
        """Identity, not equality. The orchestrator ranks the whole pool on every step and
        reparsing a 1.8 MB bank per decision is the difference between inside the 100 ms
        selection budget and outside it."""
        assert store.bank("DA") is store.bank("DA")

    def test_a_changed_bank_is_reparsed(self, store):
        register(store, "X", [item("q1", "X.1")], graph("X.1"))
        first = store.bank("X")
        register(store, "X", [item("q1", "X.1"), item("q2", "X.1")], graph("X.1"))
        assert store.bank("X") is not first
        assert len(store.bank("X").all_items()) == 2


class TestTheOverlay:
    def test_a_posted_bank_appears_beside_the_seeds(self, store):
        register(store, "NEW", [item("q1", "X.1")], graph("X.1"))
        assert "NEW" in store.profiles()
        assert store.profiles()["NEW"].source == "stored"
        assert store.bank("NEW").variables() == ["X"]

    def test_a_posted_bank_shadows_a_seed_of_the_same_id(self, store):
        register(store, "DA", [item("q1", "X.1")], graph("X.1"))
        assert store.profiles()["DA"].source == "stored"
        assert len(store.bank("DA").all_items()) == 1

    def test_deleting_the_shadow_restores_the_seed(self, store):
        """What makes an accidental overwrite recoverable without a redeploy."""
        register(store, "DA", [item("q1", "X.1")], graph("X.1"))
        assert store.delete("DA") is True
        assert store.profiles()["DA"].source == "seed"
        assert len(store.bank("DA").all_items()) > 1

    def test_deleting_a_bank_that_was_never_posted_reports_so(self, store):
        assert store.delete("AIE") is False

    def test_a_replacement_that_fails_leaves_the_previous_bank_intact(self, store):
        register(store, "X", [item("q1", "X.1")], graph("X.1"))
        broken = validate(store, "X", [], None)
        with pytest.raises(BankStoreError):
            store.save(
                bank_id="X",
                title="X",
                items=[],
                graph=None,
                coverage_critical_only=False,
                validation=broken,
            )
        assert len(store.bank("X").all_items()) == 1


class TestABankIdCannotEscapeTheStore:
    """The one place an id supplied over HTTP becomes a filesystem path."""

    @pytest.mark.parametrize(
        "bank_id",
        ["../escape", "a/b", "..", ".hidden", "", "x" * 65, "with space", "a\\b"],
    )
    def test_it_is_refused(self, store, bank_id):
        assert not validate(store, bank_id, [item("q1", "X.1")], None).accepted
        with pytest.raises(BankStoreError):
            store._directory_for(bank_id)

    def test_a_traversal_never_reaches_the_filesystem(self, store, tmp_path):
        canary = tmp_path / "canary.json"
        canary.write_text("{}", encoding="utf-8")
        with pytest.raises(BankStoreError):
            store._directory_for("../canary")
        assert canary.read_text(encoding="utf-8") == "{}"


class TestValidationRefusesWhatWouldBreakAnAssessment:
    def test_an_out_of_range_discrimination_is_refused(self, store):
        """Not a bank that ranks slightly oddly — one whose information scores are
        meaningless, on every decision, for the life of the bank."""
        report = validate(store, "X", [item("q1", "X.1", a=12.0)], graph("X.1"))
        assert not report.accepted
        assert any(f.code == "item_invalid" for f in report.errors)

    def test_a_duplicate_item_id_is_refused(self, store):
        report = validate(
            store, "X", [item("q1", "X.1"), item("q1", "X.1")], graph("X.1")
        )
        assert any(f.code == "item_id_duplicate" for f in report.errors)

    def test_a_bank_with_no_active_item_is_refused(self, store):
        report = validate(
            store, "X", [item("q1", "X.1", active=False)], graph("X.1")
        )
        assert any(f.code == "no_active_items" for f in report.errors)

    def test_a_graph_that_pairs_with_a_different_bank_is_refused(self, store):
        """The failure this prevents is silent and permanent: every required node reads
        unmeasured and convergence is vetoed for the whole session."""
        report = validate(store, "X", [item("q1", "X.1")], graph("Y.1", main="Y"))
        assert any(f.code == "graph_bank_mismatch" for f in report.errors)

    def test_a_measured_variable_with_no_node_is_refused(self, store):
        report = validate(
            store, "X", [item("q1", "X.1"), item("q2", "X.9")], graph("X.1")
        )
        assert any(f.code == "measured_node_absent" for f in report.errors)

    def test_a_required_node_no_item_measures_is_refused(self, store):
        report = validate(store, "X", [item("q1", "X.1")], graph("X.1", "X.2"))
        assert any(f.code == "required_node_unmeasured" for f in report.errors)

    def test_an_unsatisfiable_coverage_requirement_is_refused(self, store):
        """The guard the AI Engineer bank needed. Eleven required nodes against a
        twelve-question cap leaves no room for the corroboration item a precision stop
        demands, so the gate could never be satisfied and every session would end on the
        budget escape — reporting a stop it never actually chose."""
        variables = [f"X.{n}" for n in range(1, 12)]
        report = validate(
            store,
            "X",
            [item(f"q{n}", v) for n, v in enumerate(variables)],
            graph(*variables),
        )
        assert any(f.code == "coverage_unreachable" for f in report.errors)

    def test_a_cyclic_prerequisite_graph_is_refused(self, store):
        raw = graph("X.1", "X.2")
        raw["edges"] += [
            {"from": "X.1", "to": "X.2", "relation": "PREREQUISITE", "strength": 1.0},
            {"from": "X.2", "to": "X.1", "relation": "PREREQUISITE", "strength": 1.0},
        ]
        report = validate(
            store, "X", [item("q1", "X.1"), item("q2", "X.2")], raw
        )
        assert any(f.code == "graph_invalid" for f in report.errors)

    def test_nothing_is_written_when_validation_fails(self, store):
        report = validate(store, "X", [item("q1", "X.1", a=12.0)], graph("X.1"))
        with pytest.raises(BankStoreError):
            store.save(
                bank_id="X",
                title="X",
                items=[item("q1", "X.1", a=12.0)],
                graph=graph("X.1"),
                coverage_critical_only=False,
                validation=report,
            )
        assert "X" not in store.profiles()


class TestValidationWarnsWithoutRefusing:
    def test_a_bank_with_no_graph_is_accepted_with_a_warning(self, store):
        report = validate(store, "X", [item("q1", "X.1")], None)
        assert report.accepted
        assert any(f.code == "no_graph" for f in report.findings)

    def test_a_warning_never_blocks_registration(self, store):
        register(store, "X", [item("q1", "X.1")], None)
        assert "X" in store.profiles()


class TestAPostedBankIsAssessableEndToEnd:
    """Validation passing is not the same as the engine being able to use it."""

    def test_the_bank_serves_items_and_the_graph_pairs_with_it(self, store):
        register(store, "END2END", [item("q1", "X.1"), item("q2", "X.1")], graph("X.1"))
        bank = store.bank("END2END")
        assert bank.variables() == ["X"]
        assert len(bank.shortlist("X", exclude=set())) == 2
        assert store.graph("END2END") is not None

    def test_the_written_files_are_the_shapes_the_engine_reads(self, store):
        register(store, "ONDISK", [item("q1", "X.1")], graph("X.1"))
        directory = store.store_dir / "ONDISK"
        assert set(json.loads((directory / "bank.json").read_text())) == {"items"}
        assert "nodes" in json.loads((directory / "graph.json").read_text())
        profile = json.loads((directory / "profile.json").read_text())
        assert profile["bank_id"] == "ONDISK"
        assert profile["mains"] == ["X"]
        assert profile["registered_at"]
