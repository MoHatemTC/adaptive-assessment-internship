"""Load four services that all name their package `app` into one test session.

THE OBSTACLE, AND WHY IT IS NOT A DEFECT

Every service ships `app/main.py`. That is correct for deployment — each runs in its own
container with its own interpreter, and `app` is the conventional name — but it means the
four cannot be imported into one process: the second `import app.main` gets the first
service's module out of `sys.modules` and every assertion after it silently describes the
wrong service.

So each service is loaded in isolation, with `app*` evicted from `sys.modules` around the
import. Tests that assert something about ALL four therefore load them one at a time rather
than holding four app objects at once, and the fixture is function-scoped so no test can
leak a half-imported package into the next.

`adaptive_contracts` is a genuinely shared package and stays on the path throughout — that
one IS meant to be importable everywhere, which is the whole point of it being a package
rather than a service.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

SERVICES_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = SERVICES_ROOT / "contracts"

#: Directory name -> the `service_name` it must report. Kept explicit rather than derived,
#: so a service that renames itself without updating its config fails here instead of
#: reporting a name no dashboard expects.
SERVICES: dict[str, str] = {
    "assessment-orchestrator": "assessment-orchestrator",
    "bank-registry": "bank-registry",
    "competency-graph": "competency-graph",
    "grader": "grader",
}

#: Ports as declared in each service's `Settings`. Asserted against deploy/docker-compose.yml
#: by `test_deployment.py` — a service and its compose entry disagreeing about a port is the
#: kind of thing that only shows up as a failed health check in an environment nobody is
#: watching.
EXPECTED_PORTS: dict[str, int] = {
    "assessment-orchestrator": 8080,
    "bank-registry": 8081,
    "grader": 8082,
    "competency-graph": 8083,
}

if str(CONTRACTS) not in sys.path:
    sys.path.insert(0, str(CONTRACTS))


def _evict_app_modules() -> None:
    for name in [n for n in sys.modules if n == "app" or n.startswith("app.")]:
        del sys.modules[name]


@contextmanager
def load_service(directory: str):
    """Yield a service's FastAPI app, with its own `app` package in force.

    The eviction happens on the way IN as well as on the way out: a previous test that
    failed mid-import would otherwise leave a partial `app` behind, and the failure would
    appear in whichever test happened to run next.
    """
    service_dir = SERVICES_ROOT / directory
    if not service_dir.is_dir():
        raise AssertionError(f"no such service directory: {service_dir}")

    _evict_app_modules()
    sys.path.insert(0, str(service_dir))
    try:
        main = importlib.import_module("app.main")
        yield main
    finally:
        try:
            sys.path.remove(str(service_dir))
        except ValueError:
            pass
        _evict_app_modules()


@pytest.fixture(params=sorted(SERVICES), ids=sorted(SERVICES))
def service(request):
    """Every test using this runs once per service. Four services, one assertion each."""
    with load_service(request.param) as main:
        yield request.param, main


@pytest.fixture()
def client_factory():
    """A TestClient for one named service, torn down before the next import."""
    from fastapi.testclient import TestClient

    @contextmanager
    def make(directory: str):
        with load_service(directory) as main:
            with TestClient(main.app) as client:
                yield client

    return make
