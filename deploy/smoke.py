#!/usr/bin/env python3
"""Drive the running stack end to end, over real HTTP.

    cd deploy && docker compose up -d && python smoke.py

WHAT THIS CATCHES THAT THE TEST SUITES DO NOT

The suites wire services to each other in-process, which is the right way to test
behaviour — it is fast and it cannot be flaky. What it cannot test is everything that is
only true of a container: a missing dependency that raises at import, a volume that is not
writable, a service name that does not resolve, a health check that never goes green.

The first run of this found one: `python-multipart` was undeclared, so the orchestrator
raised at import the moment FastAPI saw a `File` parameter. No test could have — every test
imports the app from a checkout where the dependency happens to be installed.

It answers MCQ items only, and stops when it meets anything else. Code grading needs an E2B
sandbox and rubric grading needs a model gateway; a smoke test that required credentials
would be a smoke test nobody runs.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

ORCHESTRATOR = "http://localhost:8080"
REGISTRY = "http://localhost:8081"
SERVICES = {
    "assessment-orchestrator": 8080,
    "bank-registry": 8081,
    "grader": 8082,
    "competency-graph": 8083,
    "live-voice": 8765,
}


def call(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - fixed localhost scheme
        url, data=data, method=method, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def check(label: str, condition: bool, detail: str = "") -> bool:
    print(f"  {'ok  ' if condition else 'FAIL'}  {label}{f' — {detail}' if detail else ''}")
    return condition


def main() -> int:
    failures = 0

    print("health, and whether the services agree about what they measure")
    fingerprints, contracts = set(), set()
    for name, port in SERVICES.items():
        status, body = call("GET", f"http://localhost:{port}/health")
        if not check(name, status == 200 and body.get("status") == "ok", str(status)):
            failures += 1
            continue
        fingerprints.add(body["engine_config_fingerprint"])
        contracts.add(body["contract_schema_version"])
    failures += not check(
        "one engine configuration across all five", len(fingerprints) == 1, str(fingerprints)
    )
    failures += not check(
        "one contract version across all five", len(contracts) == 1, str(contracts)
    )

    print("\nthe bank catalogue")
    status, banks = call("GET", f"{ORCHESTRATOR}/banks")
    failures += not check("proxied through the orchestrator", status == 200)
    seeded = {b["bank_id"] for b in banks}
    failures += not check("the checked-in banks are registered", "DA" in seeded, str(sorted(seeded)))
    failures += not check("every bank carries a version", all(b.get("version") for b in banks))

    print("\nthe ranking view cannot read a question")
    status, items = call("GET", f"{REGISTRY}/banks/DA/items")
    failures += not check("items are served", status == 200 and len(items) > 0)
    body = json.dumps(items)
    for forbidden in ("answer_index", "stem", "options", "reference_solution"):
        failures += not check(f"no {forbidden} anywhere in the ranking view", forbidden not in body)

    print("\nan assessment, end to end")
    status, session = call(
        "POST", f"{ORCHESTRATOR}/assessments", {"bank_id": "DA", "use_llm": False, "seed": 11}
    )
    if not check("begun", status == 201, json.dumps(session)[:200]):
        return failures + 1
    assessment_id = session["session_id"]
    check("the bank version is pinned", bool(session.get("bank_version")), session.get("bank_version", ""))

    answered = 0
    while not session["stop"] and answered < 40:
        item = session["presenting"]["item"]
        if item["modality"] != "mcq":
            print(f"  ..    stopping at a {item['modality']} item (needs a sandbox or a model)")
            break
        # The answer key is not in any candidate-facing response, which is the point — so
        # the smoke test reads it from the grading view, exactly as the grader does.
        _s, full = call("GET", f"{REGISTRY}/banks/DA/items/{item['item_id']}")
        status, session = call(
            "POST",
            f"{ORCHESTRATOR}/assessments/{assessment_id}/responses",
            {"type": "mcq", "chosen_index": int(full["payload"]["answer_index"])},
        )
        if status != 200:
            failures += not check("answer accepted", False, json.dumps(session)[:200])
            break
        answered += 1
        failures += not check(
            f"answer {answered} acknowledged without a score",
            set(session["last_graded"]) == {"item_id", "modality", "accepted", "flags"},
        )

    check(f"{answered} items administered", answered > 0)
    if session["stop"]:
        report = session["report"]
        failures += not check("a report was produced", bool(report["variables"]))
        failures += not check(
            "every measured competency is provisional",
            {v["decision_status"] for v in report["variables"] if v["observations"]} <= {"provisional"},
        )

    print("\nthe author view is refused by default")
    status, _ = call("GET", f"{ORCHESTRATOR}/assessments/{assessment_id}/diagnostics")
    failures += not check("diagnostics", status == 404, str(status))
    status, _ = call("GET", f"{ORCHESTRATOR}/assessments/{assessment_id}/state")
    failures += not check("raw state", status == 404, str(status))

    print("\nregistering a bank through the admin API")
    submission = {
        "bank_id": "SMOKE",
        "title": "smoke test bank",
        "items": [
            {
                "item_id": f"s{n}",
                "modality": "mcq",
                "measures": [{"variable": "X.1", "weight": 1.0}],
                "cat": {"a": 1.0, "b": 0.0, "c": 0.25},
                "payload": {"stem": "2 + 2?", "options": ["3", "4"], "answer_index": 1},
            }
            for n in range(3)
        ],
        "graph": {
            "nodes": [
                {"competency_id": "X", "title": "X", "node_type": "main"},
                {
                    "competency_id": "X.1",
                    "title": "X.1",
                    "node_type": "sub_competency",
                    "main_competencies": ["X"],
                    "critical": True,
                },
            ],
            "edges": [{"from": "X.1", "to": "X", "relation": "CONTRIBUTES_TO", "weight": 1.0}],
        },
        "coverage_critical_only": False,
    }
    call("DELETE", f"{REGISTRY}/banks/SMOKE")
    status, report = call("POST", f"{REGISTRY}/banks", submission)
    failures += not check("accepted", status == 201, json.dumps(report)[:200])

    status, session = call(
        "POST", f"{ORCHESTRATOR}/assessments", {"bank_id": "SMOKE", "use_llm": False, "seed": 3}
    )
    failures += not check("assessable immediately, with no restart", status == 201)
    if status == 201:
        call("DELETE", f"{ORCHESTRATOR}/assessments/{session['session_id']}")

    status, refused = call("POST", f"{REGISTRY}/banks", submission)
    failures += not check("a second POST is refused", status == 409, refused.get("code", ""))

    broken = {**submission, "bank_id": "SMOKE-BAD"}
    broken["graph"] = {
        "nodes": [{"competency_id": "Y", "title": "Y", "node_type": "main"}],
        "edges": [],
    }
    status, refused = call("POST", f"{REGISTRY}/banks", broken)
    failures += not check(
        "a mismatched graph is refused with the reason",
        status == 422 and "graph_bank_mismatch" in refused.get("detail", ""),
        refused.get("detail", "")[:120],
    )
    call("DELETE", f"{REGISTRY}/banks/SMOKE")

    print(f"\n{'FAILED' if failures else 'PASSED'} — {failures} failing check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
