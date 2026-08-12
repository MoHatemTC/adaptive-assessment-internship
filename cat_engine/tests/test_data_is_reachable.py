"""Every file shipped in `engine/data/` must be reachable from code.

WHY THIS TEST EXISTS

107 rubric files, `grading_rubric.json` and `open_grading_rubric.json` — 960 KB, a fifth of
the packaged data — sat in `data/` belonging to a bank that had been replaced. Nothing
referenced them, nothing failed when they were removed, and no test noticed for however
long they had been there. They shipped in every wheel.

Grep could not have found them: a rubric was reached by ID from an item payload, so no
import ever named one. Reachability has to be computed the way the code computes it — from
the bank profiles and the module constants — which is what this does.

The failure it prevents is not a crash. It is a package that quietly grows a directory of
files nobody can explain, until somebody deletes the wrong one.

HOW TO ADD A FILE

Register it: name it in a `BankProfile`, or point a module constant at it. If it is neither
— if it is a fixture, a sample, or a thing you are keeping just in case — it does not belong
in the packaged data directory, because `pyproject.toml` ships that directory to every host.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cat_engine.engine.config.paths import PACKAGED_DATA_DIR


def reachable_paths() -> set[Path]:
    """Everything the code can actually open, resolved the way the code resolves it."""
    from cat_engine.engine.services.adaptive.bank import DEFAULT_BANK_PATH
    from cat_engine.engine.services.code_adaptive.bank import BANK_PATH as CODE_BANK
    from cat_engine.engine.services.code_adaptive.prompts import RUBRIC_DIR as CODE_RUBRICS
    from cat_engine.engine.services.competency_graph import DEFAULT_GRAPH_PATH
    from cat_engine.engine.services.orchestrator.bank import BANK_PATH
    from cat_engine.engine.services.orchestrator.bank_store import _seed_profiles
    from cat_engine.engine.services.orchestrator.selection_calibration import (
        CALIBRATION_PATH,
    )

    found: set[Path] = set()

    # The registered banks and their graphs.
    for profile in _seed_profiles().values():
        found.add(Path(profile.bank_path).resolve())
        if profile.graph_path is not None:
            found.add(Path(profile.graph_path).resolve())

    # Module-level defaults. Each is the path some repository or loader opens when no
    # caller says otherwise.
    for constant in (DEFAULT_BANK_PATH, CODE_BANK, DEFAULT_GRAPH_PATH, BANK_PATH):
        found.add(Path(constant).resolve())

    # Directories whose whole contents are selected by configuration: CODE_RUBRIC picks one
    # of `rubrics/`, so every file in it is reachable by some setting.
    for directory in (CODE_RUBRICS,):
        found |= {p.resolve() for p in Path(directory).rglob("*") if p.is_file()}

    # Written by a calibration run rather than shipped. Included so that a checkout which
    # HAS one does not fail; its absence is not an error.
    found.add(Path(CALIBRATION_PATH).resolve())

    return found


#: Documentation. It lives beside the data because that is where somebody looking at the
#: data will be, and it does NOT ship — `pyproject.toml` declares package-data as `*.json`
#: only, which `test_the_readme_does_not_ship` below holds it to.
NOT_DATA = {"README.md"}


def shipped_files() -> list[Path]:
    return sorted(
        p.resolve()
        for p in PACKAGED_DATA_DIR.rglob("*")
        if p.is_file() and p.name not in NOT_DATA
    )


@pytest.mark.parametrize(
    "shipped", shipped_files(), ids=lambda p: str(p.relative_to(PACKAGED_DATA_DIR))
)
def test_every_shipped_data_file_is_reachable(shipped):
    assert shipped in reachable_paths(), (
        f"{shipped.relative_to(PACKAGED_DATA_DIR)} ships in every wheel and no code path "
        "opens it. Register it in a BankProfile or a module constant, or delete it."
    )


def test_every_reachable_path_that_should_exist_does():
    """The other direction: a constant naming a file that is not there.

    `CALIBRATION_PATH` is exempt — it is produced by a calibration run rather than shipped,
    and its absence is the normal state of a checkout.
    """
    from cat_engine.engine.services.orchestrator.selection_calibration import (
        CALIBRATION_PATH,
    )

    optional = {Path(CALIBRATION_PATH).resolve()}
    missing = [
        p for p in reachable_paths() if p not in optional and not p.exists()
    ]
    assert not missing, f"code points at data files that do not exist: {missing}"


def test_the_data_directory_ships_nothing_but_json():
    """A stray notebook, archive or fixture in here would ship to every host."""
    strays = [
        p.relative_to(PACKAGED_DATA_DIR)
        for p in shipped_files()
        if p.suffix != ".json"
    ]
    assert not strays, f"non-JSON files in the packaged data directory: {strays}"


def test_the_readme_does_not_ship():
    """The exemption above is only safe while packaging really is JSON-only.

    `README.md` sits in the data directory so that somebody looking at the banks finds it,
    and it is exempted from the reachability check for that reason. That exemption would
    become a hole the moment package-data widened to `*`, so the declaration is asserted
    rather than trusted.
    """
    import tomllib

    root = Path(__file__).resolve().parents[2]
    with (root / "pyproject.toml").open("rb") as handle:
        config = tomllib.load(handle)

    patterns = config["tool"]["setuptools"]["package-data"]["cat_engine.engine"]
    assert all(p.endswith(".json") for p in patterns), (
        f"package-data for the engine is no longer JSON-only ({patterns}); README.md and "
        "anything else non-JSON in engine/data/ now ships to every host"
    )
