#!/usr/bin/env python3
"""Write each service's OpenAPI document to `docs/api/`.

WHY THE SPECS ARE COMMITTED

A frontend is built against this API by people who are not in this repository. A spec that
only exists on a running service cannot be reviewed in a pull request, cannot be diffed
when it changes, and cannot be generated from until someone deploys. Committing it makes
an API change visible in the same place every other change is.

`services/tests/test_api_documentation.py` fails when a committed spec no longer matches
the code, so the two cannot drift silently.

    python scripts/dump_openapi.py            # rewrite them
    python scripts/dump_openapi.py --check    # fail if any is stale
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "api"

SERVICES = (
    "assessment-orchestrator",
    "bank-registry",
    "grader",
    "competency-graph",
    "live-voice",
)


def spec_for(directory: str) -> dict:
    """One service's OpenAPI document, with its own `service` package in force.

    The same isolation the test suite uses: five services each ship `service/main.py`, so
    the module has to be evicted between imports or the second one describes the first.
    """
    for name in [n for n in sys.modules if n == "service" or n.startswith("service.")]:
        del sys.modules[name]
    service_dir = str(ROOT / "services" / directory)
    sys.path.insert(0, service_dir)
    try:
        return importlib.import_module("service.main").app.openapi()
    finally:
        sys.path.remove(service_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail if a committed spec differs from the code, and write nothing",
    )
    arguments = parser.parse_args()

    for extra in ("contracts", "common", "clients"):
        sys.path.insert(0, str(ROOT / "services" / extra))
    sys.path.insert(0, str(ROOT / "backend"))

    OUT.mkdir(parents=True, exist_ok=True)
    stale: list[str] = []
    for directory in SERVICES:
        target = OUT / f"openapi-{directory}.json"
        rendered = json.dumps(spec_for(directory), indent=2, sort_keys=True) + "\n"
        if arguments.check:
            current = target.read_text(encoding="utf-8") if target.is_file() else ""
            if current != rendered:
                stale.append(directory)
        else:
            target.write_text(rendered, encoding="utf-8")
            print(f"wrote {target.relative_to(ROOT)}")

    if stale:
        print(
            "these committed specs no longer match the code: "
            f"{stale}\nrun: python scripts/dump_openapi.py",
            file=sys.stderr,
        )
        return 1
    if arguments.check:
        print("every committed spec matches the code")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
