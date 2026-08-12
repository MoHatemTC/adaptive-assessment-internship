"""What every service must be true of, whatever it does.

The behaviour of each service is tested beside it. What is tested HERE is the set of
properties that only exist ACROSS services, and that no single service's own suite could
notice:

    every service reports the same contract version, and the same engine configuration
    `/config` prints no credential
    the operator endpoints are never shadowed
    declared ports match the compose file
    no service imports another, and each imports only its declared slice of the engine

Most of those have been a production incident somewhere. The last one is the service
boundary itself, written down and enforced — `bank-registry` reaching into the grader would
put sandbox execution behind a read-only bank API, and nothing else would catch it.

The stub-honesty tests that used to live here are gone: every service is implemented, and a
test asserting that an unimplemented one returns an honest 501 has nothing left to assert.
Each `MIGRATION.md` records what moved.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from adaptive_contracts import SCHEMA_VERSION

from conftest import EXPECTED_PORTS, SERVICES

SERVICE_IDS = sorted(SERVICES)


def _imports(source: Path) -> set[str]:
    """Every module this file imports, resolved to a dotted path.

    A `from X import a, b` yields `X.a` and `X.b` rather than `X`, because the slice
    allowlist below draws its boundaries at the submodule — `from app.services.orchestrator
    import registry` and `... import grader` are the two facts it exists to tell apart, and
    recording both as `app.services.orchestrator` would collapse them.

    AST rather than a substring search, because `import app.services.code_adaptive` and
    `from app.services.code_adaptive import x` are the same fact spelled two ways, and
    neither should be found in a docstring that merely mentions it.
    """
    import ast

    found: set[str] = set()
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found |= {f"{node.module}.{alias.name}" for alias in node.names}
    return found


class TestHealth:
    def test_it_answers_and_identifies_itself(self, client_factory):
        for directory, expected_name in sorted(SERVICES.items()):
            with client_factory(directory) as client:
                response = client.get("/health")
            # Status first. When the catch-all shadows this route the body has no `status`
            # key, and asserting on the body would report `KeyError: 'status'` — which
            # tells the reader nothing about what actually broke.
            assert response.status_code == 200, (
                f"{directory} /health returned {response.status_code}; if this is 501 the "
                "catch-all route has been registered ahead of it"
            )
            body = response.json()
            assert body["status"] == "ok"
            assert body["service"] == expected_name, (
                f"{directory} reports {body['service']!r}; a dashboard keyed on the "
                "directory name would never find it"
            )

    def test_every_service_reports_the_same_contract_version(self, client_factory):
        """This is the entire reason the field is on /health.

        A mismatched pair is meant to be visible in a dashboard rather than as a decoding
        error three hops away — which only works if they are checked against each other.
        """
        seen = {}
        for directory in SERVICE_IDS:
            with client_factory(directory) as client:
                seen[directory] = client.get("/health").json()["contract_schema_version"]
        assert set(seen.values()) == {SCHEMA_VERSION}, seen

    def test_release_is_reported_so_a_deploy_is_identifiable(self, client_factory):
        with client_factory("bank-registry") as client:
            assert client.get("/health").json()["release"]


class TestConfigEndpoint:
    def test_it_returns_the_effective_configuration(self, client_factory):
        with client_factory("grader") as client:
            body = client.get("/config").json()
        assert body["service_name"] == "grader"
        assert body["port"] == EXPECTED_PORTS["grader"]
        assert "log_level" in body

    def test_no_currently_declared_setting_is_a_credential(self, client_factory):
        """Holds today because no service declares one. Asserted so that adding one is a
        deliberate act with a failing test attached, not an oversight."""
        secretish = ("key", "secret", "token", "password", "credential")
        for directory in SERVICE_IDS:
            with client_factory(directory) as client:
                body = client.get("/config").json()
            leaked = [k for k in body if any(s in k.lower() for s in secretish)]
            assert not leaked, f"{directory} exposes {leaked} on /config"

    def test_the_redaction_filter_catches_the_obvious_names(self, client_factory):
        """The filter is a substring test on the FIELD NAME. Pinned so the next person to
        touch it knows what it does and does not do."""
        redacted = ("litellm_api_key", "jwt_secret", "refresh_token", "db_password")
        for name in redacted:
            assert any(s in name for s in ("key", "secret", "token", "password"))

    @pytest.mark.parametrize(
        "name", ["database_url", "dsn", "connection_string", "sentry_dsn"]
    )
    def test_known_gap_credentials_embedded_in_a_url_are_not_redacted(self, name):
        """DOCUMENTED GAP, not an assertion that this is correct.

        `postgres://user:pw@host/db` in a field called `database_url` passes the filter and
        would be printed by /config. No service declares such a field today, which is why
        this is a gap and not a defect — but the filter tests the name, not the value, so
        it will not save anyone who adds one.

        Left as a named test rather than a comment so it appears in the run output and in
        review, and so closing it has an obvious home.
        """
        assert not any(s in name for s in ("key", "secret", "token", "password"))


class TestEveryServiceKeepsItsMigrationChecklist:
    def test_the_checklist_exists(self):
        """Ticked or not. A 501 pointing at a missing file is worse than one pointing at
        nothing, and a migrated service's checklist is the record of what moved."""
        root = Path(__file__).resolve().parents[1]
        for directory in SERVICE_IDS:
            assert (root / directory / "MIGRATION.md").is_file()


class TestTheOperatorEndpointsAreNeverShadowed:
    """The one way a service can be actively wrong before it does anything.

    A catch-all `/{path:path}` matches everything including `/health`, and FastAPI resolves
    in registration order — so the operator endpoints survive only because they are
    declared first. That is a property of line ordering in a file: exactly the kind of
    thing a later edit breaks silently, and the symptom is a liveness probe returning 501
    during an incident.

    It still matters for an implemented service. `adaptive_service.operator_router` is
    included before any other route in every `main.py`, and this is what says so.
    """

    @pytest.mark.parametrize("route", ["/health", "/config"])
    def test_the_operator_endpoints_win(self, client_factory, route):
        for directory in SERVICE_IDS:
            with client_factory(directory) as client:
                response = client.get(route)
            assert response.status_code == 200, (
                f"{directory} returns {response.status_code} for {route} — something has "
                "been registered ahead of the operator router"
            )

    @staticmethod
    def _declared_paths(app) -> list[str]:
        """Route paths in registration order, descending into included routers.

        This FastAPI version keeps an `include_router` call as a single `_IncludedRouter`
        entry that defers to `original_router` rather than flattening its routes into the
        parent — so reading `app.router.routes` directly reports that a service serving
        `/health` does not. FastAPI's own `/openapi.json`, `/docs` and `/redoc` are
        registered before anything in the file and are dropped, so index 0 means "the
        first route this service declares".
        """

        def walk(routes) -> list[str]:
            found: list[str] = []
            for route in routes:
                included = getattr(route, "original_router", None)
                nested = getattr(included, "routes", None) or getattr(
                    route, "routes", None
                )
                if nested:
                    found.extend(walk(nested))
                    continue
                path = getattr(route, "path", "")
                if path and not path.startswith(("/openapi", "/docs", "/redoc")):
                    found.append(path)
            return found

        return walk(app.router.routes)

    def test_they_are_declared_first(self, service):
        """Asserted on the route table, not only on behaviour, so the diagnosis is in the
        failure message rather than left to whoever reads the 501."""
        directory, main = service
        declared = self._declared_paths(main.app)
        for route in ("/health", "/config"):
            assert route in declared, f"{directory} does not serve {route}"
            assert declared.index(route) < 2, (
                f"{directory}: {route} is not among the first routes declared, so a "
                f"later catch-all could shadow it — order is {declared[:4]}"
            )


class TestDeploymentDescriptorAgreesWithTheCode:
    """A service and its compose entry disagreeing about a port surfaces as a failed
    health check in an environment nobody is watching."""

    def test_each_service_declares_the_port_the_suite_expects(self, service):
        directory, main = service
        assert main.settings.port == EXPECTED_PORTS[directory]

    def test_ports_are_unique(self):
        assert len(set(EXPECTED_PORTS.values())) == len(EXPECTED_PORTS)

    def test_compose_publishes_exactly_those_ports(self):
        compose = (
            Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        for directory, port in EXPECTED_PORTS.items():
            assert f"{port}:{port}" in compose, f"{directory} port {port} not published"

    def test_every_service_is_in_the_compose_file(self):
        compose = (
            Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"
        ).read_text(encoding="utf-8")
        for directory in SERVICE_IDS:
            assert directory in compose

    def test_every_service_ships_a_dockerfile_and_requirements(self):
        root = Path(__file__).resolve().parents[1]
        for directory in SERVICE_IDS:
            assert (root / directory / "Dockerfile").is_file()
            assert (root / directory / "requirements.txt").is_file()


class TestTheServicesDoNotDependOnEachOther:
    """The decomposition is only real if the seams hold.

    Each service has its own `Settings` precisely so that one cannot read another's
    configuration — a service that can will eventually depend on it. Services talk to each
    other over HTTP or not at all; they never import each other.
    """

    def test_no_service_imports_another_service(self):
        """Read as IMPORTS, not as text.

        The substring version of this test failed the moment a service's docstring
        explained why the grader needs hidden test cases. Prose about another service is
        how a boundary gets documented; an import is how it gets crossed.
        """
        root = Path(__file__).resolve().parents[1]
        forbidden = {other.replace("-", "_") for other in SERVICE_IDS}
        for directory in SERVICE_IDS:
            own = directory.replace("-", "_")
            for source in (root / directory / "service").rglob("*.py"):
                for imported in _imports(source):
                    top = imported.split(".")[0]
                    assert top not in (forbidden - {own}), (
                        f"{directory}/{source.name} imports {imported}"
                    )

    def test_each_service_declares_its_own_settings(self, service):
        directory, main = service
        assert main.settings.service_name == SERVICES[directory]


class TestEachServiceImportsOnlyTheEngineSliceItOwns:
    """The assertion that replaced "no service imports the monolith".

    That one stopped meaning anything once the engine became a shared library every
    service installs — and it was always the weaker claim. What actually matters is not
    WHETHER a service imports the engine but WHICH PART: `bank-registry` reaching into the
    grader would put sandbox execution behind a read-only bank API, and `competency-graph`
    reaching into `orchestrator.variables` would put it one import from a posterior.

    So the allowlist below is the service boundary, written down and enforced. Widening it
    is a deliberate act with a failing test attached — which is exactly what a boundary
    that reviewers keep having to notice is not.
    """

    #: service directory -> engine module prefixes it is allowed to import.
    ALLOWED: dict[str, tuple[str, ...]] = {
        "bank-registry": (
            "cat_engine.engine.config",
            "cat_engine.engine.schemas",
            "cat_engine.engine.services.orchestrator.bank",
            "cat_engine.engine.services.orchestrator.bank_store",
            "cat_engine.engine.services.orchestrator.registry",
            "cat_engine.engine.services.orchestrator.calibration",
            "cat_engine.engine.services.orchestrator.competency",
            "cat_engine.engine.services.competency_graph",
        ),
        "bank-ingest": (
            "cat_engine.engine.config",
            # The store and its validation, which is the WHOLE point: a bank arriving as
            # an upload clears exactly the bar the checked-in banks clear, because it is
            # checked by the same function. A second implementation of those rules would
            # mean the engine's own suite was testing the seeds rather than the system.
            "cat_engine.engine.services.orchestrator.bank_store",
            "cat_engine.engine.services.orchestrator.registry",
        ),
        "grader": (
            "cat_engine.engine.config",
            "cat_engine.engine.schemas",
            "cat_engine.engine.services.orchestrator.grader",
            "cat_engine.engine.services.orchestrator.outcome",
            "cat_engine.engine.services.code_adaptive",
            "cat_engine.engine.services.voice",
            "cat_engine.engine.services.observability",
            # Transcription only — NOT `voice_live` at large, which holds the realtime
            # rooms and belongs to live-voice. Turning recorded audio into text is part of
            # turning a spoken answer into a graded outcome, and putting it anywhere else
            # would cost two hops for one answer. The prefix is the module, not the
            # package, so importing a realtime room from here still fails this test.
            "cat_engine.engine.services.voice_live.transcribe",
        ),
        "competency-graph": (
            "cat_engine.engine.config",
            "cat_engine.engine.schemas",
            "cat_engine.engine.services.competency_graph",
            "cat_engine.engine.services.orchestrator.graph_delta",
            "cat_engine.engine.services.orchestrator.competency",
            # The transaction itself. It lives beside the orchestrator because that is the
            # only caller, but the CODE is graph code — it opens the ledger, walks the
            # edges and writes node state, and none of that is the orchestrator's to do.
            # Named as a module so the rest of `orchestrator` stays out of reach: this
            # service must not be one import from a posterior.
            "cat_engine.engine.services.orchestrator.propagation_port",
        ),
        "competency-scope": (
            # `app.config` ONLY, for `cat_max_questions` — the budget the reachability
            # check is against. Everything else this service needs about a bank arrives as
            # a DTO, so it touches no schema, no graph module and no orchestrator module.
            # It is the narrowest slice any service declares, and that is the point: a
            # scope decides which questions MAY be asked and must never be one import from
            # a posterior.
            "cat_engine.engine.config",
        ),
        "live-voice": (
            "cat_engine.engine.config",
            "cat_engine.engine.services.observability",
            # The realtime rooms and their transport. NOT `app.services.voice`, which is
            # the rubric grader: this service produces a transcript and never a score, and
            # keeping the two apart is why an audio failure can be reported as an audio
            # failure rather than as a candidate who said nothing.
            "cat_engine.engine.services.voice_live",
        ),
        "assessment-orchestrator": (
            "cat_engine.engine.config",
            "cat_engine.engine.schemas",
            "cat_engine.engine.services.adaptive",
            "cat_engine.engine.services.competency_graph",
            "cat_engine.engine.services.orchestrator",
            "cat_engine.engine.services.observability",
        ),
    }

    #: The services that PERSIST something, and may therefore hold a database driver.
    #:
    #: `bank-registry` and `bank-ingest` read and write banks; `assessment-orchestrator`
    #: writes live sessions. The other four hold nothing durable — the grader grades one
    #: response, `competency-graph` and `competency-scope` are stateless by construction, and
    #: `live-voice` keeps rooms in memory for an hour. A driver in any of them would be a
    #: driver nobody needed, in a service that would eventually find a use for it.
    MAY_IMPORT_THE_STORE = {"bank-registry", "bank-ingest", "assessment-orchestrator"}

    def test_only_the_persisting_services_can_reach_the_store(self):
        """`adaptive_store` carries psycopg, the bank write path and the session table. It is
        installed into three images on purpose, and this is what stops a fourth quietly
        acquiring it — the same argument as the engine-slice allowlist below, for a package
        rather than a module."""
        root = Path(__file__).resolve().parents[1]
        offenders = []
        for directory in sorted(SERVICES):
            for path in (root / directory).rglob("*.py"):
                if "adaptive_store" in path.read_text(encoding="utf-8"):
                    if directory not in self.MAY_IMPORT_THE_STORE:
                        offenders.append(f"{directory}/{path.name}")
        assert not offenders, (
            f"these services reach the bank store and must not: {offenders}"
        )

    def test_every_engine_import_is_inside_the_declared_slice(self):
        root = Path(__file__).resolve().parents[1]
        violations: list[str] = []
        for directory, allowed in self.ALLOWED.items():
            for source in (root / directory / "service").rglob("*.py"):
                for imported in _imports(source):
                    if imported.split(".")[0] != "app":
                        continue
                    if not any(
                        imported == prefix or imported.startswith(prefix + ".")
                        for prefix in allowed
                    ):
                        violations.append(f"{directory}/{source.name} imports {imported}")
        assert not violations, (
            "these imports fall outside the service's declared engine slice; widen "
            f"ALLOWED deliberately if the boundary really has moved: {violations}"
        )

    def test_the_allowlist_covers_every_service(self):
        """A service missing from the table would be unconstrained and look compliant."""
        assert set(self.ALLOWED) == set(SERVICE_IDS)

    def test_no_service_may_import_the_grader_unless_it_is_the_grader(self):
        """Spelled out separately because it is the one that carries a sandbox.

        `code_adaptive` executes untrusted candidate code. Exactly one service is meant to
        be able to, and it is the only one with egress.
        """
        for directory, allowed in self.ALLOWED.items():
            if directory == "grader":
                continue
            assert not any("code_adaptive" in prefix for prefix in allowed), (
                f"{directory} is allowed to import the sandbox"
            )
