"""Configuration for assessment-orchestrator.

IT NEEDS MODEL EGRESS, AND ADR-0001 SAID IT WOULD NOT

`docs/microservices.md` recorded the grader as "the only component needing egress", and
that is wrong: the Picking Agent calls a model on every queue fill, to choose one item from
a shortlist the engine has already ranked. A deployment that wrote its network policy from
that sentence would find selection silently falling back to the deterministic choice on
every question — which is a legitimate degraded mode, and therefore one that produces no
error and no alert. Corrected here, in ADR-0002, and in the compose file.
"""

from __future__ import annotations

from adaptive_service import ServiceSettings


class Settings(ServiceSettings):
    service_name: str = "assessment-orchestrator"
    port: int = 8080

    bank_registry_url: str = "http://bank-registry:8081"
    grader_url: str = "http://grader:8082"
    #: Empty runs propagation IN THIS PROCESS instead of calling the graph service. That is
    #: a supported single-container deployment, not a fallback: `/health` reports which one
    #: is in force, so a misconfiguration is visible rather than merely quiet.
    competency_graph_url: str = "http://competency-graph:8083"
    #: Empty disables competency-scoped assessments: `POST /assessments` with a `scope`
    #: then refuses rather than quietly assessing the whole bank, because "you asked for
    #: three competencies and got eleven" is not a degraded mode a candidate or a report
    #: could detect afterwards.
    competency_scope_url: str = "http://competency-scope:8085"
    #: A scope is induced once per session, not per response, so it is nowhere near the
    #: 150 ms per-response budget. The ceiling is here to bound session START.
    scope_timeout_seconds: float = 10.0

    #: A code submission is sandbox-bound and budgeted at 30 s; the ceiling sits above the
    #: sandbox's own timeout rather than racing it.
    grader_timeout_seconds: float = 120.0
    bank_timeout_seconds: float = 10.0
    #: Propagation is in-memory work behind one hop. If it takes longer than this something
    #: is wrong, and the correct answer is to continue without the graph rather than to
    #: keep a candidate waiting.
    graph_timeout_seconds: float = 5.0

    #: The instrumented author view: posteriors, per-item information, the criterion phase,
    #: raw session state. NOT candidate-safe — it exposes what a candidate must not see
    #: while answering. Off by default, and refused by the service rather than merely not
    #: rendered by a client.
    author_diagnostics_enabled: bool = False

    #: WHERE LIVE ASSESSMENTS LIVE. Empty keeps them in this process — a supported
    #: single-replica deployment, not a fallback, and `/health` reports which is in force.
    #:
    #: Set it and a restart resumes instead of losing every candidate mid-answer, and a
    #: second replica can serve a session the first one started. A SEPARATE setting from
    #: `BANK_DATABASE_URL` even when both point at one server: a bank is content that is
    #: published and kept, a session is a record of what a person answered and has a
    #: deletion deadline, and one URL for both makes that deadline somebody's afterthought.
    session_database_url: str = ""
    #: False blanks the per-response detail as soon as an assessment ends, keeping the
    #: report. The report is what anybody reads afterwards; the responses are what make the
    #: row personal data.
    session_keep_responses: bool = False

    #: A frontend runs on its own origin. Wildcard with credentials off is the monolith's
    #: posture carried forward — there is no cookie-authenticated API here, and wildcard
    #: plus credentials is invalid browser policy anyway. Narrow it in any deployment that
    #: puts something behind an origin check.
    cors_allow_origins: str = "*"

    def allowed_origins(self) -> list[str]:
        raw = (self.cors_allow_origins or "").strip()
        return ["*"] if raw in ("", "*") else [o.strip() for o in raw.split(",") if o.strip()]


settings = Settings()
