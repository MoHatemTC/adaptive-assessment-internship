"""End-to-end check for Approach 3 (full LLM CAT), without an API key.

This branch gives the LLM the most rope, so the boundaries matter most here. The
properties asserted are the ones that make it adaptive *testing* rather than a chatbot
with a question list:

  * selection happens on an engine-scored shortlist, computed at the LLM's new theta
  * the LLM cannot administer an item outside that shortlist
  * the LLM cannot end the assessment early — stopping is a code-applied rule
  * an update that moves theta the wrong way is rejected
  * the coded EAP is never in the prompt
  * hostile / malformed responses degrade to the coded path

Run: python test_approach.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import llm_full_cat
from engine import MAX_QUESTIONS, SE_TARGET, eap_update, p_correct, prior_from_level
from llm_full_cat import (
    DEVIATION_REJECT,
    check_direction,
    llm_full_step,
    posterior_from_theta_se,
)

BANK = Path(__file__).parent / "enriched_bank_cat.json"
failures: list[str] = []
payloads: list[dict] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok   ' if cond else 'FAIL '} {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def stub(math_resp, select_resp=None, *, raises: Exception | None = None):
    """Route the two phases to canned responses by inspecting the system prompt."""
    def fake(system: str, user: str):
        payload = json.loads(user)
        payloads.append(payload)
        if raises:
            raise raises
        if "selector" in system.lower():
            r = select_resp(payload) if callable(select_resp) else select_resp
            return r if r is not None else {}
        return math_resp(payload) if callable(math_resp) else math_resp
    llm_full_cat.chat_json = fake


def newton(p):
    prev, it = p["previous_state"], p["item"]
    th, se_prev = prev["theta_prev"], prev["se_prev"]
    a, b, c = it["a"], it["b"], it["c"]
    x = p["response"]["x"]
    P = c + (1 - c) / (1 + np.exp(-a * (th - b)))
    score = a * (x - P) * (P - c) / (P * (1 - c))
    info = a**2 * ((P - c) / (1 - c))**2 * (1 - P) / P
    prec = 1 / se_prev**2 + info
    return {"theta_hat": float(th + score / prec), "se": float(np.sqrt(1 / prec)),
            "certainty_pct": 50, "calculation_steps": ["newton"], "math_note": "newton",
            "should_stop": False, "stop_reason": ""}


def pick_top(p):
    return {"selected_id": p["shortlist"][0]["id"], "procedure_steps": ["stub"],
            "selection_note": "stub", "rule_applied": "highest information",
            "rephrased_stem": ""}


def fresh_state(theta=0.0, se=1.7):
    return {"posterior": prior_from_level(3, se), "theta_hat": theta, "se": se,
            "q_count": 0, "self_confidence": "low", "prior_sd": se, "se_start": se,
            "served_ids": [], "certainty_pct": 0.0}


def main() -> int:
    bank = json.loads(BANK.read_text())
    pool = [q for q in bank if q["competency"] == "Agentic AI & Orchestration"]
    item = pool[0]

    print("Approach 3 — full LLM CAT (LLM math + LLM selection)\n")

    # --- selection runs on an engine-scored shortlist -------------------------
    payloads.clear()
    stub(newton, pick_top)
    st = fresh_state()
    res = llm_full_step(st, pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=True)
    sel_payload = next((p for p in payloads if "shortlist" in p), None)
    expect(sel_payload is not None, "a selection phase ran with a shortlist")
    expect(sel_payload and all("info_score" in s for s in sel_payload["shortlist"]),
           "every shortlist candidate carries an engine-computed info_score")
    expect(sel_payload and len(sel_payload["shortlist"]) <= 5,
           "shortlist is bounded, not the whole unserved pool",
           f"n={len(sel_payload['shortlist']) if sel_payload else '?'}")
    expect(sel_payload and sel_payload["criterion"] in ("KL", "Fisher", "E[Fisher]"),
           "the criterion is stated by the engine")
    # The shortlist must be scored at the LLM's UPDATED theta, not the stale one.
    expect(sel_payload and abs(sel_payload["theta_hat"] - round(res.theta_hat, 3)) < 1e-6,
           "shortlist is scored at the updated θ̂, not the pre-answer θ̂",
           f"payload={sel_payload['theta_hat'] if sel_payload else '?'} vs {res.theta_hat:.3f}")
    expect(res.selection is not None and res.selection.item["id"] in res.selection.shortlist_ids,
           "administered item came from the shortlist")

    # --- coded answer is not leaked into either prompt ------------------------
    _p, coded_theta, _cse = eap_update(fresh_state()["posterior"], item, True)
    blob = json.dumps(payloads)
    expect(not any(k in blob for k in ("coded_reference", "coded_theta", "coded_se")),
           "coded reference is not in any payload")
    expect(not any(f"{coded_theta:.{d}f}" in blob for d in range(2, 5)),
           "coded theta value does not appear in any payload")

    # --- LLM cannot invent an item -------------------------------------------
    stub(newton, {"selected_id": "NOPE-999", "rephrased_stem": ""})
    res = llm_full_step(fresh_state(), pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=True)
    expect(res.selection is not None and res.selection.item["id"] in [q["id"] for q in pool],
           "invented id falls back to a real engine-selected item")
    expect(res.selection is not None and not res.selection.llm_used,
           "invented id marks the selection as engine-made")

    # --- LLM cannot stop the test early --------------------------------------
    stub(lambda p: {**newton(p), "should_stop": True, "stop_reason": "I feel done"}, pick_top)
    st = fresh_state(se=1.7)
    res = llm_full_step(st, pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=True)
    expect(not res.stop, "LLM asking to stop does NOT stop the test while SE > target",
           f"se={res.se:.2f} target={SE_TARGET}")
    expect(res.llm_wanted_stop and res.stop_disagreement,
           "the model's stop opinion is recorded as a disagreement")
    expect(res.selection is not None, "a next item is still selected despite the LLM's request")

    # --- the LLM's claimed SE cannot stop the test either ---------------------
    # This used to assert the opposite: stub se=0.3 and expect a stop. That was the bug.
    # The prompt's Newton SE is monotonically non-increasing (info >= 0) and never widened
    # once in 48,000 measured updates, while the grid EAP SE rises ~10% of the time after
    # a surprising response. A stopping rule fed by a number that cannot move against it
    # guarantees nothing, so code derives the SE and the rule runs on that.
    stub(lambda p: {**newton(p), "se": 0.21, "should_stop": True}, pick_top)
    res = llm_full_step(fresh_state(), pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=True)
    expect(res.se > 0.5,
           "an absurdly confident LLM se is not the se the rule sees",
           f"llm claimed 0.21, rule saw {res.se:.3f}")
    expect(not res.stop,
           "the LLM cannot end the test by reporting a tiny se",
           f"se={res.se:.3f} certainty={res.certainty_pct:.1f}%")

    # --- the rule does stop once the posterior is genuinely narrow ------------
    # The stop comes from the *belief*, not from a number anyone asserted: a narrow prior
    # makes the code-derived SE small, and the confidence rule fires on that. (Reaching
    # SE ≤ 0.65 from a flat prior takes more than 12 items on a 4-option bank — c = 0.25
    # means information genuinely vanishes at low θ — so the narrow state is constructed
    # rather than answered into.)
    stub(lambda p: {**newton(p), "should_stop": False}, pick_top)
    res = llm_full_step(fresh_state(se=0.4), pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=True)
    expect(res.stop and res.stop_rule_reason == "confidence",
           "the rule stops on a narrow posterior even when the LLM wants to continue",
           f"stop={res.stop} reason={res.stop_rule_reason!r} "
           f"certainty={res.certainty_pct:.1f}% se={res.se:.3f}")
    expect(res.converged, "a rule-driven stop on a measurement criterion reports converged")

    # --- direction invariant --------------------------------------------------
    expect(check_direction(0.0, -0.4, True) != "", "correct answer moving θ̂ down is a violation")
    expect(check_direction(0.0, 0.2, True) == "", "rising SE is not conflated with a violation")
    stub(lambda p: {**newton(p), "theta_hat": -3.0}, pick_top)
    st = fresh_state(theta=0.0)
    _p2, ct, _c2 = eap_update(st["posterior"], item, True)
    res = llm_full_step(st, pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=True)
    expect(res.invariant_violation and res.fallback_used,
           "wrong-direction update is rejected")
    expect(abs(res.theta_hat - ct) < 1e-9, "rejected update falls back to the coded EAP")

    # --- magnitude gate --------------------------------------------------------
    # Direction alone is not a check: theta_prev + 3.9 passes it, and so does
    # theta_prev + 1e-4. A θ̂ further from the coded EAP than a correct Newton update ever
    # lands is rejected, and counted as a magnitude failure rather than mislabelled an
    # invariant violation — the two mean different things about the model.
    st = fresh_state(theta=0.0)
    _p3, ct3, _c3 = eap_update(st["posterior"], item, True)
    stub(lambda p: {**newton(p), "theta_hat": ct3 + DEVIATION_REJECT + 0.3}, pick_top)
    res_far = llm_full_step(st, pool, "Agentic AI & Orchestration",
                            answered_item=item, is_correct=True, use_llm=True)
    expect(res_far.fallback_used and res_far.deviation_rejected
           and not res_far.invariant_violation,
           "a θ̂ past DEVIATION_REJECT is rejected as a magnitude failure, not miscounted "
           "as an invariant violation",
           f"fallback={res_far.fallback_used} dev_rejected={res_far.deviation_rejected} "
           f"invariant={res_far.invariant_violation!r}")
    expect(abs(res_far.theta_hat - ct3) < 1e-9,
           "a magnitude-rejected update falls back to the coded EAP")

    # Inside the gate the update stands: the branch exists to let the model do the maths,
    # and a gate that rejects normal Newton-vs-grid drift would make it a copy of the
    # engine wearing an LLM's name.
    st = fresh_state(theta=0.0)
    _p4, ct4, _c4 = eap_update(st["posterior"], item, True)
    stub(lambda p: {**newton(p), "theta_hat": ct4 + DEVIATION_REJECT * 0.8}, pick_top)
    res_near = llm_full_step(st, pool, "Agentic AI & Orchestration",
                             answered_item=item, is_correct=True, use_llm=True)
    expect(not res_near.fallback_used and not res_near.deviation_rejected,
           "an off-target θ̂ inside the gate is administered, not rejected",
           f"dev={res_near.theta_deviation:.3f} gate={DEVIATION_REJECT}")

    # --- hostile: LLM raises / junk -------------------------------------------
    stub(None, None, raises=RuntimeError("gateway down"))
    res = llm_full_step(fresh_state(), pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=True)
    expect(res.fallback_used and abs(res.theta_hat - ct) < 1e-9,
           "LLM exception falls back to coded EAP")
    expect(res.stop or res.selection is not None,
           "a failed step still yields either a next item or a stop")

    stub({"junk": 1}, {"junk": 1})
    res = llm_full_step(fresh_state(), pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=True)
    expect(abs(res.theta_hat - ct) < 1e-9, "malformed math falls back to the coded value")

    # --- use_llm=False is fully coded -----------------------------------------
    res = llm_full_step(fresh_state(), pool, "Agentic AI & Orchestration",
                        answered_item=item, is_correct=True, use_llm=False)
    expect(res.fallback_used and abs(res.theta_hat - ct) < 1e-9, "use_llm=False uses coded EAP")
    expect(res.selection is not None and not res.selection.llm_used,
           "use_llm=False uses engine selection")

    # --- full session terminates and recovers ability -------------------------
    stub(newton, pick_top)
    rng = np.random.default_rng(17)
    errs = []
    for true_theta in (-2.0, 0.0, 2.0):
        st = fresh_state(se=1.7)
        st["posterior"] = prior_from_level(int(np.clip(round(3 + true_theta), 1, 5)), 1.7)
        # First item, no answer yet.
        res = llm_full_step(st, pool, "Agentic AI & Orchestration", use_llm=True)
        guard = 0
        while res.selection is not None and not res.stop and guard < MAX_QUESTIONS + 5:
            guard += 1
            it = res.selection.item
            correct = bool(rng.random() < p_correct(true_theta, it["a"], it["b"], it["c"]))
            st["current_item"] = it
            res = llm_full_step(st, pool, "Agentic AI & Orchestration",
                                answered_item=it, is_correct=correct, use_llm=True)
            st["theta_hat"], st["se"] = res.theta_hat, res.se
            # The exact grid posterior the step carried, not a Gaussian rebuilt from its
            # two moments. This test kept the old round-trip after the code stopped doing
            # it, manufacturing drift the real path does not have — and once the magnitude
            # gate landed, that drift was large enough to reject correct maths.
            st["posterior"] = res.posterior
            st["q_count"] += 1
            st["served_ids"].append(it["id"])
        expect(guard <= MAX_QUESTIONS, f"session at θ={true_theta:+.0f} respects MAX_QUESTIONS",
               f"ran {guard}")
        expect(len(set(st["served_ids"])) == len(st["served_ids"]),
               f"session at θ={true_theta:+.0f} never repeats an item")
        errs.append(abs(st["theta_hat"] - true_theta))
    expect(float(np.mean(errs)) < 1.3, "full-LLM θ̂ lands near true ability",
           f"mean |error| = {np.mean(errs):.2f}")

    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)}): ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
