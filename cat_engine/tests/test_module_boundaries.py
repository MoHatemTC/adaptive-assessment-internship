"""The one boundary that survives inside a single process.

WHAT THIS REPLACES

`test_services.py` asserted that no service imported another, and that each imported only
its declared slice of the engine — an allowlist per service directory. Most of that claim
dissolved with the services: there are no separate processes to keep apart, and a module
whose parts may not call each other is not a module.

One claim did not dissolve, and it is the one that carried real weight:

    `code_adaptive` EXECUTES UNTRUSTED CANDIDATE CODE.

Exactly one component was ever allowed to import it, and it was the only one with sandbox
egress. That was enforced by packaging — the sandbox client was installed into one image —
and packaging cannot enforce it any more. It is a review property now, which means it is
worth a test, because a review property nobody checks is a convention.

WHAT THIS DOES NOT CLAIM

Not that the sandbox is unreachable. `grading` imports it and must. The claim is that the
parts which decide WHICH QUESTION TO ASK and WHAT TO SHOW A CANDIDATE stay one import away
from it — so a change that lets selection execute code has to widen this list deliberately,
with a failing test attached.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]

#: Modules that decide what is asked, what is shown, or what a scope covers. None of them
#: has any business reaching the thing that runs candidate code.
MUST_NOT_REACH_THE_SANDBOX = (
    "catalogue.py",
    "projection.py",
    "diagnostics.py",
    "settings.py",
    "errors.py",
    "scope/scoping.py",
    "scope/render.py",
    "scope/bank.py",
    "scope/__init__.py",
    "ingest/derive.py",
    "ingest/uploads.py",
    "stores/sessions.py",
)

#: The only files that may import it, and why.
MAY_REACH_THE_SANDBOX = {
    "grading.py": "it is the grader; running the code is the job",
    "wiring.py": "it constructs the grader",
    "facade.py": "it holds the grader and exposes trial runs",
}

SANDBOX = "cat_engine.engine.services.code_adaptive"


def imports_of(source: Path) -> set[str]:
    """Every module this file imports, resolved to a dotted path.

    AST rather than a substring search, because `import x.y` and `from x import y` are the
    same fact spelled two ways, and neither should be found in a docstring that merely
    mentions it — this file's own docstring names `code_adaptive` four times.
    """
    found: set[str] = set()
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
            found.add(node.module)
    return found


def reaches_sandbox(source: Path) -> bool:
    return any(
        name == SANDBOX or name.startswith(SANDBOX + ".") for name in imports_of(source)
    )


@pytest.mark.parametrize("relative", MUST_NOT_REACH_THE_SANDBOX)
def test_the_selection_and_presentation_path_cannot_execute_code(relative):
    source = MODULE / relative
    assert source.is_file(), f"{relative} has moved; update this list deliberately"
    assert not reaches_sandbox(source), (
        f"{relative} imports {SANDBOX}, which executes untrusted candidate code. "
        "This was a packaging boundary when the grader was its own image and is a review "
        "boundary now — widen MAY_REACH_THE_SANDBOX if that is really intended."
    )


def test_the_files_that_may_reach_it_still_exist():
    """A guard that names a file which has been renamed is a guard that stopped guarding."""
    for relative in MAY_REACH_THE_SANDBOX:
        assert (MODULE / relative).is_file(), relative


def test_no_other_module_file_reaches_the_sandbox():
    """The complement of the list above, computed rather than enumerated.

    The parametrised test catches a named file acquiring the import; this catches a NEW file
    doing it, which is the case a hand-maintained list always misses.
    """
    allowed = set(MAY_REACH_THE_SANDBOX)
    offenders = []
    for source in sorted(MODULE.rglob("*.py")):
        relative = source.relative_to(MODULE)
        if relative.parts[0] in ("engine", "tests", "evaluation", "scripts"):
            continue
        if "__pycache__" in relative.parts:
            continue
        if str(relative) in allowed:
            continue
        if reaches_sandbox(source):
            offenders.append(str(relative))
    assert not offenders, (
        f"these files reach the sandbox and are not on the allowlist: {offenders}"
    )


def test_the_module_surface_does_not_import_a_web_framework():
    """The whole point of the collapse. A host owns transport.

    An accidental `from fastapi import ...` would not fail anything — it would just make
    the module drag a web framework into every host that installs it, which is exactly what
    a library should not do.
    """
    offenders = []
    for source in sorted(MODULE.rglob("*.py")):
        relative = source.relative_to(MODULE)
        if relative.parts[0] in ("tests", "evaluation", "scripts"):
            continue
        if "__pycache__" in relative.parts:
            continue
        for name in imports_of(source):
            if name.split(".")[0] in ("fastapi", "starlette", "uvicorn"):
                offenders.append(f"{relative} imports {name}")
    assert not offenders, offenders
