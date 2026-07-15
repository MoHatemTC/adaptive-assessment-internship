"""End-to-end check for Approach 2 (LLM math, coded selection), without an API key.

The properties that must hold no matter what the model returns:

  * the coded answer is never in the prompt — otherwise the branch measures copying
  * an update that moves theta the wrong way is rejected, not administered
  * deviation from the coded EAP is measured on every step (this branch's output)
  * malformed / hostile responses degrade to the coded EAP rather than crashing
  * a full session still terminates and recovers ability

Run: python test_approach.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import llm_math
from engine import MAX_QUESTIONS, SE_TARGET, eap_update, p_correct, prior_from_level, select_item
from llm_math import check_direction, llm_math_update, posterior_from_theta_se

BANK = Path(__file__).parent / "enriched_bank_cat.json"
failures: list[str] = []
seen_payloads: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok   ' if cond else 'FAIL '} {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def stub(response, *, raises: Exception | None = None):
    def fake(system: str, user: str):
        seen_payloads.append(user)
        if raises:
            raise raises
        return response(json.loads(user)) if callable(response) else response
    llm_math.chat_json = fake


def fresh_state(pool, theta=0.0, se=1.7):
    post = prior_from_level(3, se)
    return {"posterior": post, "theta_hat": theta, "se": se, "q_count": 0,
            "self_confidence": "low", "prior_sd": se, "se_start": se, "served_ids": []}


def main() -> int:
    bank = json.loads(BANK.read_text())
    pool = [q for q in bank if q["competency"] == "Data & ML Foundations"]
    item = pool[0]

    print("Approach 2 — LLM math, coded selection\n")

    # --- the answer must not be in the prompt --------------------------------
    stub(lambda p: {"theta_hat": 0.5, "se": 1.2, "certainty_pct": 40,
                    "calculation_steps": ["stub"], "math_note": "stub"})
    st = fresh_state(pool)
    _post, coded_theta, coded_se = eap_update(st["posterior"], item, True)
    res = llm_math_update(st, item, True, use_llm=True)
    payload = seen_payloads[-1]
    leaked = any(k in payload for k in ("coded_reference", "coded_theta", "coded_se"))
    expect(not leaked, "coded reference is not in the LLM payload")
    # Stronger: the coded value must not appear numerically at any rounding.
    numeric_leak = any(f"{coded_theta:.{d}f}" in payload for d in range(1, 5))
    expect(not numeric_leak, "coded theta value does not appear in the payload",
           f"coded_theta={coded_theta:.4f}")
    expect("a" in json.loads(payload)["item"] and "b" in json.loads(payload)["item"],
           "LLM still receives the item parameters it needs to do the math")

    # --- deviation measured against coded EAP --------------------------------
    expect(res.coded_theta is not None and abs(res.coded_theta - coded_theta) < 1e-9,
           "coded EAP is computed for comparison")
    expect(res.theta_deviation is not None
           and abs(res.theta_deviation - abs(0.5 - coded_theta)) < 1e-9,
           "theta deviation from coded EAP is recorded")

    # --- direction invariant --------------------------------------------------
    expect(check_direction(0.0, -0.4, True) != "", "correct answer moving θ̂ down is a violation")
    expect(check_direction(0.0, 0.4, False) != "", "incorrect answer moving θ̂ up is a violation")
    expect(check_direction(0.0, 0.4, True) == "", "correct answer moving θ̂ up is fine")
    expect(check_direction(0.0, -0.4, False) == "", "incorrect answer moving θ̂ down is fine")
    # SE rising must NOT be treated as a violation — measured at 23% of real EAP updates.
    expect(check_direction(0.0, 0.2, True) == "", "rising SE is not conflated with a violation")

    # --- a wrong-direction LLM update is rejected -----------------------------
    stub({"theta_hat": -3.0, "se": 0.8, "certainty_pct": 60, "math_note": "backwards"})
    st = fresh_state(pool, theta=0.0)
    res = llm_math_update(st, item, True, use_llm=True)  # correct answer, theta plunges
    expect(res.fallback_used and res.invariant_violation,
           "wrong-direction update is rejected")
    expect(abs(res.theta_hat - coded_theta) < 1e-9,
           "rejected update falls back to the coded EAP value")

    # --- hostile: LLM raises / returns junk -----------------------------------
    stub(None, raises=RuntimeError("gateway down"))
    res = llm_math_update(fresh_state(pool), item, True, use_llm=True)
    expect(res.fallback_used and abs(res.theta_hat - coded_theta) < 1e-9,
           "LLM exception falls back to coded EAP")

    # A malformed response must fall back AND be *counted* as a fallback. Checking only
    # theta_hat is what let the accounting bug through: _num() defaulted to coded_theta
    # while fallback_used stayed False, so every garbage response was booked as a
    # successful LLM step with theta_deviation=0.0 -- biasing this branch's headline
    # mean |Δθ̂| optimistically with its own failures. The value being right is not
    # enough; the books have to be right too.
    for label, payload in [
        ("missing theta_hat", {"garbage": "yes"}),
        ("non-numeric theta_hat", {"theta_hat": "not-a-number", "se": None}),
        ("empty response", {}),
    ]:
        stub(payload)
        res = llm_math_update(fresh_state(pool), item, True, use_llm=True)
        expect(
            abs(res.theta_hat - coded_theta) < 1e-9
            and res.fallback_used
            and 0.2 <= res.se <= 2.5,
            f"{label} falls back AND is counted as a fallback",
            f"theta={res.theta_hat:.4f} fallback_used={res.fallback_used} se={res.se:.3f}",
        )

    # --- use_llm=False is pure coded ------------------------------------------
    res = llm_math_update(fresh_state(pool), item, True, use_llm=False)
    expect(res.fallback_used and abs(res.theta_hat - coded_theta) < 1e-9,
           "use_llm=False uses the coded EAP")

    # --- posterior reconstruction is a valid distribution ---------------------
    post = posterior_from_theta_se(1.0, 0.5)
    expect(abs(post.sum() - 1.0) < 1e-9 and (post >= 0).all(),
           "reconstructed posterior is normalised and non-negative")

    # --- SE is code's, not the model's ----------------------------------------
    # The LLM's Newton SE cannot widen (info >= 0), so a stopping rule fed by it
    # declared convergence 4x too often. Code derives SE from the posterior instead.
    st_ = fresh_state(pool)
    _p, coded_th, coded_se_ = eap_update(st_["posterior"], item, True)
    stub({"theta_hat": coded_th, "se": 0.21, "math_note": "absurdly confident"})
    res = llm_math_update(st_, item, True, use_llm=True)
    expect(abs(res.se - 0.21) > 1e-6,
           "the LLM's reported se does not become the assessment's se",
           f"llm said 0.21, assessment used {res.se:.3f}")
    # ...and when the LLM lands exactly on the posterior mean, the code-derived SE
    # reduces to the exact EAP SE. That equivalence is the point of measuring dispersion
    # about θ̂ rather than inventing a separate scale.
    expect(abs(res.se - coded_se_) < 1e-9,
           "a θ̂ on the posterior mean reproduces the EAP SE exactly",
           f"se {res.se:.6f} vs coded EAP {coded_se_:.6f}")
    expect(res.se_llm is not None and abs(res.se_llm - 0.21) < 1e-9,
           "the LLM's se is still recorded for comparison", f"se_llm={res.se_llm}")
    expect(res.posterior is not None and abs(res.posterior.sum() - 1.0) < 1e-9,
           "the exact grid posterior is carried, not rebuilt from two moments")

    # Measured about the LLM's theta_hat, so a wrong estimate reports as uncertain
    # rather than confidently wrong: sqrt(Var + (mean - theta_hat)^2).
    st_ = fresh_state(pool)
    _p2, coded_th2, coded_se2 = eap_update(st_["posterior"], item, True)
    stub({"theta_hat": coded_th2 + 1.0, "se": 0.3})
    res_off = llm_math_update(st_, item, True, use_llm=True)
    expect(res_off.se > coded_se2,
           "an off-target θ̂ widens the SE instead of hiding in it",
           f"θ̂ off by 1.0 -> se {res_off.se:.3f} vs coded {coded_se2:.3f}")

    # --- a full session with a competent LLM terminates and recovers ability ---
    # Model executes the documented Newton update correctly.
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
                "certainty_pct": 50, "calculation_steps": ["newton"], "math_note": "newton"}
    stub(newton)

    rng = np.random.default_rng(5)
    errs, viol = [], 0
    for true_theta in (-2.0, 0.0, 2.0):
        st = fresh_state(pool, se=1.7)
        st["posterior"] = prior_from_level(int(np.clip(round(3 + true_theta), 1, 5)), 1.7)
        served = []
        for qc in range(MAX_QUESTIONS):
            if st["se"] <= SE_TARGET:
                break
            nxt = select_item(st["theta_hat"], qc, pool, served, st["posterior"])
            if nxt is None:
                break
            correct = bool(rng.random() < p_correct(true_theta, nxt["a"], nxt["b"], nxt["c"]))
            r = llm_math_update(st, nxt, correct, use_llm=True)
            viol += bool(r.invariant_violation)
            st["theta_hat"], st["se"] = r.theta_hat, r.se
            st["posterior"] = posterior_from_theta_se(r.theta_hat, r.se)
            st["q_count"] += 1
            served.append(nxt["id"])
        expect(len(served) <= MAX_QUESTIONS, f"session at θ={true_theta:+.0f} respects MAX_QUESTIONS")
        errs.append(abs(st["theta_hat"] - true_theta))
    expect(viol == 0, "a correct Newton implementation never violates the direction invariant")
    expect(float(np.mean(errs)) < 1.3, "LLM-math θ̂ lands near true ability",
           f"mean |error| = {np.mean(errs):.2f}")

    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)}): ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
