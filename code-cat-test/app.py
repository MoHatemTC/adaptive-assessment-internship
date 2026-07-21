"""Streamlit harness for the code-question adaptive assessment.

Three screens:

  Setup       choose the competency to be assessed on, and optionally self-rate. The
              choice scopes the whole session: which questions are eligible, which
              competency the utility function aims at, and which one the stopping rule
              judges precision on.
  Assessment  take the test, with the CAT state visible at every step — mastery, standard
              error, evidence count, the utility and regret of the selection, and how far
              the stopping rule has to go.
  Inspector   run any seeded submission through the pipeline and see every layer.

The engine log is surfaced rather than left on stderr. A session that quietly ran entirely
on deterministic fallbacks looks identical to one that ran on the model unless the
rejections are visible, and that difference is the whole point of the study.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pandas as pd
import streamlit as st

from cat import session_log, trace as trace_mod
from cat.competency_state import LearnerModel
from cat.config import settings
from cat.pipeline import (
    apply_to_learner,
    audit_record,
    evaluate_submission,
    load_bank,
    result_to_dict,
)
from cat.selection import (
    choose,
    competency_weight,
    evaluate_stop,
    filter_candidates,
    rank_candidates,
)

SEEDED_DIR = Path(__file__).resolve().parent / "bank" / "seeded"

APPROACH_LABEL = {
    "A": "A · objective only — tests + static analysis, no LLM scoring",
    "B": "B · test-anchored LLM — model interprets, tests anchor correctness",
    "C": "C · LLM-led — model scores every criterion, hard contradictions blocked",
}

# Below this many eligible questions, an adaptive test cannot really adapt: there is
# nothing to choose between. Warned about rather than blocked, since a short run is still
# useful for inspecting the pipeline.
THIN_POOL = 4

st.set_page_config(page_title="Code CAT", layout="wide")
session_log.install()


@st.cache_data
def bank() -> list[dict]:
    return load_bank()


def competency_catalogue() -> list[tuple[str, int, float]]:
    """(competency, how many questions assess it, the best weight any of them gives it)."""
    rows = []
    for competency in sorted({c["competency_id"] for q in bank() for c in q["competencies"]}):
        weights = [competency_weight(q, competency) for q in bank()]
        assessing = [w for w in weights if w > 0]
        rows.append((competency, len(assessing), max(assessing)))
    return sorted(rows, key=lambda r: (-r[1], r[0]))


def run() -> dict | None:
    return st.session_state.get("run")


def start_run(competency: str, self_rating: int | None) -> None:
    model = LearnerModel()
    if self_rating is not None:
        # A self-rating is weak evidence and seeds the prior only. Beta(1,1) plus a
        # fractional pseudo-observation: enough to bias the first question's difficulty,
        # far too little to survive contrary evidence, and `observed` stays False so the
        # UI never reports a self-rating as a measurement.
        state = model.get(competency)
        strength = 0.8
        target = (self_rating - 1) / 4.0
        state.alpha += strength * target
        state.beta += strength * (1.0 - target)
        state.last_updated_by = "self_rating"

    session_log.clear()
    st.session_state["run"] = {
        "id": f"session_{uuid.uuid4().hex[:8]}",
        "competency": competency,
        "self_rating": self_rating,
        "model": model,
        "answered": [],
        "audit": [],
        "trajectory": [],
        "traces": [],
        "started": time.time(),
        "current": None,
    }
    st.session_state.pop("last_result", None)


def record_trajectory(state: dict, decision, result) -> None:
    """One row per answered question: the CAT parameters as they stood after it."""
    target = state["model"].get(state["competency"])
    state["trajectory"].append(
        {
            "q": len(state["answered"]),
            "question": result.question_id,
            "tests": f"{result.execution.passed_tests}/{result.execution.total_tests}",
            "overall": None if result.overall_score is None else round(result.overall_score, 3),
            "mastery": round(target.mastery, 4),
            "std_error": round(target.standard_error, 4),
            "evidence": target.evidence_count,
            "rank": decision.rank if decision else None,
            "utility": round(decision.utility, 4) if decision else None,
            "regret": round(decision.normalized_regret, 4) if decision else None,
            "by_llm": decision.chosen_by_llm if decision else None,
            "llm_scored": result.llm.available,
        }
    )


# --- sidebar ---------------------------------------------------------------
st.sidebar.title("Code CAT")
st.sidebar.markdown(f"**Approach {settings.code_cat_approach}** · rubric `{settings.code_cat_rubric}`")
st.sidebar.caption(APPROACH_LABEL[settings.code_cat_approach])
st.sidebar.caption(f"model `{settings.litellm_model}`")
if not settings.e2b_api_key:
    st.sidebar.error("E2B_API_KEY is not set — no code can be executed.")

mode = st.sidebar.radio("Mode", ["Assessment", "Inspector"])
active = run()

if active:
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Session** `{active['id']}`")
    st.sidebar.caption(f"assessing **{active['competency']}**")
    target = active["model"].get(active["competency"])
    c1, c2 = st.sidebar.columns(2)
    c1.metric("Mastery", f"{target.mastery:.3f}" if target.observed else "—")
    c2.metric("Std error", f"{target.standard_error:.3f}")
    st.sidebar.progress(
        min(settings.target_standard_error / max(target.standard_error, 1e-6), 1.0),
        text=f"precision target SE ≤ {settings.target_standard_error}",
    )
    if st.sidebar.button("End session"):
        st.session_state.pop("run", None)
        st.rerun()

with st.sidebar.expander(f"Engine log ({sum(session_log.counts().values())})"):
    tally = session_log.counts()
    if tally:
        st.caption(" · ".join(f"{k} {v}" for k, v in sorted(tally.items())))
    records = session_log.records()
    if not records:
        st.caption("No engine events yet.")
    else:
        for record in records[-25:]:
            line = f"`{record['time']}` **{record['source']}** — {record['message']}"
            if record["level"] in ("WARNING", "ERROR"):
                st.warning(line)
            else:
                st.caption(line)


def render_learner_model(model: LearnerModel, target: str) -> None:
    snapshot = model.snapshot()
    if not snapshot:
        st.caption("No competency evidence yet.")
        return
    rows = [
        {
            "competency": ("▶ " if cid == target else "") + cid,
            "level": s["level"] if s["level"] is not None else "—",
            "band": s["band"],
            "mastery": f"{s['mastery']:.3f}" if s["observed"] else "—",
            "std error": f"{s['standard_error']:.3f}",
            "evidence": s["evidence_count"],
            "misconceptions": ", ".join(s["misconception_codes"]) or "—",
        }
        for cid, s in snapshot.items()
    ]
    st.dataframe(rows, use_container_width=True, hide_index=True)
    st.caption(
        "▶ marks the competency under test. Mastery shows — where no evidence exists yet: "
        "a Beta(1,1) prior has a mean of exactly 0.5, which would otherwise read as a "
        "measured mid-level result rather than as absence of evidence."
    )


def render_evaluation(result) -> None:
    """Every layer, in the order it was computed."""
    execution = result.execution
    c1, c2, c3, c4 = st.columns(4)
    # Every one of these reads "unknown" rather than a value when the sandbox never ran.
    # Showing "0/8", "Compiled: no" and an overall score for a submission that was never
    # executed states things about the candidate that no evidence supports.
    c1.metric("Tests passed",
              "—" if not execution.usable else f"{execution.passed_tests}/{execution.total_tests}")
    c2.metric("Compiled",
              "unknown" if execution.compiled is None else ("yes" if execution.compiled else "no"))
    c3.metric("Overall", "—" if result.overall_score is None else f"{result.overall_score:.3f}")
    c4.metric("Latency", f"{result.evaluation_latency_ms:,}ms")

    if not execution.usable:
        st.error(
            "**Sandbox unavailable — nothing about this submission was measured.** "
            "The code was never executed, so no test result, compile status or score "
            "below describes the candidate. The learner model was not updated.\n\n"
            f"`{execution.error_message[:200]}`"
        )
        with st.expander("Why you are seeing this"):
            st.write(
                "The sandbox is where untrusted code runs; if it cannot start there is no "
                "objective evidence, and this pipeline refuses to score without it. "
                "Common causes: E2B quota exhausted by concurrent runs, a network "
                "interruption, or a missing/expired `E2B_API_KEY`. Resubmit once it "
                "recovers — no state was written."
            )
        return

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
    unscored = [c.criterion_id for c in result.criterion_scores if c.score is None]
    if unscored:
        st.warning(
            f"Not assessed: **{', '.join(unscored)}**. Under approach "
            f"{result.approach} the only source configured for these is the model, and it "
            "produced nothing usable. They are left unscored and excluded from the overall "
            "and from the learner model — an unassessed criterion is not a passed one."
        )
    st.dataframe(
        [
            {
                "criterion": c.criterion_id,
                "score": "not assessed" if c.score is None else f"{c.score:.3f}",
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
        st.json(result_to_dict(result))


# --- assessment ------------------------------------------------------------
if mode == "Assessment":
    if active is None:
        st.header("Set up the assessment")
        st.caption(
            "Choose the competency to be assessed on. It scopes the session: which "
            "questions are eligible, what the selection utility aims at, and which "
            "competency the stopping rule judges precision on."
        )

        catalogue = competency_catalogue()
        labels = {
            f"{c}  ·  {n} question{'s' if n != 1 else ''} available": c
            for c, n, _ in catalogue
        }
        picked_label = st.selectbox("Competency under test", list(labels))
        picked = labels[picked_label]
        available = next(n for c, n, _ in catalogue if c == picked)

        if available < THIN_POOL:
            st.warning(
                f"Only {available} question{'s' if available != 1 else ''} in the bank "
                f"assess **{picked}**. The test can run, but with this little to choose "
                "between there is almost no adaptation to observe — the engine will "
                "administer nearly everything eligible regardless of the answers."
            )

        rated = st.checkbox("I want to self-rate first", value=False)
        rating = None
        if rated:
            rating = st.slider(
                f"How would you rate yourself on {picked}?", 1, 5, 3,
                help="Seeds the prior only. Two or three answers override it entirely, "
                     "and it is never blended into the final score.",
            )

        st.markdown(
            f"**Session settings** — stop at SE ≤ {settings.target_standard_error}, "
            f"between {settings.min_questions} and {settings.max_questions} questions, "
            f"or {settings.time_limit_minutes} minutes."
        )
        if st.button("Start assessment", type="primary"):
            start_run(picked, rating)
            st.rerun()
        st.stop()

    # --- in progress ---
    questions = bank()
    answered_ids = {a["question_id"] for a in active["answered"]}
    elapsed = (time.time() - active["started"]) / 60.0
    eligible = filter_candidates(questions, answered_ids, active["model"], active["competency"])
    stop = evaluate_stop(
        active["model"], len(active["answered"]), elapsed, len(eligible), active["competency"]
    )

    st.header(f"Assessing · {active['competency']}")

    target = active["model"].get(active["competency"])
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Questions", len(active["answered"]))
    m2.metric("Mastery", f"{target.mastery:.3f}" if target.observed else "—")
    m3.metric("Std error", f"{target.standard_error:.3f}",
              delta=f"target ≤ {settings.target_standard_error}", delta_color="off")
    m4.metric("Evidence", target.evidence_count)
    m5.metric("Eligible left", len(eligible))

    traces = active["traces"]
    if traces:
        cat_rows = trace_mod.cat_frame(traces)
        comp_rows = trace_mod.competency_frame(traces)

        tab_cat, tab_comp, tab_chart = st.tabs(
            ["CAT parameters", "Competency levels", "Trajectory"]
        )

        with tab_cat:
            st.dataframe(pd.DataFrame(cat_rows), use_container_width=True, hide_index=True)
            st.caption(
                "`ability_before/after` and `se_*` are the Beta posterior for the "
                "competency under test — `se_after` is what the stopping rule reads. "
                "`regret` is the fraction of the best available utility given up: 0 means "
                "the engine's top-ranked question was administered."
            )
            divergence = trace_mod.divergence_summary(traces)
            if divergence and divergence["overridden"]:
                st.warning(
                    f"The model overrode the engine's top pick on "
                    f"**{divergence['overridden']} of {divergence['steps']}** steps "
                    f"(mean regret {divergence['mean_regret']:.1%}, worst "
                    f"{divergence['max_regret']:.1%})."
                    + (f" Never administered despite ranking first: "
                       f"`{', '.join(divergence['never_administered'])}`."
                       if divergence["never_administered"] else "")
                )
            st.download_button(
                "Download CAT parameters (CSV)",
                pd.DataFrame(cat_rows).to_csv(index=False).encode(),
                file_name=f"{active['id']}_cat_parameters.csv",
                mime="text/csv",
            )

        with tab_comp:
            if comp_rows:
                st.dataframe(pd.DataFrame(comp_rows), use_container_width=True, hide_index=True)
                st.caption(
                    "One row per competency that MOVED at that step — a question carrying "
                    "four competencies would otherwise repeat all four every step. ◀ marks "
                    "the competency under test. `level` is the 1-5 band; it is blank until "
                    "a competency has real evidence, because a Beta(1,1) prior sits at "
                    "exactly 0.5 and would read as a measured mid-level result."
                )
                st.download_button(
                    "Download competency trace (CSV)",
                    pd.DataFrame(comp_rows).to_csv(index=False).encode(),
                    file_name=f"{active['id']}_competency_trace.csv",
                    mime="text/csv",
                )
            else:
                st.caption("No competency has moved yet.")

        with tab_chart:
            series = trace_mod.level_series(traces)
            if series and len(series[0]) > 1:
                st.markdown("**Mastery by competency**")
                st.line_chart(pd.DataFrame(series).set_index("step"))
            target_line = pd.DataFrame(
                [
                    {
                        "step": r["step"],
                        "ability": r["ability_after"],
                        "std_error": r["se_after"],
                    }
                    for r in cat_rows
                    if r["ability_after"] is not None
                ]
            )
            if not target_line.empty:
                st.markdown(f"**{active['competency']} — ability and precision**")
                st.line_chart(target_line.set_index("step"))
                st.caption(
                    f"The test stops when std_error reaches {settings.target_standard_error}. "
                    "A flat or rising std_error means the questions being administered are "
                    "not informative about this competency."
                )

    if stop.should_stop:
        st.success(f"Assessment complete — {stop.reason}"
                   f"{' (target precision reached)' if stop.converged else ''}")
        if not stop.converged:
            st.warning(
                f"Stopped on **{stop.reason}**, not on measured precision. The estimate "
                "below is the best available, not the target quality."
            )
        st.subheader("Learner model")
        render_learner_model(active["model"], active["competency"])
        st.subheader("Audit trail")
        st.dataframe(active["audit"], use_container_width=True, hide_index=True)
        st.stop()

    if active["current"] is None:
        with st.spinner("Selecting the next question…"):
            active["current"] = choose(
                rank_candidates(eligible, active["model"], active["competency"]),
                active["model"],
                target_competency=active["competency"],
            )

    decision = active["current"]
    if decision is None:
        st.warning("No eligible questions remain.")
        st.stop()

    question = decision.question
    st.subheader(f"{question['question_id']} · {question['title']}")
    st.caption(
        f"Question {len(active['answered']) + 1} · difficulty {question['difficulty']:.2f} · "
        f"targets {', '.join(c['competency_id'] for c in question['competencies'])}"
    )

    with st.expander("Why this question?"):
        st.write(f"**{decision.reason_code}** — {decision.reason or '(engine default)'}")
        s1, s2, s3 = st.columns(3)
        s1.metric("Rank", f"{decision.rank} / {len(decision.shortlist_ids)}")
        s2.metric("Utility", f"{decision.utility:.3f}",
                  delta=f"best {decision.best_utility:.3f}", delta_color="off")
        s3.metric("Regret", f"{decision.normalized_regret:.1%}")
        st.caption(
            ("Chosen by the model from the engine's shortlist."
             if decision.chosen_by_llm else "Engine default — the model was not used or "
             "its choice was rejected.")
        )
        shortlist = rank_candidates(eligible, active["model"], active["competency"])[
            : settings.shortlist_size
        ]
        st.markdown("**The shortlist it chose from**, ranked by expected information:")
        st.dataframe(
            [
                {
                    "rank": i + 1,
                    "question": c.question["question_id"],
                    "difficulty": c.question["difficulty"],
                    "discrim": c.question.get("discrimination"),
                    "info": c.signals["expected_information"],
                    "loading": c.signals["target_loading"],
                    "utility": c.utility,
                    "administered": "◀" if c.question["question_id"] == question["question_id"] else "",
                }
                for i, c in enumerate(shortlist)
            ],
            use_container_width=True,
            hide_index=True,
        )
        st.caption(
            "`info` is the IRT information this question carries at the current ability "
            "estimate — highest where the candidate could plausibly pass or fail, low for "
            "questions far above or below them. `loading` is how much of the question "
            "assesses the competency under test."
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
        before = active["model"].snapshot()
        with st.spinner("Running your code in the sandbox…"):
            result = evaluate_submission(question, code)
        apply_to_learner(active["model"], result)
        after = active["model"].snapshot()
        active["answered"].append({"question_id": question["question_id"]})
        active["audit"].append(
            audit_record(active["id"], len(active["answered"]), result, before, after, decision)
        )
        record_trajectory(active, decision, result)
        active["traces"].append(
            trace_mod.record(
                len(active["answered"]), question["question_id"], active["competency"],
                result, decision, before, after,
            )
        )
        active["current"] = None
        st.session_state["last_result"] = result
        st.rerun()

    if "last_result" in st.session_state:
        st.markdown("---")
        st.subheader("Previous submission")
        render_evaluation(st.session_state["last_result"])

    st.markdown("---")
    st.subheader("Learner model")
    render_learner_model(active["model"], active["competency"])

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
