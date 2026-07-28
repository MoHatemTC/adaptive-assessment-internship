"""Compare the three CAT experiment branches.

This script is intentionally read-only: it uses `git show` to inspect branch
contents and summarizes who owns math and next-question selection per branch.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

BRANCHES = [
    "approach-1-code-math-llm-pick",
    "approach-2-llm-math-code-pick",
    "approach-3-llm-full-cat",
]


@dataclass
class ApproachSummary:
    branch: str
    commit: str
    approach_id: str
    math_actor: str
    selection_actor: str
    controller_files: list[str]
    status: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _git(args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=_repo_root(),
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def _show(branch: str, path: str) -> str:
    return _git(["show", f"{branch}:{path}"])


def _extract_default(source: str, name: str) -> str:
    pattern = rf'{name}\s*=\s*os\.getenv\("[^"]+",\s*"([^"]+)"\)'
    match = re.search(pattern, source)
    return match.group(1) if match else "unknown"


def _exists(branch: str, path: str) -> bool:
    try:
        _show(branch, path)
        return True
    except subprocess.CalledProcessError:
        return False


def summarize_branch(branch: str) -> ApproachSummary:
    commit = _git(["rev-parse", "--short", branch])
    tracing = _show(branch, "masaar-mcq-cat-test/tracing.py")
    controller_files = [
        path
        for path in [
            "masaar-mcq-cat-test/selection_pipeline.py",
            "masaar-mcq-cat-test/llm_math.py",
            "masaar-mcq-cat-test/llm_full_cat.py",
        ]
        if _exists(branch, path)
    ]
    return ApproachSummary(
        branch=branch,
        commit=commit,
        approach_id=_extract_default(tracing, "APPROACH_ID"),
        math_actor=_extract_default(tracing, "MATH_ACTOR"),
        selection_actor=_extract_default(tracing, "SELECTION_ACTOR"),
        controller_files=controller_files,
        status="ok",
    )


def print_markdown(rows: list[ApproachSummary]) -> None:
    print("| Branch | Commit | Math | Next question | Controller files |")
    print("|---|---:|---|---|---|")
    for row in rows:
        files = ", ".join(Path(path).name for path in row.controller_files)
        print(
            f"| `{row.branch}` | `{row.commit}` | `{row.math_actor}` | "
            f"`{row.selection_actor}` | {files} |"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Print JSON instead of Markdown.")
    args = parser.parse_args()

    rows = [summarize_branch(branch) for branch in BRANCHES]
    if args.json:
        print(json.dumps([asdict(row) for row in rows], indent=2))
    else:
        print_markdown(rows)


if __name__ == "__main__":
    main()
