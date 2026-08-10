"""The API a frontend is built against, and whether it is documented well enough to build on.

WHO THIS IS FOR

Someone who is not in this repository. They cannot read `main.py` to find out what a 409
means or whether `presenting` can be null, so anything they need has to be IN the spec. A
route that is technically documented and practically unusable is the failure this suite
exists to catch.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SERVICES, load_service

ROOT = Path(__file__).resolve().parents[2]
API_DOCS = ROOT / "docs" / "api"
SERVICE_IDS = sorted(SERVICES)


@pytest.fixture(scope="module")
def committed() -> dict[str, dict]:
    specs = {}
    for directory in SERVICE_IDS:
        path = API_DOCS / f"openapi-{directory}.json"
        assert path.is_file(), (
            f"no committed spec for {directory}; run scripts/dump_openapi.py"
        )
        specs[directory] = json.loads(path.read_text(encoding="utf-8"))
    return specs


class TestTheCommittedSpecsMatchTheCode:
    def test_nothing_has_drifted(self):
        """A spec that only exists on a running service cannot be reviewed in a pull
        request, diffed when it changes, or generated from before somebody deploys."""
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, str(ROOT / "scripts" / "dump_openapi.py"), "--check"],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestEveryRouteIsDocumented:
    """A summary is what a reader sees in a route list. Without one they see a function
    name, and `record_response` does not say that a 409 means the item changed."""

    def test_every_route_has_a_summary(self, service):
        directory, main = service
        undocumented = []
        for path, operations in main.app.openapi()["paths"].items():
            for method, operation in operations.items():
                if not operation.get("summary"):
                    undocumented.append(f"{method.upper()} {path}")
        assert not undocumented, f"{directory}: {undocumented}"

    def test_every_route_is_tagged(self, service):
        """Tags are what group a spec into something navigable. An untagged route lands in
        a default bucket a reader has no reason to open."""
        directory, main = service
        untagged = [
            f"{method.upper()} {path}"
            for path, operations in main.app.openapi()["paths"].items()
            for method, operation in operations.items()
            if not operation.get("tags")
        ]
        assert not untagged, f"{directory}: {untagged}"

    def test_the_service_itself_says_what_it_is_for(self, service):
        directory, main = service
        spec = main.app.openapi()
        assert spec["info"].get("summary"), f"{directory} has no summary"
        assert len(spec["info"].get("description", "")) > 200, (
            f"{directory}'s description is too thin to orient anybody"
        )


class TestTheAssessmentApiIsUsableWithoutReadingTheSource:
    """The one an external frontend actually builds against."""

    @pytest.fixture(scope="class")
    def spec(self) -> dict:
        with load_service("assessment-orchestrator") as main:
            return main.app.openapi()

    def test_every_lifecycle_route_is_present(self, spec):
        paths = set(spec["paths"])
        assert {
            "/assessments",
            "/assessments/{assessment_id}",
            "/assessments/{assessment_id}/next",
            "/assessments/{assessment_id}/responses",
            "/assessments/{assessment_id}/report",
            "/banks",
        } <= paths

    def test_the_error_shape_is_declared_for_the_status_codes_it_returns(self, spec):
        """A client needs to know that a 409 has a `code` it can branch on, not just that
        a 409 happens."""
        responses = spec["paths"]["/assessments/{assessment_id}/responses"]["post"][
            "responses"
        ]
        for status in ("404", "409", "422", "503"):
            assert status in responses, f"{status} is not documented"
            content = responses[status].get("content", {})
            schema = content.get("application/json", {}).get("schema", {})
            assert "ErrorResponse" in json.dumps(schema), (
                f"{status} does not declare the error shape"
            )

    def test_the_presented_item_schema_carries_no_answer_field(self, spec):
        """The candidate boundary, asserted on the published contract. If the SPEC ever
        describes an answer field, either the boundary moved or the documentation is
        lying — and both are worth failing over."""
        item = spec["components"]["schemas"]["PresentedItemDTO"]["properties"]
        for forbidden in ("answer_index", "answer", "reference_solution", "tests"):
            assert forbidden not in item

    def test_the_gated_routes_say_they_are_gated(self, spec):
        for path in (
            "/assessments/{assessment_id}/diagnostics",
            "/assessments/{assessment_id}/state",
        ):
            described = json.dumps(spec["paths"][path]["get"])
            assert "AUTHOR_DIAGNOSTICS_ENABLED" in described, (
                f"{path} does not say what turns it on"
            )
            assert "candidate-safe" in described.lower()

    def test_the_report_route_warns_against_reading_a_point_band(self, spec):
        """Within-one-level accuracy runs about 99% and exact-band about 65%. A frontend
        that renders a single level without that context is presenting a confidence the
        measurement does not support, and this is the only place it would learn."""
        described = spec["paths"]["/assessments/{assessment_id}/report"]["get"]
        assert "BAND RANGE" in json.dumps(described)


class TestTheBankApiDocumentsItsTwoReadPaths:
    @pytest.fixture(scope="class")
    def spec(self) -> dict:
        with load_service("bank-registry") as main:
            return main.app.openapi()

    def test_the_ranking_endpoint_says_it_serves_no_payloads(self, spec):
        described = json.dumps(spec["paths"]["/banks/{bank_id}/items"]["get"])
        assert "Never payloads" in described or "never payloads" in described

    def test_the_admin_routes_are_tagged_apart_from_the_read_routes(self, spec):
        """So a reviewer can see the write surface without reading the whole document."""
        admin = {
            path
            for path, operations in spec["paths"].items()
            for operation in operations.values()
            if "admin" in operation.get("tags", [])
        }
        assert "/banks/validate" in admin
        assert "/banks/{bank_id}/items" not in admin
