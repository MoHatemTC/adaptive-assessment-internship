"""A declared setting that nothing reads is a promise the deployment cannot collect on.

THE BUG CLASS

`Settings` is the deployment's whole vocabulary. Each field is documented, appears in
`.env.example`, and reads as a lever. A field that no branch consults is worse than a missing
feature: an operator sets it, sees no error, and believes the system now behaves differently.

This has happened here twice.

    cat_band_probability_stop_enabled   was live, documented and unreachable, because the
                                        `band_probability` argument was omitted at both call
                                        sites so the rule saw None whatever the flag said.
                                        `variables.evaluate_finalisation` still carries the
                                        comment.

    cat_aberrance_drives_verification   was declared, documented, in `.env.example`, and read
                                        by nothing at all. `personfit` computed the residual,
                                        the report carried the count, and the setting that
                                        claimed to act on it was inert.

Both were found by hand, once each. This finds the whole class, every run.

WHAT COUNTS AS BEING READ

Either something outside `settings.py` names the field, or an accessor inside `settings.py`
reads it — several fields are deliberately private behind a parser (`modality_minimums()`
parses `orchestrator_modality_minimums`, `interval_widening_for()` reads three fields), and
those are live even though no caller ever names them.

`fingerprint.py` does NOT count. It lists setting names to hash them; listing a name is not
consulting a value, and a setting that only appears there is exactly the inert kind.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from cat_engine.engine.config.settings import Settings

MODULE = Path(__file__).resolve().parents[1]
SETTINGS_FILE = MODULE / "engine" / "config" / "settings.py"
#: Lists names to hash them. Appearing here is not evidence anything reads the value.
NAMES_ONLY = {MODULE / "engine" / "config" / "fingerprint.py"}


def accessor_bodies() -> str:
    """Every method body in `Settings`, where a privately-parsed field is read."""
    tree = ast.parse(SETTINGS_FILE.read_text())
    chunks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    chunks.append(ast.unparse(item))
    return "\n".join(chunks)


def readers_of(name: str, corpus: dict[Path, str], accessors: str) -> list[str]:
    hits = [
        str(path.relative_to(MODULE))
        for path, text in corpus.items()
        if re.search(rf"\b{re.escape(name)}\b", text)
    ]
    if re.search(rf"\bself\.{re.escape(name)}\b", accessors):
        hits.append("settings.py (accessor)")
    return hits


@pytest.fixture(scope="module")
def corpus() -> dict[Path, str]:
    """Every shipped source file except the declaration itself and the name lists."""
    found = {}
    for path in MODULE.rglob("*.py"):
        if path == SETTINGS_FILE or path in NAMES_ONLY:
            continue
        if "__pycache__" in path.parts or path.parts[len(MODULE.parts)] == "tests":
            continue
        found[path] = path.read_text()
    return found


@pytest.fixture(scope="module")
def accessors() -> str:
    return accessor_bodies()


DECLARED = sorted(Settings.model_fields)


def test_the_scan_finds_the_settings_at_all():
    """A guard on the guard: a regex that matched nothing would pass everything."""
    assert len(DECLARED) > 80, DECLARED


@pytest.mark.parametrize("name", DECLARED)
def test_every_declared_setting_is_read_by_something(name, corpus, accessors):
    """The whole point. A lever wired to nothing is a lie told to an operator."""
    where = readers_of(name, corpus, accessors)
    assert where, (
        f"`{name}` is declared on Settings and no shipped code reads it. Either wire it "
        f"up or delete it — a documented setting that changes nothing is worse than an "
        f"absent one, because a deployment will set it and believe it took effect."
    )


def test_appearing_only_in_the_fingerprint_list_does_not_count(corpus, accessors):
    """The exemption that would have hidden both historical bugs.

    `fingerprint.py` names ~40 settings in a tuple. If that counted as a read, every one of
    them would pass this file whatever the rest of the engine did with them.
    """
    assert not any(path in corpus for path in NAMES_ONLY)
    assert readers_of("cat_band_probability_stop_conjunctive", corpus, accessors), (
        "this setting IS read behaviourally; if this assertion fails the exemption above "
        "has been widened too far"
    )
