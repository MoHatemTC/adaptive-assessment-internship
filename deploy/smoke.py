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
INGEST = "http://localhost:8084"
SCOPE = "http://localhost:8085"
SERVICES = {
    "assessment-orchestrator": 8080,
    "bank-registry": 8081,
    "grader": 8082,
    "competency-graph": 8083,
    "bank-ingest": 8084,
    "competency-scope": 8085,
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


def upload_file(url: str, bank: dict) -> tuple[int, dict]:
    """PUT one bank as multipart, without a third-party HTTP client.

    Hand-rolled because `smoke.py` deliberately imports nothing that is not in the standard
    library — a smoke test with dependencies is a smoke test that fails for its own reasons.
    """
    boundary = "----smoke-boundary"
    payload = json.dumps(bank).encode()
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="file"; filename="bank.json"\r\n',
            b"Content-Type: application/json\r\n\r\n",
            payload,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    request = urllib.request.Request(  # noqa: S310 - fixed localhost scheme
        url,
        data=body,
        method="PUT",
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


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
    failures += not check("proxied through the orchestrator", status == 200, str(banks)[:120])
    # A failed check must not abort the run. The first version of this unpacked the body
    # unconditionally and died on the error dict, so one transient 503 during startup hid
    # every check after it — which is the opposite of what a smoke test is for.
    banks = banks if isinstance(banks, list) else []
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

    print("\nthe write path that moved says where it went")
    status, moved = call("POST", f"{REGISTRY}/banks", {"bank_id": "X"})
    failures += not check(
        "bank-registry refuses writes with a 410 naming the replacement",
        status == 410 and "bank-ingest" in moved.get("detail", ""),
        f"{status} {moved.get('code', '')}",
    )

    # --- uploading a bank, which is the path an author actually uses ---------
    #
    # Only a container can catch what this catches. `python-multipart` is a separate
    # dependency and FastAPI raises at IMPORT the moment it sees a `File` parameter without
    # it — which is exactly the defect this file's first run found in the orchestrator, and
    # which no test can find, because every test imports the app from a checkout where the
    # dependency happens to be installed.
    print("\nuploading a bank, with no graph — the author never writes one")
    upload = {
        "schema_version": 2,
        "competency": "smoke upload",
        "items": [
            {
                "item_id": f"u{n}",
                "modality": "mcq",
                "competency": "Uploaded",
                "sub_competency": "U.1 · uploaded sub-competency",
                "measures": [{"variable": "U.1", "weight": 1.0}],
                "cat": {"a": 1.0, "b": 0.0, "c": 0.25},
                "mcq": {"stem": "2 + 2?", "options": ["3", "4"], "answer_index": 1},
            }
            for n in range(3)
        ],
    }
    call("DELETE", f"{INGEST}/banks/SMOKE-UP")
    status, receipt = upload_file(f"{INGEST}/banks/SMOKE-UP", upload)
    failures += not check(
        "one file in, a registered bank out",
        status == 200 and receipt.get("status") == "registered",
        json.dumps(receipt)[:200],
    )
    derived = (receipt or {}).get("derived_graph") or {}
    failures += not check(
        "a graph was derived from the questions",
        derived.get("mains") == ["U"] and derived.get("sub_competencies") == ["U.1"],
        json.dumps(derived)[:160],
    )
    failures += not check(
        "every derived prerequisite edge ships inert",
        derived.get("prerequisite_edges_are_inert") is True,
    )

    status, banks = call("GET", f"{REGISTRY}/banks")
    failures += not check(
        "the registry sees it with no restart",
        status == 200 and any(b["bank_id"] == "SMOKE-UP" for b in banks),
    )

    # --- scoping an assessment to some of a bank's competencies --------------
    print("\nscoping an assessment to selected competencies")
    status, manifest = call(
        "POST", f"{SCOPE}/scopes", {"bank_id": "DA", "selected": ["DA.1", "DA.2"]}
    )
    failures += not check(
        "a selection becomes a sub-graph and an allowlist",
        status == 200 and bool(manifest.get("item_ids")),
        json.dumps(manifest)[:160],
    )
    failures += not check(
        "a partially selected main says so",
        any(m["main"] == "DA" and m["partial"] for m in manifest.get("mains", [])),
    )
    # Asserted on the serialised body rather than on the model, so a field that arrived
    # through a nested object fails here rather than in a candidate's report.
    forbidden = [f for f in ("theta_hat", "posterior", "standard_error")
                 if f in json.dumps(manifest)]
    failures += not check(
        "the manifest carries no ability estimate", not forbidden, str(forbidden)
    )

    status, session = call(
        "POST",
        f"{ORCHESTRATOR}/assessments",
        {"bank_id": "DA", "use_llm": False, "seed": 5, "scope": {"selected": ["DA.1", "DA.2"]}},
    )
    failures += not check("a scoped assessment begins", status == 201, json.dumps(session)[:200])
    if status == 201:
        presented = (session.get("presenting") or {}).get("item", {}).get("item_id", "")
        failures += not check(
            "the presented item is inside the scope",
            presented in set(manifest.get("item_ids", [])),
            presented,
        )
        call("DELETE", f"{ORCHESTRATOR}/assessments/{session['session_id']}")

    call("DELETE", f"{INGEST}/banks/SMOKE-UP")

    print(f"\n{'FAILED' if failures else 'PASSED'} — {failures} failing check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
