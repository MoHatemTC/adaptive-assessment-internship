"""The engine's data directory is relocatable, and nothing bypasses it.

WHY THIS SUITE EXISTS

Before `app.config.paths`, seven modules each derived `app/data/` from their own
`__file__` with a different number of `.parent`s. That works in a checkout and fails in
every deployment where banks are a mounted volume rather than part of the image — and it
fails silently, as an empty bank list rather than as an error.

The assertions here are therefore about the SHAPE of the resolution, not about any one
path: that one module decides, that the environment can move it, and that no module has
quietly gone back to deriving its own.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

from cat_engine.engine.config.paths import DATA_DIR, PACKAGED_DATA_DIR, data_path
from cat_engine.engine.config.settings import settings
from cat_engine.engine.services.orchestrator import registry


class TestTheDefaultIsTheCheckout:
    def test_unset_resolves_to_the_packaged_data_directory(self, monkeypatch):
        """Every checkout, every test run and the evaluation harness take this path."""
        monkeypatch.delenv("ENGINE_DATA_DIR", raising=False)
        module = importlib.reload(importlib.import_module("cat_engine.engine.config.paths"))
        assert module.DATA_DIR == PACKAGED_DATA_DIR

    def test_the_packaged_directory_actually_holds_the_banks(self):
        assert (PACKAGED_DATA_DIR / "question_bank_AIE.json").is_file()
        assert (PACKAGED_DATA_DIR / "rubrics").is_dir()


class TestTheEnvironmentCanMoveIt:
    def test_data_path_honours_a_mid_process_change(self, monkeypatch, tmp_path):
        """`data_path()` re-reads, which is what lets a service test point at a fixture.

        The module constant is resolved once at import — correct for a process whose
        configuration is fixed at startup, which is every deployment. The function exists
        for the callers that are not that.
        """
        monkeypatch.setenv("ENGINE_DATA_DIR", str(tmp_path))
        assert data_path("question_bank.json") == tmp_path / "question_bank.json"

    def test_a_relocated_directory_actually_serves_a_bank(self, tmp_path):
        """The end-to-end claim, proved in a fresh interpreter.

        `DATA_DIR` is an import-time constant, so setting the variable inside this process
        proves nothing about a container that sets it before start. A subprocess is the
        only honest test of the mechanism the whole mounted-volume story rests on — and it
        is the same reason `evaluation/sweep.py` spawns `run_arm` rather than importing it.
        """
        relocated = tmp_path / "banks"
        relocated.mkdir()
        for name in ("question_bank.json", "competency_graph.json"):
            shutil.copy(PACKAGED_DATA_DIR / name, relocated / name)

        probe = textwrap.dedent("""
            from cat_engine.engine.config.paths import DATA_DIR
            from cat_engine.engine.services.orchestrator import registry
            bank = registry.get_bank("DA")
            print(DATA_DIR, len(bank.all_items()), sep="|")
        """)
        environment = {
            **os.environ,
            "ENGINE_DATA_DIR": str(relocated),
            "PYTHONPATH": str(PACKAGED_DATA_DIR.parents[1]),
        }
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, "-c", probe],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        resolved, item_count = completed.stdout.strip().split("|")
        assert Path(resolved) == relocated
        assert int(item_count) > 0, "relocated bank loaded but served no items"

    def test_settings_reports_one_answer_rather_than_two(self):
        """`/config` is the first thing an operator checks when a bank list comes back
        empty. It must report the directory actually in force, not a second field that
        could disagree with it."""
        assert settings.engine_data_dir == str(DATA_DIR)


class TestNoModuleDerivesItsOwnDataDirectory:
    """A regression guard with teeth: the failure this prevents is silent.

    A module that goes back to `Path(__file__).parents[n] / "data"` keeps working in a
    checkout and stops seeing the mounted volume in production, where the symptom is an
    empty bank list rather than an exception.
    """

    def test_every_engine_data_path_lives_under_the_resolved_directory(self):
        from cat_engine.engine.services.adaptive.bank import DEFAULT_BANK_PATH
        from cat_engine.engine.services.code_adaptive.bank import BANK_PATH as CODE_BANK_PATH
        from cat_engine.engine.services.code_adaptive.prompts import RUBRIC_DIR as CODE_RUBRIC_DIR
        from cat_engine.engine.services.competency_graph import DEFAULT_GRAPH_PATH
        from cat_engine.engine.services.orchestrator.bank import BANK_PATH
        from cat_engine.engine.services.orchestrator.selection_calibration import CALIBRATION_PATH
        for path in (
            DEFAULT_BANK_PATH,
            CODE_BANK_PATH,
            CODE_RUBRIC_DIR,
            DEFAULT_GRAPH_PATH,
            BANK_PATH,
            CALIBRATION_PATH,
        ):
            assert DATA_DIR in Path(path).parents, f"{path} escapes {DATA_DIR}"

    def test_every_registered_bank_and_graph_resolves_through_it(self, any_profile):
        assert DATA_DIR in any_profile.bank_path.parents
        if any_profile.graph_path is not None:
            assert DATA_DIR in any_profile.graph_path.parents

    def test_the_registry_module_constant_is_the_shared_one(self):
        assert registry.DATA == DATA_DIR

    def test_no_engine_module_still_derives_data_from_dunder_file(self):
        """Asserted on the source, because an import-time constant computed the old way
        is indistinguishable from the new one in a checkout — the two only differ once
        `ENGINE_DATA_DIR` is set, which no test environment does by default."""
        engine_root = PACKAGED_DATA_DIR.parent
        offenders = []
        for source in engine_root.rglob("*.py"):
            if "__pycache__" in source.parts or source.name == "paths.py":
                continue
            for number, line in enumerate(
                source.read_text(encoding="utf-8").splitlines(), 1
            ):
                stripped = line.split("#")[0]
                if '"data"' in stripped and "__file__" in stripped:
                    offenders.append(f"{source.relative_to(engine_root)}:{number}")
        assert not offenders, (
            f"these derive the data directory from __file__ instead of "
            f"cat_engine.engine.config.paths: {offenders}"
        )
