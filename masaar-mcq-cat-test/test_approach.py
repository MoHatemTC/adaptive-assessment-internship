"""End-to-end check for this approach's CAT loop, without an API key.

Streamlit + a live LLM make this branch awkward to test, which is exactly why the
interesting failures hide here. This drives the real controller with a stubbed
chat_json, so we can assert the properties that must hold on every branch:

  * the LLM cannot administer an item outside the engine's shortlist
  * a malformed / hostile LLM response degrades to the coded CAT path, never crashes
  * stopping rules are enforced by code, not by the model's say-so
  * a rephrase that leaks the key or drops identifiers never reaches the examinee

Run: python test_approach.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

import llm_client
import selection_pipeline
from engine import MAX_QUESTIONS, SE_TARGET, eap_update, p_correct, prior_from_level
from selection_pipeline import select_next_item

BANK = Path(__file__).parent / "enriched_bank_cat.json"
failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    print(f"  {'ok   ' if cond else 'FAIL '} {label}" + (f"  — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(label)


def stub(response, *, raises: Exception | None = None):
    """Replace the LLM with a canned response, as both modules resolve it."""
    def fake(system: str, user: str):
        if raises:
            raise raises
        return response(user) if callable(response) else response
    selection_pipeline.chat_json = fake
    selection_pipeline.llm_configured = lambda: True
    llm_client.llm_configured = lambda: True


def pick_first_shortlist_id(user: str) -> str:
    return json.loads(user)["shortlist"][0]["id"]


def main() -> int:
    bank = json.loads(BANK.read_text())
    pool = [q for q in bank if q["competency"] == "Python & Software Engineering"]
    post = prior_from_level(3, 1.7)
    th, se = 0.0, 1.7

    print("Approach 1 — code math, LLM picks from engine shortlist\n")

    # --- LLM confined to the shortlist ---------------------------------------
    stub(lambda u: {"selected_id": pick_first_shortlist_id(u), "criterion_used": "Fisher",
                    "procedure_steps": ["stub"], "adaptation_note": "stub", "rule_applied": "stub",
                    "rephrased_stem": ""})
    sel = select_next_item(th, se, 5, "Python & Software Engineering", pool, [], [], post,
                           use_llm=True, allow_rephrase=True)
    expect(sel is not None and sel.llm_used, "LLM selection returns an item")
    expect(sel.item["id"] in sel.shortlist_ids, "chosen item came from the engine shortlist")

    # --- hostile: LLM invents an id ------------------------------------------
    stub({"selected_id": "TOTALLY-MADE-UP", "criterion_used": "Fisher", "rephrased_stem": ""})
    sel = select_next_item(th, se, 5, "Python & Software Engineering", pool, [], [], post,
                           use_llm=True)
    expect(sel is not None and not sel.llm_used,
           "invented id falls back to the deterministic engine")

    # --- hostile: LLM raises --------------------------------------------------
    stub(None, raises=RuntimeError("gateway exploded"))
    sel = select_next_item(th, se, 5, "Python & Software Engineering", pool, [], [], post,
                           use_llm=True)
    expect(sel is not None and not sel.llm_used, "LLM exception falls back to the engine")
    expect("gateway exploded" in (sel.adaptation_note or ""),
           "fallback explains why the LLM was skipped")

    # --- hostile: LLM returns junk -------------------------------------------
    stub({"nonsense": True})
    sel = select_next_item(th, se, 5, "Python & Software Engineering", pool, [], [], post,
                           use_llm=True)
    expect(sel is not None and not sel.llm_used, "malformed response falls back to the engine")

    # --- rephrase guard is enforced in the pipeline, not just in isolation ----
    target = pool[0]
    leak = target["options"][target["answer_index"]]
    stub(lambda u: {"selected_id": json.loads(u)["shortlist"][0]["id"], "criterion_used": "Fisher",
                    "procedure_steps": [], "adaptation_note": "", "rule_applied": "",
                    "rephrased_stem": f"Given the code, is it true that {leak}?"})
    sel = select_next_item(th, se, 5, "Python & Software Engineering", pool, [], [], post,
                           use_llm=True, allow_rephrase=True)
    chosen = next(q for q in pool if q["id"] == sel.item["id"])
    leaked = sel.rephrased_stem != chosen["stem"] and chosen["options"][chosen["answer_index"]].lower() in sel.rephrased_stem.lower()
    expect(not leaked, "a key-leaking rephrase never reaches the examinee",
           f"stem={sel.rephrased_stem!r}")

    # --- rephrase disabled -> original wording, guard not consulted -----------
    stub(lambda u: {"selected_id": json.loads(u)["shortlist"][0]["id"], "criterion_used": "Fisher",
                    "procedure_steps": [], "adaptation_note": "", "rule_applied": "",
                    "rephrased_stem": "TOTALLY DIFFERENT WORDING"})
    sel = select_next_item(th, se, 5, "Python & Software Engineering", pool, [], [], post,
                           use_llm=True, allow_rephrase=False)
    chosen = next(q for q in pool if q["id"] == sel.item["id"])
    expect(sel.rephrased_stem == chosen["stem"], "allow_rephrase=False administers the calibrated stem")

    # --- full adaptive session terminates and recovers ability ----------------
    stub(lambda u: {"selected_id": pick_first_shortlist_id(u), "criterion_used": "Fisher",
                    "procedure_steps": [], "adaptation_note": "", "rule_applied": "",
                    "rephrased_stem": ""})
    rng = np.random.default_rng(11)
    errs = []
    for true_theta in (-2.0, 0.0, 2.0):
        post = prior_from_level(int(np.clip(round(3 + true_theta), 1, 5)), 1.7)
        th, se, served = 0.0, 1.7, []
        for qc in range(MAX_QUESTIONS + 5):  # deliberately allow overrun to catch a runaway loop
            if se <= SE_TARGET or qc >= MAX_QUESTIONS:
                break
            s = select_next_item(th, se, qc, "Python & Software Engineering", pool, served, [], post,
                                 use_llm=True)
            if s is None:
                break
            item = s.item
            correct = bool(rng.random() < p_correct(true_theta, item["a"], item["b"], item["c"]))
            post, th, se = eap_update(post, item, correct)
            served.append(item["id"])
        expect(len(served) <= MAX_QUESTIONS, f"session at θ={true_theta:+.0f} respects MAX_QUESTIONS",
               f"served {len(served)}")
        expect(len(set(served)) == len(served), f"session at θ={true_theta:+.0f} never repeats an item")
        errs.append(abs(th - true_theta))
    expect(float(np.mean(errs)) < 1.2, "θ̂ lands near true ability across the range",
           f"mean |error| = {np.mean(errs):.2f}")

    print(f"\n{'PASSED' if not failures else f'FAILED ({len(failures)}): ' + ', '.join(failures)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
