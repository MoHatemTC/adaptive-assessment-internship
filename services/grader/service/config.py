"""Configuration for grader.

The only service that needs egress — a sandbox for candidate code and a model for rubric
grading and code interpretation. Everything it needs to reach is declared here so that is
readable in one place, and so a network policy can be written from it.
"""

from __future__ import annotations

from adaptive_service import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "grader"
    port: int = 8082

    #: Where items come from. The grader fetches them itself rather than being handed one,
    #: so the answer key, the hidden tests and the reference solution are never in a
    #: message the orchestrator has to be trusted not to forward.
    bank_registry_url: str = "http://bank-registry:8081"

    #: A sandboxed submission legitimately takes tens of seconds — `docs/microservices.md`
    #: budgets 30 s for code against 500 ms for MCQ. This is the ceiling on the whole
    #: grade call, so it sits above the sandbox's own timeout rather than racing it.
    grade_timeout_seconds: float = 120.0
    #: Fetching an item is a small same-cluster read. It has no business waiting as long as
    #: a sandbox does.
    bank_timeout_seconds: float = 10.0


settings = Settings()
