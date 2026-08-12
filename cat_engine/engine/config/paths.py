"""Where the engine's data lives.

BANKS AND GRAPHS ARE DATA, NOT CODE

Every bank, competency graph, rubric and calibration file was reachable only at
`engine/data/`, derived from `__file__`. That is correct for a checkout and wrong for a
container: it makes shipping a new bank a rebuild of the image rather than a change to a
mounted volume, and it means four services that must agree about a bank each carry their
own copy of it.

`ENGINE_DATA_DIR` moves that directory without moving the code. Unset — which is every
checkout, every test run and the evaluation harness — it resolves to the packaged
`engine/data/`, so nothing that exists today changes behaviour.

WHY THIS READS THE ENVIRONMENT DIRECTLY AND NOT `Settings`

`Settings` is engine POLICY: what a stopping rule targets, how much authority the model
holds. This is deployment TOPOLOGY, and it has to resolve before anything that reads a
file — including the bank-registry's store, which is constructed at import. Routing it
through `Settings` would add an import-order dependency between configuration and data
for no benefit. `Settings.engine_data_dir` delegates here, so `/config` still reports one
answer rather than two.
"""

from __future__ import annotations

import os
from pathlib import Path

# cat_engine/engine — the engine package root, whatever it is installed as.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]

PACKAGED_DATA_DIR = PACKAGE_ROOT / "data"


def _resolve_data_dir() -> Path:
    configured = os.environ.get("ENGINE_DATA_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else PACKAGED_DATA_DIR


#: The directory every bank, graph, rubric and calibration file is read from.
DATA_DIR = _resolve_data_dir()


def data_path(*parts: str) -> Path:
    """A path inside the engine data directory.

    A function as well as the constant, so a caller that wants to honour a mid-process
    change to `ENGINE_DATA_DIR` (the service test suites do) has something to call.
    """
    return _resolve_data_dir().joinpath(*parts)
