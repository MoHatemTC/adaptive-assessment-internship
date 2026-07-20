"""Streamlit harness for the code-question adaptive assessment.

Two audiences, one app:

  Assessment  take the adaptive test as a candidate would, to judge whether the questions
              and the difficulty trajectory feel right — things no automated metric sees.
  Inspector   run any seeded submission through the pipeline and see every layer: raw
              execution, static signals, the model's diagnosis, the criterion arithmetic
              and what it did to the learner model.

The inspector matters as much as the assessment. The whole design rests on the claim that
the model interprets evidence without controlling the score, and that claim should be
visible rather than asserted.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import streamlit as st

from cat.competency_state import LearnerModel
from cat.config import settings
from cat.pipeline import (
    apply_to_learner,
    audit_record,
    evaluate_submission,
    load_bank,
    result_to_dict,
)
from cat.selection import choose, evaluate_stop, filter_candidates, rank_candidates

SEEDED_DIR = Path(__file__).resolve().parent / "bank" / "seeded"

APPROACH_LABEL = {
    "A": "A · objective only — tests + static analysis, no LLM scoring",
    "B": "B · test-anchored LLM — model interprets, tests anchor correctness",
    "C": "C · LLM-led — model scores every criterion, hard contradictions blocked",
}

st.set_page_config(page_title="Code CAT", layout="wide")


@st.cache_data
def bank() -> list[dict]:
    return load_bank()


def session() -> dict:
    if "run" not in st.session_state:
        st.session_state["run"] = {
            "id": f"session_{uuid.uuid4().hex[:8]}",
            "model": LearnerModel(),
            "answered": [],
            "audit": [],
            "started": time.time(),
            "current": None,
        }
    return st.session_state["run"]


def render_state(model: LearnerModel) -> None:
    snapshot = model.snapshot()
    if not snapshot:
        st.caption("No competency evidence yet.")
        return
    rows = [
        {
            "competency": cid,
            "mastery": f"{s['mastery']:.3f}" if s["observed"] else "—",
            "std error": f"{s['standard_error']:.3f}",
            "evidence": s["evidence_count"],
            "misconceptions": ", ".join(s["misconception_codes"]) or "—",
        }
        for cid, s in snapshot.items()
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption(
        "Mastery shows — for competencies with no evidence yet. A Beta(1,1) prior has a "
        "mean of exactly 0.5, which would otherwise read as a measured mid-level result "
        "rather than as absence of evidence."
    )


def render_evaluation(result) -> None:
    """Every layer, in the order it was computed."""
    payload = result_to_dict(result)
    execution = result.execution

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tests passed", f"{execution.passed_tests}/{execution.total_tests}")
    c2.metric("Compiled", "yes" if execution.compiled else "no")
    c3.metric("Overall", f"{result.overall_score:.3f}")
    c4.metric("Latency", f"{result.evaluation_latency_ms:,}ms")

    if not execution.usable:
        st.error(
            "Sandbox unavailable — this run measured our infrastructure, not the "
            "candidate. The learner model was not updated."
        )

    st.markdown("**1 · Objective evidence** (deterministic; nothing may contradict it)")
    st.dataframe(
        [
            {
                "test": o.test_id,
                "passed": "✓" if o.passed else "✗",
                "failure": o.failure_type or "—",
                "detail": o.detail[:80] or "—",
            }
            for o in execution.test_results
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**2 · Static signals** (structure; catches what tests cannot)")
    signals = result.signals.as_dict()
    st.json({k: v for k, v in signals.items() if k != "warnings"}, expanded=False)
    for warning in signals["warnings"]:
        st.warning(warning)

    st.markdown("**3 · Model diagnosis**")
    if not result.llm.available:
        st.info(
            "No model evidence. "
            + ("Approach A does not call the model at all." if result.approach == "A"
               else "The model was unavailable or its reply was rejected — the objective "
                    "evidence carried the score.")
        )
    else:
        st.caption(f"confidence {result.llm.evaluation_confidence:.2f} — "
                   f"{result.llm.overall_diagnostic}")
        st.dataframe(
            [
                {
                    "criterion": e.criterion_id,
                    "competency": e.competency_id,
                    "score": f"{e.score:.2f}",
                    "conf": f"{e.confidence:.2f}",
                    "misconception": e.misconception_code or "—",
                }
                for e in result.llm.criterion_evidence
            ],
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("**4 · Criterion scores** (code combines the sources; weights per approach)")
    st.dataframe(
        [
            {
                "criterion": c.criterion_id,
                "score": f"{c.score:.3f}",
                "sources": ", ".join(f"{k} {v:.0%}" for k, v in c.sources_used.items()) or "—",
                "conflict": c.conflict_flag or "—",
            }
            for c in result.criterion_scores
        ],
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("**5 · Competency evidence** (what reaches the learner model)")
    st.dataframe(
        [
            {
                "competency": e.competency_id,
                "score": f"{e.score:.3f}",
                "strength": f"{e.evidence_strength:.2f}",
                "source": e.source,
                "misconceptions": ", ".join(e.misconception_codes) or "—",
            }
            for e in result.competency_evidence
        ],
        use_container_width=True,
        hide_index=True,
    )

    for flag in result.flags:
        st.error(f"flag · {flag}")
    with st.expander("Raw result"):
        st.json(payload)


# --- sidebar ---------------------------------------------------------------
st.sidebar.title("Code CAT")
st.sidebar.markdown(f"**Approach {settings.code_cat_approach}**")
st.sidebar.caption(APPROACH_LABEL[settings.code_cat_approach])
st.sidebar.caption(f"model `{settings.litellm_model}`")
if not settings.e2b_api_key:
    st.sidebar.error("E2B_API_KEY is not set — no code can be executed.")

mode = st.sidebar.radio("Mode", ["Assessment", "Inspector"])
run = session()
st.sidebar.markdown("---")
st.sidebar.markdown("**Learner model**")
render_state(run["model"])
if st.sidebar.button("Reset session"):
    del st.session_state["run"]
    st.rerun()

# --- assessment ------------------------------------------------------------
if mode == "Assessment":
    st.header("Adaptive code assessment")
    questions = bank()
    answered_ids = {a["question_id"] for a in run["answered"]}
    elapsed = (time.time() - run["started"]) / 60.0

    eligible = filter_candidates(questions, answered_ids, run["model"])
    stop = evaluate_stop(run["model"], len(run["answered"]), elapsed, len(eligible))

    if stop.should_stop:
        st.success(f"Assessment complete — {stop.reason}"
                   f"{' (target precision reached)' if stop.converged else ''}")
        if not stop.converged:
            st.warning(
                f"Stopped on **{stop.reason}**, not on measured precision. The competency "
                "estimates below are the best available, not the target quality."
            )
        st.dataframe(run["audit"], use_container_width=True, hide_index=True)
        st.stop()

    if run["current"] is None:
        with st.spinner("Selecting the next question…"):
            decision = choose(rank_candidates(eligible, run["model"]), run["model"])
        run["current"] = decision

    decision = run["current"]
    question = decision.question
    st.subheader(f"{question['question_id']} · {question['title']}")
    st.caption(
        f"Question {len(run['answered']) + 1} · difficulty {question['difficulty']:.2f} · "
        f"targets {', '.join(c['competency_id'] for c in question['competencies'])}"
    )
    with st.expander("Why this question?"):
        st.write(f"**{decision.reason_code}** — {decision.reason or decision.shortlist_ids}")
        st.caption(
            f"rank {decision.rank} of {len(decision.shortlist_ids)} · utility "
            f"{decision.utility:.3f} of {decision.best_utility:.3f} best · regret "
            f"{decision.normalized_regret:.1%} · "
            + ("chosen by the model" if decision.chosen_by_llm else "engine default")
        )
        for flag in decision.flags:
            st.warning(flag)

    st.markdown(question["prompt"])
    code = st.text_area(
        "Your solution",
        value=f"def {question['function_name']}():\n    ...",
        height=280,
        key=f"code_{question['question_id']}",
    )

    if st.button("Submit", type="primary"):
        before = run["model"].snapshot()
        with st.spinner("Running your code in the sandbox…"):
            result = evaluate_submission(question, code)
        apply_to_learner(run["model"], result)
        after = run["model"].snapshot()
        run["answered"].append({"question_id": question["question_id"]})
        run["audit"].append(
            audit_record(run["id"], len(run["answered"]), result, before, after, decision)
        )
        run["current"] = None
        st.session_state["last_result"] = result
        st.rerun()

    if "last_result" in st.session_state:
        st.markdown("---")
        st.subheader("Previous submission")
        render_evaluation(st.session_state["last_result"])

# --- inspector -------------------------------------------------------------
else:
    st.header("Pipeline inspector")
    st.caption(
        "Run a seeded submission with known defects and watch every layer. Ground truth "
        "is declared in the bank and verified against actual execution, so what the "
        "pipeline concludes can be checked rather than trusted."
    )

    seeded = sorted(SEEDED_DIR.glob("*.json"))
    if not seeded:
        st.error("No seeded submissions found in bank/seeded.")
        st.stop()

    choice = st.selectbox("Seeded submission", [p.stem for p in seeded])
    entry = json.loads((SEEDED_DIR / f"{choice}.json").read_text())
    question = next(q for q in bank() if q["question_id"] == entry["question_id"])
    truth = entry["ground_truth"]

    left, right = st.columns([3, 2])
    with left:
        st.code(entry["code"], language="python")
    with right:
        st.markdown(f"**Seeded defect:** `{entry['defect']}`")
        st.markdown("**Ground truth**")
        st.json(truth, expanded=True)

    if st.button("Run through the pipeline", type="primary"):
        with st.spinner("Executing…"):
            result = evaluate_submission(question, entry["code"])

        failed = {o.test_id for o in result.execution.test_results if not o.passed}
        expected = set(truth["expected_failing_test_ids"])
        if failed == expected:
            st.success(f"Execution matches ground truth exactly: failed {sorted(failed) or 'nothing'}")
        else:
            st.error(
                f"Execution disagrees with ground truth — failed {sorted(failed)}, "
                f"expected {sorted(expected)}. The BANK is wrong, not the approach."
            )

        scores = {e.competency_id: e.score for e in result.competency_evidence}
        weak = [scores[c] for c in truth["weak_competencies"] if c in scores]
        strong = [scores[c] for c in truth["strong_competencies"] if c in scores]
        if weak and strong:
            margin = sum(strong) / len(strong) - sum(weak) / len(weak)
            (st.success if margin > 0 else st.error)(
                f"Separation margin {margin:+.3f} — seeded-weak competencies scored "
                f"{'below' if margin > 0 else 'AT OR ABOVE'} seeded-strong ones"
            )

        render_evaluation(result)
