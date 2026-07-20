"""Offline checks for Approach 3 pure LLM CAT control.

Run: python test_approach.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

import llm_full_cat
from engine import eap_update, prior_from_level
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
    def fake(_system: str, user: str, **_kwargs):
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


def competent_controller(pool, true_theta: float):
    """A stub that runs the CAT correctly: coded EAP maths, argmax-Fisher selection.

    The point is not that a stub can do IRT — it is code, of course it can. It is that
    the ARCHITECTURE recovers ability when the controller behaves, so that a bad score
    from a real model is attributable to the model rather than to a broken loop. Every
    other branch has this check; approach 3, which hands the model the most authority
    and aborts the session outright on a bad step, had no session-level assertion at
    all — the branch most able to fail end-to-end was the one with no end-to-end test.
    """
    import numpy as np

    from engine import GRID, eap_update, fisher_info

    state = {"post": None}

    def respond(payload):
        prev = payload["previous_state"]
        served = set(payload["served_ids"])
        latest = payload.get("latest_response")

        if state["post"] is None:
            state["post"] = prior_from_level(3, 1.7)
        if latest is not None:
            item = next(q for q in pool if q["id"] == latest["item"]["id"])
            state["post"], theta, se = eap_update(state["post"], item, latest["correct"])
        else:
            theta, se = prev["theta_hat"], prev["se"]

        avail = [q for q in pool if q["id"] not in served]
        stop = (not avail) or se <= 0.65 or payload["questions_answered_after_update"] >= 12
        pick = "" if stop else max(avail, key=lambda q: fisher_info(theta, q))["id"]
        return {
            "theta_hat": round(float(theta), 4),
            "se": round(float(np.clip(se, 0.2, 2.5)), 4),
            "certainty_pct": 50.0,
            "should_stop": bool(stop),
            "converged": bool(se <= 0.65),
            "stop_reason": "confidence" if se <= 0.65 else "",
            "selected_id": pick,
            "calculation_steps": ["coded EAP"],
            "selection_reason": "max Fisher at theta_hat",
            "rule_applied": "max Fisher",
            "rephrased_stem": "",
        }

    state["reset"] = lambda: state.update(post=None)
    return respond, state


def drift_checks(pool) -> None:
    """The comparator must see cumulative drift, not just one step of it.

    A model that adds a constant +0.25 to theta every question is wrong by +2.25 after
    nine questions, and that is precisely the failure a full-LLM controller is most
    likely to have: not one absurd answer, but a steady lean the numbers never flag.

    The old comparator re-derived its reference from the model's OWN previous (theta,
    se) each step, so it re-anchored to wherever the model had walked to and could only
    ever report the size of a single increment. This drives exactly that biased model
    and asserts the reported deviation grows.
    """
    import numpy as np

    from llm_full_cat import fresh_audit_posterior

    print("\n  comparator drift detection (biased vs faithful, same responses)")

    def run(make_reply) -> list[float]:
        state = fresh_state()
        state["audit_posterior"] = fresh_audit_posterior(prior_sd=1.7)
        post = {"p": prior_from_level(3, 1.7)}
        devs: list[float] = []

        def reply(payload):
            return make_reply(payload, post, state, pool)

        stub(reply)
        res = llm_full_step(state, pool, "Agentic AI & Orchestration", use_llm=True)
        for _ in range(8):
            if res.selection is None or res.invalid_llm_step:
                break
            it = res.selection.item
            res = llm_full_step(state, pool, "Agentic AI & Orchestration",
                                answered_item=it, is_correct=True, use_llm=True)
            if res.audit_posterior is not None:
                state["audit_posterior"] = res.audit_posterior
            if res.invalid_llm_step:
                break
            if res.theta_deviation is not None:
                devs.append(res.theta_deviation)
            state["posterior"] = res.posterior
            state["theta_hat"], state["se"] = res.theta_hat, res.se
            state["certainty_pct"] = res.certainty_pct
            state["q_count"] += 1
            state["served_ids"].append(it["id"])
        return devs

    def _next_id(payload, pool):
        return next(q["id"] for q in pool if q["id"] not in payload["served_ids"])

    def biased(payload, _post, _state, pool):
        # +0.25 every step regardless of evidence. Safe against the direction invariant
        # because the run answers everything correctly, so theta only ever rises.
        return {**controller_pick(""), "se": 1.0, "certainty_pct": 40.0,
                "should_stop": False, "selected_id": _next_id(payload, pool),
                "theta_hat": round(payload["previous_state"]["theta_hat"] + 0.25, 4)}

    def faithful(payload, post, _state, pool):
        # Runs the coded EAP itself, so it should agree with the comparator throughout.
        latest = payload.get("latest_response")
        if latest is not None:
            it = next(q for q in pool if q["id"] == latest["item"]["id"])
            post["p"], theta, se = eap_update(post["p"], it, latest["correct"])
        else:
            theta, se = payload["previous_state"]["theta_hat"], payload["previous_state"]["se"]
        return {**controller_pick(""), "theta_hat": round(float(theta), 4),
                "se": round(float(np.clip(se, 0.2, 2.5)), 4), "certainty_pct": 40.0,
                "should_stop": False, "selected_id": _next_id(payload, pool)}

    bad, good = run(biased), run(faithful)
    expect(len(bad) >= 5 and len(good) >= 5, "drift runs produced enough steps to judge",
           f"biased {len(bad)}, faithful {len(good)}")
    if len(bad) >= 5 and len(good) >= 5:
        expect(bad[-1] > bad[0],
               "reported deviation GROWS as the controller drifts from the evidence",
               f"first {bad[0]:.3f} -> last {bad[-1]:.3f}")
        # The real assertion. A comparator anchored to the model's own previous answer
        # reports roughly the per-step increment for BOTH controllers and cannot tell
        # them apart; an independent one separates them by an order of magnitude.
        expect(max(good) < 0.05,
               "a faithful controller reads as agreeing with the comparator",
               f"max deviation {max(good):.4f}")
        expect(bad[-1] > 10 * max(max(good), 1e-3),
               "a drifting controller is separated from a faithful one, not blurred with it",
               f"biased {bad[-1]:.3f} vs faithful max {max(good):.4f}")


def session_checks(pool) -> None:
    """Drive whole sessions end-to-end, the check this branch was missing."""
    import numpy as np

    from engine import MAX_QUESTIONS, p_correct

    print("\n  session-level (whole competencies, competent stub controller)")
    errors: list[float] = []
    aborted = 0
    over_budget = 0
    repeated = 0

    for idx, true_theta in enumerate((-2.0, -1.0, 0.0, 1.0, 2.0)):
        # Seeded off the index, not theta: default_rng rejects negative seeds, so
        # int(100 * true_theta) raises at the first grid point.
        rng = np.random.default_rng(1000 + idx)
        respond, state = competent_controller(pool, true_theta)
        state["reset"]()
        stub(respond)

        st = fresh_state()
        res = llm_full_step(st, pool, "Agentic AI & Orchestration", use_llm=True)
        for _ in range(MAX_QUESTIONS):
            if res.invalid_llm_step:
                aborted += 1
                break
            if res.selection is None:
                break
            it = res.selection.item
            if it["id"] in st["served_ids"]:
                repeated += 1
                break
            correct = bool(rng.random() < p_correct(true_theta, it["a"], it["b"], it["c"]))
            res = llm_full_step(st, pool, "Agentic AI & Orchestration",
                                answered_item=it, is_correct=correct, use_llm=True)
            if res.invalid_llm_step:
                aborted += 1
                break
            st["posterior"] = res.posterior
            st["theta_hat"], st["se"] = res.theta_hat, res.se
            st["certainty_pct"] = res.certainty_pct
            st["q_count"] += 1
            st["served_ids"].append(it["id"])
            if res.stop:
                break
        if st["q_count"] > MAX_QUESTIONS:
            over_budget += 1
        errors.append(abs(st["theta_hat"] - true_theta))

    mean_err = float(np.mean(errors))
    expect(aborted == 0, "no session aborts when the controller behaves",
           f"{aborted} aborted")
    expect(repeated == 0, "an item is never administered twice in a session",
           f"{repeated} repeats")
    expect(over_budget == 0, f"no session exceeds MAX_QUESTIONS", f"{over_budget} over")
    expect(mean_err < 1.3, "theta is recovered end-to-end through the pure LLM loop",
           f"mean |error| {mean_err:.3f}")


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

    drift_checks(pool)
    session_checks(pool)

    if failures:
        print("\nFailures:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
