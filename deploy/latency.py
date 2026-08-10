#!/usr/bin/env python3
"""What the split costs per candidate response, against the budget that bounds it.

    cd deploy && docker compose up -d && python latency.py

THE BUDGET, AND WHY IT IS THE NUMBER THAT MATTERS

`docs/microservices.md` allows the split **< 150 ms per response**. That is not a
performance preference. The session cap is 90 minutes and P90 already sits at 83 with the
coverage requirement on; 150 ms across a twelve-question session that also has to fit
grading and selection is the margin available before the split has eaten a question.

WHAT IS MEASURED

MCQ only, deliberately. A code response is sandbox-bound at ~30 s and a spoken one is
model-bound, so both would drown the number this exists to find — the hop cost the SPLIT
added, not the cost of the work.

The measurement is `POST /responses` wall clock, which contains everything the split
introduced: the grader hop (which itself fetches the item from the registry), the
propagation hop, and the orchestrator's own selection refill. In the monolith all three
were function calls.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import urllib.error
import urllib.request

ORCHESTRATOR = "http://localhost:8080"
REGISTRY = "http://localhost:8081"
BUDGET_MS = 150.0
SESSIONS = 6


def call(method: str, url: str, body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - fixed localhost scheme
        url, data=data, method=method, headers={"content-type": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310
            return json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read() or b"{}")


def main() -> int:
    answer_keys: dict[str, int] = {}
    timings: list[float] = []

    for run in range(SESSIONS):
        session = call(
            "POST",
            f"{ORCHESTRATOR}/assessments",
            {"bank_id": "DA", "use_llm": False, "seed": 100 + run},
        )
        if "session_id" not in session:
            print(f"could not begin an assessment: {session}", file=sys.stderr)
            return 1

        while not session["stop"]:
            item = session["presenting"]["item"]
            if item["modality"] != "mcq":
                break
            if item["item_id"] not in answer_keys:
                full = call("GET", f"{REGISTRY}/banks/DA/items/{item['item_id']}")
                answer_keys[item["item_id"]] = int(full["payload"]["answer_index"])

            started = time.perf_counter()
            session = call(
                "POST",
                f"{ORCHESTRATOR}/assessments/{session['session_id']}/responses",
                {"type": "mcq", "chosen_index": answer_keys[item["item_id"]]},
            )
            timings.append((time.perf_counter() - started) * 1000.0)
            if "presenting" not in session:
                print(f"unexpected response: {session}", file=sys.stderr)
                return 1

        call("DELETE", f"{ORCHESTRATOR}/assessments/{session['session_id']}")

    if not timings:
        print("no responses were timed", file=sys.stderr)
        return 1

    timings.sort()
    p50 = statistics.median(timings)
    p90 = timings[int(len(timings) * 0.9) - 1]
    worst = timings[-1]

    print(f"responses timed : {len(timings)} MCQ, across {SESSIONS} sessions")
    print(f"p50             : {p50:7.1f} ms")
    print(f"p90             : {p90:7.1f} ms")
    print(f"max             : {worst:7.1f} ms")
    print(f"budget          : {BUDGET_MS:7.1f} ms per response (docs/microservices.md)")

    # P90 rather than the max: one slow response does not cost a question, and a cold
    # cache on the first request of a session is a real but one-off effect.
    within = p90 <= BUDGET_MS
    print(f"\n{'PASSED' if within else 'FAILED'} — p90 is {p90:.1f} ms against {BUDGET_MS:.0f} ms")
    if not within:
        print(
            "\nThis is a whole response, not just the added hops: it includes MCQ grading "
            "and the selection refill, which the monolith also paid for.",
            file=sys.stderr,
        )
    return 0 if within else 1


if __name__ == "__main__":
    raise SystemExit(main())
