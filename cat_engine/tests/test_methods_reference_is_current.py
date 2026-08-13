"""`METHODS.md` names every method a host can call, and names none that is gone.

WHY THIS IS A TEST

`METHODS.md` at the repository root is the complete callable surface — one entry per method,
its inputs, its outputs. A reference like that is worth exactly as much as its currency: a
host reads it, calls what it describes, and a method added last month that nobody wrote down
is a method nobody outside this repository knows exists.

So two claims, and they decay in opposite directions:

  1. Every public name on `AssessmentModule`, `Live`, `SessionStore` and `CatConfig` appears
     in the reference.
  2. Every method the reference documents still exists.

WHAT IS DELIBERATELY NOT CHECKED

That the description is right, that the parameter table matches the signature, or that the
stated return type is the real one. No test can check prose. What a test can check is that
the surface and the document were changed together — which is how a reference like this rots
in practice: not by becoming wrong, but by becoming incomplete while still reading as
complete.

The name match is a whole-word search over headings and table rows only. Prose elsewhere in
the file may legitimately mention a method it is not documenting. It is a loose match by
design: a NEW name will not appear by accident, and that is the failure this exists to catch.

Skipped when `METHODS.md` is absent, so the module still passes its own suite when installed
into a host on its own.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REFERENCE = Path(__file__).resolve().parents[2] / "METHODS.md"


def index() -> str:
    """Headings and table rows. The index, as opposed to the prose around it."""
    if not REFERENCE.is_file():
        pytest.skip("METHODS.md lives in the repository, not in the installed package")
    return "\n".join(
        line
        for line in REFERENCE.read_text(encoding="utf-8").splitlines()
        if line.startswith("#") or line.startswith("|")
    )


def public_names(obj) -> list[str]:
    return sorted(name for name in dir(obj) if not name.startswith("_"))


def documented(name: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", text) is not None


def assessment_module_names() -> list[str]:
    from cat_engine.facade import AssessmentModule

    return public_names(AssessmentModule)


def live_names() -> list[str]:
    from cat_engine.live import Live

    return public_names(Live)


def session_store_names() -> list[str]:
    from cat_engine.stores import SessionStore

    return public_names(SessionStore)


def config_field_names() -> list[str]:
    from cat_engine.config import CatConfig

    return sorted(CatConfig.model_fields)


def error_names() -> list[str]:
    from cat_engine import errors

    return sorted(errors.__all__)


@pytest.mark.parametrize("name", assessment_module_names())
def test_every_module_method_is_documented(name):
    assert documented(name, index()), (
        f"METHODS.md does not document `AssessmentModule.{name}`. It is the complete "
        "callable surface; a method missing from it is a method a host has no way to "
        "learn about."
    )


@pytest.mark.parametrize("name", live_names())
def test_every_live_method_is_documented(name):
    assert documented(name, index()), f"METHODS.md does not document `Live.{name}`."


@pytest.mark.parametrize("name", session_store_names())
def test_every_session_store_method_is_documented(name):
    """A host implementing the Protocol needs every method, not most of them.

    A missing one is not a documentation gap — it is a store that raises `AttributeError`
    the first time an assessment reaches the path nobody wrote down.
    """
    assert documented(name, index()), (
        f"METHODS.md does not document `SessionStore.{name}`, which a host must implement."
    )


@pytest.mark.parametrize("name", config_field_names())
def test_every_config_field_is_documented(name):
    """`CatConfig` forbids extra fields, so an undocumented one is unreachable in practice.

    A host cannot guess a field name that `extra="forbid"` will reject on a typo, which
    makes the reference the only way to find out a lever exists at all.
    """
    assert documented(name, index()), f"METHODS.md does not document `CatConfig.{name}`."


@pytest.mark.parametrize("name", error_names())
def test_every_exported_error_is_documented(name):
    assert documented(name, index()), (
        f"METHODS.md does not document `{name}`. Every exported error is something a host "
        "is expected to branch on."
    )


def test_the_reference_documents_no_method_that_is_gone():
    """The other direction, and the one that actually rots.

    Methods are removed far more often than they are renamed, and a reference describing a
    call that no longer exists sends a host looking for something deleted on purpose.

    Read from the `###` headings only — those are the entries. A table row may legitimately
    name a method of some other object, and the prose may name one in order to say it went.
    """
    text = index()
    known = set(
        assessment_module_names()
        + live_names()
        + session_store_names()
        + config_field_names()
        + error_names()
        + [
            # Documented deliberately and defined elsewhere: room methods, module-level
            # helpers, and the two `CatConfig` methods that are not fields.
            "connect", "push_pcm", "events", "finish", "set_turn_state",
            "activity_start", "activity_end", "mark_pause", "mark_resume",
            "turn_config", "as_package",
            "open_session_store", "applied_fingerprint", "overrides", "apply",
            "AssessmentModule", "SyncAssessmentModule", "CatConfig",
        ]
    )
    headings = [
        line for line in text.splitlines() if line.startswith("###")
    ]
    documented_calls = set()
    for line in headings:
        documented_calls.update(re.findall(r"`(?:async )?(?:[\w.]+\.)?(\w+)\(", line))

    ghosts = sorted(documented_calls - known)
    assert not ghosts, (
        f"METHODS.md documents methods that no longer exist: {ghosts}. A reference "
        "describing a call that was deleted on purpose is worse than one that is silent."
    )
