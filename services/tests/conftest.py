"""Load every service into one test session.

WHAT USED TO BE HERE, AND WHY IT IS GONE

Every service shipped `app/main.py`. That is the conventional name and it was correct
per-container, but it meant the services could not be imported into one process: the
second `import app.main` got the first service's module out of `sys.modules` and every
assertion after it silently described the wrong service. This file used to evict `app*`
from `sys.modules` around each import to work around it, and ADR-0001 recorded the
awkwardness as an accepted cost.

The cost stopped being acceptable the moment the engine became an installable library,
because the engine's package is also called `app`. A service importing `app.services.…`
inside a session that had just evicted `app*` would have imported half of itself.

So the services renamed THEIR package to `service`. There is one engine and several
adapters over it, and the names now say which is which. Each service directory goes on
`sys.path` in turn; `service.main` is evicted between them because the four modules
genuinely are different modules with the same name — but `app` is left alone, since it is
one shared library and importing it repeatedly is meant to be a no-op.
"""

from __future__ import annotations

import importlib
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

SERVICES_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = SERVICES_ROOT / "contracts"
COMMON = SERVICES_ROOT / "common"
CLIENTS = SERVICES_ROOT / "clients"

#: Directory name -> the `service_name` it must report. Kept explicit rather than derived,
#: so a service that renames itself without updating its config fails here instead of
#: reporting a name no dashboard expects.
SERVICES: dict[str, str] = {
    "assessment-orchestrator": "assessment-orchestrator",
    "bank-registry": "bank-registry",
    "competency-graph": "competency-graph",
    "grader": "grader",
}

#: Services whose routes are real. The rest still answer 501 and point at their
#: `MIGRATION.md`, and the stub-honesty tests run over those.
#:
#: This set grows one entry per migrated service and the stub tests disappear with the last
#: one. Keeping it explicit rather than sniffing for a catch-all means a service that loses
#: its routes by accident fails as "you said this was implemented" rather than passing as
#: "ah, a stub then".
IMPLEMENTED: set[str] = {"bank-registry"}

#: Ports as declared in each service's `Settings`. Asserted against deploy/docker-compose.yml
#: by `test_services.py` — a service and its compose entry disagreeing about a port is the
#: kind of thing that only shows up as a failed health check in an environment nobody is
#: watching.
EXPECTED_PORTS: dict[str, int] = {
    "assessment-orchestrator": 8080,
    "bank-registry": 8081,
    "grader": 8082,
    "competency-graph": 8083,
}

for _extra in (CONTRACTS, COMMON, CLIENTS):
    if _extra.is_dir() and str(_extra) not in sys.path:
        sys.path.insert(0, str(_extra))


@contextmanager
def bank_store_at(directory: Path):
    """Point the process-wide bank store at `directory` for the duration.

    Any test that POSTs a bank needs this. Without it the write lands in the checked-in
    `app/data/banks`, and the next run of the ENGINE suite discovers a sixth registered
    bank that no fixture created and no assertion expects — a failure in a different
    suite, hours later, with nothing pointing back here.
    """
    from app.services.orchestrator import registry
    from app.services.orchestrator.bank_store import BankStore, _seed_profiles

    previous = registry.STORE
    registry.use_store(BankStore(seeds=_seed_profiles(), store_dir=Path(directory)))
    try:
        yield registry.STORE
    finally:
        registry.use_store(previous)


def _evict_service_modules() -> None:
    """Drop `service*` only.

    `app` — the engine — is deliberately left in place. It is one library shared by every
    service, and re-importing it per test would re-read every bank from disk for no gain.
    """
    for name in [n for n in sys.modules if n == "service" or n.startswith("service.")]:
        del sys.modules[name]


@contextmanager
def load_service(directory: str):
    """Yield a service's `main` module, with its own `service` package in force.

    The eviction happens on the way IN as well as on the way out: a previous test that
    failed mid-import would otherwise leave a partial package behind, and the failure
    would appear in whichever test happened to run next.
    """
    service_dir = SERVICES_ROOT / directory
    if not service_dir.is_dir():
        raise AssertionError(f"no such service directory: {service_dir}")

    _evict_service_modules()
    sys.path.insert(0, str(service_dir))
    try:
        main = importlib.import_module("service.main")
        yield main
    finally:
        try:
            sys.path.remove(str(service_dir))
        except ValueError:
            pass
        _evict_service_modules()


@pytest.fixture(params=sorted(SERVICES), ids=sorted(SERVICES))
def service(request):
    """Every test using this runs once per service. One assertion, every service."""
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
