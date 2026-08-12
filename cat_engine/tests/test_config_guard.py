"""What two modules in one process may and may not disagree about.

THE LIMITATION, STATED PRECISELY

The engine reads its policy from a module-level singleton, imported by name at ~100 call
sites. `CatConfig` is applied onto that singleton rather than threaded through, so
**measurement policy is process-wide.**

That is narrower than "one configuration per process", and the difference matters to a host
deciding whether it can run two modules at once:

    measurement policy   SHARED. A second module that disagrees raises.
    topology             PER INSTANCE. Store locations, which surfaces are open,
                         upload ceilings — two modules differ freely.

The split is not a convenience. Measurement settings decide what a candidate is SCORED by;
topology decides where things are read from. A fingerprint that flagged a changed directory
would be ignored within a week, and one that missed a changed stopping rule would be worse
than useless.

WHY IT RAISES RATHER THAN MERGING

A grader running `CODE_APPROACH=C` beside an orchestrator that believes it is `B` produces a
session whose scores were computed one way and whose stopping rule assumed another:
internally consistent, entirely wrong, and nothing failing. Across seven services that was a
risk the fingerprint made visible. Inside one process, letting the second module quietly win
would make it a certainty.
"""

from __future__ import annotations

import pytest

from cat_engine import AssessmentModule, CatConfig
from cat_engine.config import _reset_for_tests, applied_fingerprint
from cat_engine.errors import ConfigConflict


@pytest.fixture(autouse=True)
def _clean_process():
    """Each test starts as the first module in the process, and leaves it that way.

    The bank store is restored too, and that is not paranoia: building a module with
    `bank_store_dir` set calls `registry.use_store`, which is PROCESS-WIDE. Without this,
    every test after these ones resolved banks out of a `tmp_path` that pytest had already
    deleted — 90 failures in files that had nothing to do with configuration.
    """
    from cat_engine.engine.services.orchestrator import registry

    previous_store = registry.STORE
    _reset_for_tests()
    try:
        yield
    finally:
        _reset_for_tests()
        registry.use_store(previous_store)


class TestMeasurementPolicyIsShared:
    def test_a_second_module_agreeing_is_fine(self):
        AssessmentModule(CatConfig(cat_max_questions=12))
        AssessmentModule(CatConfig(cat_max_questions=12))

    def test_a_second_module_disagreeing_raises(self):
        AssessmentModule(CatConfig(cat_max_questions=12))
        with pytest.raises(ConfigConflict) as caught:
            AssessmentModule(CatConfig(cat_max_questions=20))
        assert caught.value.code == "config_conflict"

    def test_the_refusal_names_the_setting_and_both_values(self):
        """A refusal nobody can act on gets retried unchanged. This one has to say WHICH
        setting differs and what each side wanted, because the caller is looking at two
        `CatConfig` literals in different files."""
        AssessmentModule(CatConfig(cat_max_questions=12))
        with pytest.raises(ConfigConflict) as caught:
            AssessmentModule(CatConfig(cat_max_questions=20))
        message = str(caught.value)
        assert "cat_max_questions" in message
        assert "12" in message and "20" in message

    def test_the_first_module_is_left_exactly_as_it_was(self):
        """The guard computes what WOULD be in force before mutating anything.

        Applying and rolling back would leave a window in which a concurrent request read
        the wrong policy — and this is a measurement setting, so that request would be
        scored under a configuration nobody chose and nothing would fail.
        """
        from cat_engine.engine.config.settings import settings as engine_settings

        AssessmentModule(CatConfig(cat_max_questions=12))
        before = applied_fingerprint()

        with pytest.raises(ConfigConflict):
            AssessmentModule(CatConfig(cat_max_questions=20))

        assert engine_settings.cat_max_questions == 12
        assert applied_fingerprint() == before

    @pytest.mark.parametrize(
        "field,first,second",
        [
            ("cat_se_target", 0.55, 0.40),
            ("code_approach", "B", "C"),
            ("graph_coverage_critical_only", False, True),
            ("orchestrator_max_items", 120, 60),
        ],
    )
    def test_every_kind_of_measurement_setting_is_guarded(self, field, first, second):
        """Not just the one the first test happens to use. Each of these changes a number a
        candidate is scored by or a rule that decides when to stop."""
        AssessmentModule(CatConfig(**{field: first}))
        with pytest.raises(ConfigConflict):
            AssessmentModule(CatConfig(**{field: second}))


class TestTopologyIsPerInstance:
    """The half a host is most likely to need, and the half the guard must NOT block."""

    def test_two_modules_may_differ_on_which_surfaces_are_open(self, tmp_path):
        """A host serving candidates and an internal authoring tool in one process is the
        obvious case: one needs diagnostics, the other must never expose them."""
        candidate_facing = AssessmentModule(CatConfig(author_diagnostics_enabled=False))
        author_facing = AssessmentModule(CatConfig(author_diagnostics_enabled=True))

        assert candidate_facing.settings.author_diagnostics_enabled is False
        assert author_facing.settings.author_diagnostics_enabled is True

    def test_two_modules_may_differ_on_where_banks_live(self, tmp_path):
        a = AssessmentModule(CatConfig(bank_store_dir=str(tmp_path / "a")))
        b = AssessmentModule(CatConfig(bank_store_dir=str(tmp_path / "b")))
        assert a.settings.bank_store_dir != b.settings.bank_store_dir


class TestBankStoreDirActuallyWorks:
    """It did not, and nothing noticed.

    `bank_store_dir` arrives as TEXT — from the environment, or from a `CatConfig` literal —
    and `BankStore` calls `.is_dir()` on it. Every host that configured `BANK_STORE_DIR` got
    `AttributeError: 'str' object has no attribute 'is_dir'` at construction. It is a
    documented setting on the module's own surface, and the first thing ever to set one was
    the test above.
    """

    def test_a_module_configured_with_a_store_directory_builds(self, tmp_path):
        module = AssessmentModule(CatConfig(bank_store_dir=str(tmp_path / "banks")))
        assert module.banks(), "the seeds are not visible through a configured store"

    def test_the_configured_directory_is_the_one_used(self, tmp_path):
        from cat_engine.engine.services.orchestrator import registry

        target = tmp_path / "banks"
        AssessmentModule(CatConfig(bank_store_dir=str(target)))
        assert registry.STORE.store_dir == target

    def test_a_bank_uploaded_through_it_lands_there_and_is_assessable(self, tmp_path):
        """The whole point of the setting: uploads must not land in the packaged data
        directory, where the next run would discover a bank no fixture created."""
        import json

        target = tmp_path / "banks"
        module = AssessmentModule(CatConfig(bank_store_dir=str(target)))
        bank = {
            "schema_version": 2,
            "competency": "a bank in a configured store",
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
        receipt = module.upload_bank("ZBANK", json.dumps(bank).encode())
        assert receipt.status == "registered", receipt.error
        assert module.bank("ZBANK").items == 3
        assert target.exists(), "the upload did not land in the configured directory"

    def test_two_modules_may_differ_on_the_write_paths(self):
        closed = AssessmentModule(CatConfig(ingest_api_enabled=False))
        open_ = AssessmentModule(CatConfig(ingest_api_enabled=True))
        assert closed.settings.ingest_api_enabled is False
        assert open_.settings.ingest_api_enabled is True

    def test_topology_does_not_move_the_fingerprint(self):
        """The check that keeps the two halves apart. If a store location entered the
        fingerprint, every host running two modules would hit a conflict it could do
        nothing about."""
        AssessmentModule(CatConfig(cat_max_questions=12))
        before = applied_fingerprint()
        AssessmentModule(
            CatConfig(
                cat_max_questions=12,
                author_diagnostics_enabled=True,
                ingest_api_enabled=False,
                max_upload_bytes=1024,
            )
        )
        assert applied_fingerprint() == before


class TestTheConfigSurfaceRefusesWhatItCannotApply:
    def test_an_unknown_field_is_refused_by_the_model(self):
        """`extra="forbid"`. A typo would otherwise be silently ignored, and the host would
        run the default believing it had changed something."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CatConfig(cat_max_question=12)  # singular: a real typo

    def test_an_unset_field_means_do_not_override(self):
        """Not "set to empty". That distinction is what lets a host set three things and
        leave the rest to the environment."""
        assert CatConfig().overrides() == {}
        assert CatConfig(active_bank="DA").overrides() == {"active_bank": "DA"}
