"""The four services, as they actually are: health, config, and an honest 501.

WHAT THIS SUITE IS FOR

No business logic has moved out of the monolith, so there is no behaviour to test. What
there IS to test is the thing a stub can still get wrong and that costs real time when it
does: an operator hitting `/health` and getting 501 because a catch-all was registered
first, four services disagreeing about the contract version they speak, a `/config`
endpoint that prints a credential, or a port that does not match the compose file.

Each of those has been a production incident somewhere. None of them needs the service to
do anything yet.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaptive_contracts import SCHEMA_VERSION

from conftest import EXPECTED_PORTS, SERVICES, load_service

SERVICE_IDS = sorted(SERVICES)


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


class TestTheStubIsHonest:
    """A 501 that says what it is and where the plan lives beats a 404 or a silent 200."""

    def test_an_unimplemented_route_returns_501(self, client_factory):
        with client_factory("competency-graph") as client:
            response = client.get("/nodes/C6.4")
        assert response.status_code == 501

    def test_the_body_names_the_service_and_its_migration_checklist(self, client_factory):
        for directory in SERVICE_IDS:
            with client_factory(directory) as client:
                body = client.get("/anything").json()
            assert body["service"] == SERVICES[directory]
            assert directory in body["see"] and body["see"].endswith("MIGRATION.md")

    @pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
    def test_every_declared_method_is_covered(self, client_factory, method):
        with client_factory("grader") as client:
            assert client.request(method, "/grade").status_code == 501

    def test_the_migration_checklist_it_points_at_exists(self):
        """A 501 pointing at a missing file is worse than one pointing at nothing."""
        root = Path(__file__).resolve().parents[1]
        for directory in SERVICE_IDS:
            assert (root / directory / "MIGRATION.md").is_file()


class TestTheCatchAllDoesNotShadowTheRealRoutes:
    """The one way this boilerplate can be actively wrong.

    `/{path:path}` matches everything including `/health`. FastAPI resolves in registration
    order, so health and config survive only because they are declared first. That is a
    property of line ordering in a file — exactly the kind of thing a later edit breaks
    silently, and the symptom is an operator getting 501 from a liveness probe during an
    incident.
    """

    @pytest.mark.parametrize("route", ["/health", "/config"])
    def test_the_operator_endpoints_win(self, client_factory, route):
        for directory in SERVICE_IDS:
            with client_factory(directory) as client:
                response = client.get(route)
            assert response.status_code == 200, (
                f"{directory} returns {response.status_code} for {route} — the catch-all "
                "has been registered ahead of it"
            )

    def test_they_are_declared_before_the_catch_all(self, service):
        """Asserted on the route table, not only on behaviour, so the diagnosis is in the
        failure message rather than left to whoever reads a 501."""
        directory, main = service
        paths = [getattr(r, "path", "") for r in main.app.router.routes]
        catch_all = next(i for i, p in enumerate(paths) if "{path:path}" in p)
        for route in ("/health", "/config"):
            assert paths.index(route) < catch_all, f"{directory}: {route} is shadowed"

    def test_the_root_path_is_also_answered(self, client_factory):
        """`/` is what a human types first. It must reach the stub, not a bare 404."""
        with client_factory("bank-registry") as client:
            assert client.get("/").status_code == 501


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
        root = Path(__file__).resolve().parents[1]
        others = set(SERVICE_IDS)
        for directory in SERVICE_IDS:
            for source in (root / directory / "service").rglob("*.py"):
                text = source.read_text(encoding="utf-8")
                for other in others - {directory}:
                    module = other.replace("-", "_")
                    assert module not in text, (
                        f"{directory}/{source.name} references {other}"
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
            "app.config",
            "app.schemas",
            "app.services.orchestrator.bank",
            "app.services.orchestrator.registry",
            "app.services.orchestrator.calibration",
            "app.services.orchestrator.competency",
            "app.services.competency_graph",
        ),
        "grader": (
            "app.config",
            "app.schemas",
            "app.services.orchestrator.grader",
            "app.services.orchestrator.outcome",
            "app.services.code_adaptive",
            "app.services.voice",
            "app.services.observability",
        ),
        "competency-graph": (
            "app.config",
            "app.schemas",
            "app.services.competency_graph",
            "app.services.orchestrator.graph_delta",
            "app.services.orchestrator.competency",
        ),
        "assessment-orchestrator": (
            "app.config",
            "app.schemas",
            "app.services.adaptive",
            "app.services.competency_graph",
            "app.services.orchestrator",
            "app.services.observability",
        ),
    }

    @staticmethod
    def _engine_imports(source: Path) -> set[str]:
        """Every `app.…` module this file imports, by static parse.

        AST rather than a substring search: `import app.services.code_adaptive` and
        `from app.services.code_adaptive import x` are the same fact, and neither should be
        missed because it was spelled differently or mentioned in a docstring.
        """
        import ast

        found: set[str] = set()
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found |= {a.name for a in node.names if a.name.split(".")[0] == "app"}
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] == "app":
                    found.add(node.module)
        return found

    def test_every_engine_import_is_inside_the_declared_slice(self):
        root = Path(__file__).resolve().parents[1]
        violations: list[str] = []
        for directory, allowed in self.ALLOWED.items():
            for source in (root / directory / "service").rglob("*.py"):
                for imported in self._engine_imports(source):
                    if not any(
                        imported == prefix or imported.startswith(prefix + ".")
                        for prefix in allowed
                    ):
                        violations.append(
                            f"{directory}/{source.name} imports {imported}"
                        )
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
