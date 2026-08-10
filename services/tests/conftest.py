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
IMPLEMENTED: set[str] = {"bank-registry", "competency-graph", "grader"}

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


@contextmanager
def load_services(*directories: str):
    """Several services at once, each keeping its own `service` package.

    `load_service` evicts `service*` on the way in, so the second import replaces the first
    in `sys.modules` — but the first MODULE OBJECT is already bound here, and its globals
    (including its `app`) stay alive. That is what lets a test hold two services and wire
    one's client at the other's ASGI app.
    """
    from contextlib import ExitStack

    with ExitStack() as stack:
        yield {d: stack.enter_context(load_service(d)) for d in directories}


def asgi_client(app):
    """An `httpx.Client` that speaks to an ASGI app directly, synchronously.

    Every client in `adaptive_clients` accepts a prepared `httpx.Client`, so a test can
    drive a real service-to-service call with no socket open anywhere. Without it these
    tests would need either a running server — slower, flakier, and a different thing from
    what production does — or a hand-written fake, which tests the fake.

    `TestClient` rather than `httpx.ASGITransport`: the latter implements only
    `handle_async_request`, and these clients are synchronous because the engine seams they
    plug into are.
    """
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="http://in-process")


def stub_sandbox_and_model(monkeypatch) -> None:
    """Every code submission compiles and passes; no model is ever called.

    The same line `backend/tests/conftest.py` draws. Neither boundary is exercised for
    real: the sandbox costs money and needs network, and a model is not deterministic. What
    is under test is what the service does with whatever they return.
    """
    from app.services.code_adaptive import session as code_session
    from app.services.code_adaptive.execution import ExecutionEvidence, TestOutcome
    from app.services.code_adaptive.llm_evaluator import LLMEvaluation

    monkeypatch.setattr(
        code_session,
        "run_submission",
        lambda code, tests, function_name: ExecutionEvidence(
            compiled=True,
            execution_completed=True,
            passed_tests=len(tests),
            total_tests=len(tests),
            test_results=[TestOutcome(t["test_id"], True, 1.0) for t in tests],
        ),
    )
    monkeypatch.setattr(
        code_session, "llm_evaluate", lambda *a, **k: LLMEvaluation(available=False)
    )


@pytest.fixture(scope="session")
def bank_items() -> dict[str, tuple[str, int]]:
    """One active item of each modality from the DA bank, with the MCQ's answer index.

    Read from the engine rather than hardcoded: an item id pinned in a test is an item id
    that stops existing the first time a bank is rebuilt, and the failure then looks like a
    grading bug rather than a stale fixture.
    """
    from app.services.orchestrator import registry

    chosen: dict[str, tuple[str, int]] = {}
    for item in registry.get_bank("DA").all_items():
        if item.status != "active":
            continue
        if item.modality == "mcq" and "mcq" not in chosen:
            chosen["mcq"] = (item.item_id, int(item.payload["answer_index"]))
        elif item.modality == "code" and "code" not in chosen:
            chosen["code"] = (item.item_id, 0)
        elif item.modality in ("open", "voice") and "open" not in chosen:
            chosen["open"] = (item.item_id, 0)
    missing = {"mcq", "code", "open"} - set(chosen)
    assert not missing, f"the DA bank no longer offers {missing}"
    return chosen


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
