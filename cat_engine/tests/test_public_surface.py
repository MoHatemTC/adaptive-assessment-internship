"""What `import cat_engine` gives a host, and what it deliberately does not.

The package re-exports lazily so that importing it does not drag in numpy, a sandbox client
and 600 KB of question banks before a host has decided it wants them. That is a real
benefit and a real risk: a lazy export is a name that only exists when somebody asks for it,
so nothing catches a typo in the mapping until a host hits it.
"""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest

import cat_engine


def test_all_and_the_lazy_mapping_agree():
    """`__all__` is spelled out for tools; `_EXPORTS` is what `__getattr__` reads.

    Two lists that must say the same thing, so the duplication is asserted rather than
    trusted. A name in one and not the other is either a public name that cannot be
    imported, or an importable name nothing documents.
    """
    declared = set(cat_engine.__all__) - {"__version__"}
    mapped = set(cat_engine._EXPORTS)
    assert declared == mapped, {
        "in __all__ but not importable": sorted(declared - mapped),
        "importable but undeclared": sorted(mapped - declared),
    }


@pytest.mark.parametrize("name", sorted(set(cat_engine.__all__) - {"__version__"}))
def test_every_public_name_actually_resolves(name):
    """The failure a lazy export makes possible: a mapping entry pointing nowhere.

    `getattr` is what a host's `from cat_engine import X` does, so this is the same code
    path rather than an approximation of it.
    """
    resolved = getattr(cat_engine, name)
    assert resolved is not None
    assert resolved.__name__ == name


def test_dir_lists_the_public_surface():
    """`dir()` drives autocomplete. A lazy package that does not implement `__dir__`
    autocompletes to nothing, which is how a host concludes there is no API."""
    listed = set(dir(cat_engine))
    assert set(cat_engine.__all__) <= listed


def test_an_unknown_name_is_an_attribute_error_not_an_import_error():
    """A typo should say what it is. `__getattr__` that let something else escape would
    report a missing dependency for a name that was simply misspelled."""
    with pytest.raises(AttributeError) as caught:
        cat_engine.NoSuchThing  # noqa: B018 - the attribute access IS the assertion
    assert "NoSuchThing" in str(caught.value)


def test_importing_the_package_does_not_import_the_engine():
    """The whole point of the laziness.

    Run in a subprocess because this process has long since imported everything. If
    `import cat_engine` pulled the engine in, a host that only wanted to read
    `__version__` would pay for numpy, the bank parser and every schema.
    """
    code = (
        "import sys, cat_engine;"
        "heavy = [m for m in sys.modules if m.startswith('cat_engine.engine')];"
        "print(len(heavy))"
    )
    # `code` is the literal above and the interpreter is this one. Nothing here is
    # untrusted; the subprocess exists to get a PRISTINE import, which is the whole
    # measurement — `sys.modules` in this process is already full of engine modules.
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "0", (
        f"importing cat_engine pulled in {out.stdout.strip()} engine modules; the lazy "
        "re-export has stopped being lazy"
    )


def test_the_error_hierarchy_is_reachable_without_touching_the_engine():
    """A host maps exceptions onto responses in its error handler, which runs on paths that
    may never have built a module. Importing them must not require the engine."""
    code = (
        "import sys;"
        "from cat_engine.errors import CatError, StaleAnswer, AnswerTypeMismatch;"
        "assert issubclass(StaleAnswer, CatError);"
        "print(len([m for m in sys.modules if m.startswith('cat_engine.engine')]))"
    )
    # `code` is the literal above and the interpreter is this one. Nothing here is
    # untrusted; the subprocess exists to get a PRISTINE import, which is the whole
    # measurement — `sys.modules` in this process is already full of engine modules.
    out = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "0", "importing the errors pulled in the engine"


def test_every_error_carries_a_code_and_a_status():
    """The contract a host maps onto responses. An exception without them would make the
    handler guess, and a guessed 500 for a candidate's bad answer is a support ticket."""
    errors = importlib.import_module("cat_engine.errors")
    for name in errors.__all__:
        kind = getattr(errors, name)
        if not (isinstance(kind, type) and issubclass(kind, errors.CatError)):
            continue
        instance = kind("something went wrong")
        assert instance.code, f"{name} has no code"
        assert isinstance(instance.status_code, int), f"{name} has no status"
        assert instance.detail, f"{name} has no detail"
