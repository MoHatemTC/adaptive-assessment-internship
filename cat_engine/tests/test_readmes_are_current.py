"""Every code directory documents itself, and the documentation matches the filesystem.

WHY THIS IS A TEST AND NOT A CONVENTION

A README that lists files is only useful while it is true. The moment one drifts it is worse
than nothing: a reader trusts it, looks for a file that was renamed six months ago, and
concludes the directory is broken rather than the document.

So two claims are checked, and the second is the one that decays:

  1. Every directory holding Python has a README.md.
  2. Every Python file in it is NAMED in that README.

WHAT IS DELIBERATELY NOT CHECKED

That the description is accurate — no test can check that. What a test can check is that a
file was not silently added or renamed without anybody touching the document, which is how
these rot in practice.

`__init__.py` is exempt where it is empty: a marker file has no responsibility to describe.
"""

from __future__ import annotations

from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[1]

#: Directories that hold Python but document themselves in their PARENT's README, because
#: they are a handful of files whose grouping is the interesting part rather than each file.
DOCUMENTED_BY_PARENT = {
    Path("stores/sql"),
}

#: Output directories. They carry a README describing what they hold, but the contents are
#: artefacts rather than code and enumerating them would be a losing race.
ARTEFACT_DIRS = {
    Path("evaluation/runs"),
    Path("evaluation/reports"),
    Path("evaluation/bank"),
    Path("evaluation/code_review"),
    Path("engine/data"),
    Path("engine/data/rubrics"),
    Path("live/static"),
}

SKIP_PARTS = {"__pycache__", "eval-results"}


def code_directories() -> list[Path]:
    """Every directory under the module that holds at least one non-trivial Python file."""
    found = []
    for path in sorted(MODULE.rglob("*.py")):
        rel = path.relative_to(MODULE)
        if set(rel.parts) & SKIP_PARTS:
            continue
        directory = rel.parent
        if directory in DOCUMENTED_BY_PARENT or directory in ARTEFACT_DIRS:
            continue
        if any(part.startswith("runs") for part in directory.parts):
            continue
        if directory not in found:
            found.append(directory)
    return found


def documented_files(readme: Path) -> str:
    return readme.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "directory", code_directories(), ids=lambda d: str(d) or "cat_engine"
)
def test_the_directory_has_a_readme(directory):
    readme = MODULE / directory / "README.md"
    assert readme.is_file(), (
        f"cat_engine/{directory} holds Python and does not document itself. "
        "Add a README.md naming each file and what it is responsible for."
    )


@pytest.mark.parametrize(
    "directory", code_directories(), ids=lambda d: str(d) or "cat_engine"
)
def test_every_python_file_is_named_in_the_readme(directory):
    readme = MODULE / directory / "README.md"
    if not readme.is_file():
        pytest.skip("covered by test_the_directory_has_a_readme")
    text = documented_files(readme)

    missing = []
    for source in sorted((MODULE / directory).glob("*.py")):
        if source.name == "__init__.py" and source.stat().st_size < 200:
            continue  # a bare package marker has nothing to describe
        if source.name not in text:
            missing.append(source.name)

    assert not missing, (
        f"cat_engine/{directory}/README.md does not mention {missing}. "
        "A README that has stopped listing what is there is worse than none — a reader "
        "trusts it and concludes the directory is broken rather than the document."
    )


@pytest.mark.parametrize(
    "directory", code_directories(), ids=lambda d: str(d) or "cat_engine"
)
def test_the_readme_names_no_file_that_is_gone(directory):
    """The other direction, and the one that actually rots.

    Files get deleted far more often than they get added, and a README naming a module that
    no longer exists sends a reader looking for something that was removed on purpose.
    Checked only for names that look like this directory's own Python files, so prose about
    `orchestrator.py` in a neighbouring README is not a failure.
    """
    readme = MODULE / directory / "README.md"
    if not readme.is_file():
        pytest.skip("covered by test_the_directory_has_a_readme")

    import re

    here = {p.name for p in (MODULE / directory).glob("*.py")}
    # TABLE ROWS ONLY. The table is the index; prose around it may legitimately name a file
    # that was deleted, precisely in order to say that it was.
    rows = [
        line for line in documented_files(readme).splitlines() if line.startswith("|")
    ]
    mentioned = set(re.findall(r"`([\w.]+\.py)`", "\n".join(rows)))
    ghosts = sorted(
        name
        for name in mentioned
        if name not in here and (MODULE / directory / name).parent.is_dir()
        and not (MODULE / directory / name).exists()
        # a README may legitimately name a file elsewhere in the tree
        and not list(MODULE.rglob(name))
    )
    assert not ghosts, (
        f"cat_engine/{directory}/README.md names files that do not exist anywhere: {ghosts}"
    )


def test_the_module_root_readme_links_every_subdirectory():
    """`cat_engine/README.md` is the index. A subdirectory missing from it is undiscoverable."""
    root = (MODULE / "README.md").read_text(encoding="utf-8")
    top_level = {
        d.parts[0]
        for d in code_directories()
        if len(d.parts) >= 1 and str(d) != "."
    }
    missing = sorted(name for name in top_level if f"{name}/" not in root)
    assert not missing, f"cat_engine/README.md does not link: {missing}"
