"""Offline checks for Approach 3 pure LLM CAT control.

Run: python test_approach.py
"""

from __future__ import annotations

import json
from pathlib import Path

import llm_full_cat
from engine import prior_from_level
from llm_full_cat import llm_full_step

BANK = Path(__file__).parent / "enriched_bank_cat.json"
failures: list[str] = []
payloads: list[dict] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok   ' if cond else 'FAIL '} {label}" + (f"  -- {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def fresh_state(theta=0.0, se=1.7):
    post = prior_from_level(3, se)
    return {
        "posterior": post,
        "theta_hat": theta,
        "se": se,
        "q_count": 0,
        "served_ids": [],
        "history": [],
        "level_history": [],
        "certainty_pct": 20.0,
        "self_confidence": "low",
        "prior_sd": se,
        "se_start": se,
    }


def stub(response):
    def fake(_system: str, user: str):
        payload = json.loads(user)
        payloads.append(payload)
        return response(payload) if callable(response) else response
    llm_full_cat.chat_json = fake


def controller_pick(first_id: str):
    return {
        "theta_hat": 0.25,
        "se": 1.2,
        "certainty_pct": 35,
        "should_stop": False,
        "converged": False,
        "stop_reason": "",
        "selected_id": first_id,
        "calculation_steps": ["theta update"],
        "selection_reason": "full controller chose the item",
        "rule_applied": "informative near theta with coverage",
        "rephrased_stem": "",
    }


def main() -> int:
    bank = json.loads(BANK.read_text())
    pool = [q for q in bank if q["competency"] == "Agentic AI & Orchestration"]
    first_id = pool[0]["id"]
    item = pool[0]

    print("Approach 3 -- pure LLM CAT controller\n")

    payloads.clear()
    stub(controller_pick(first_id))
    res = llm_full_step(fresh_state(), pool, "Agentic AI & Orchestration", use_llm=True)
    expect(payloads and "available_items" in payloads[-1],
           "controller receives the available item pool, not a code-ranked shortlist")
    expect(payloads and "shortlist" not in payloads[-1],
           "payload does not hand the model an engine-decided shortlist")
    expect(res.selection is not None and res.selection.item["id"] == first_id,
           "LLM-selected available id is administered")
    expect(res.selection is not None and res.selection.llm_used,
           "selection is recorded as LLM-made")

    payloads.clear()
    stub({**controller_pick("NOPE-999"), "selected_id": "NOPE-999"})
    res = llm_full_step(fresh_state(), pool, "Agentic AI & Orchestration", use_llm=True)
    expect(res.invalid_llm_step and res.selection is None and res.stop,
           "invented id invalidates the LLM step instead of falling back to coded selection")
    expect(not res.fallback_used, "invalid selection does not set coded fallback")

    stub({**controller_pick(first_id), "should_stop": True, "selected_id": "",
          "stop_reason": "llm_confident", "converged": True})
    res = llm_full_step(fresh_state(), pool, "Agentic AI & Orchestration", use_llm=True)
    expect(res.stop and res.selection is None and res.stop_rule_reason == "llm_confident",
           "LLM stop decision is honored")
    expect(res.converged, "LLM convergence flag is preserved")

    stub({**controller_pick(first_id), "theta_hat": -0.5})
    res = llm_full_step(
        fresh_state(theta=0.0), pool, "Agentic AI & Orchestration",
        answered_item=item, is_correct=True, use_llm=True,
    )
    expect(res.invalid_llm_step and "moved theta down" in res.invalid_reason,
           "wrong-direction math invalidates the pure LLM step")

    if failures:
        print("\nFailures:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
