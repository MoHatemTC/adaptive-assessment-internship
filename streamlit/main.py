"""Tester harness for the orchestrated multi-modality adaptive assessment.

Built for people who need to CHECK the engine, not for candidates. Every number a decision
rested on is on screen: what is queued and why it was shortlisted, which information
criterion is in force, what each update did to the posterior, and what the engine logged
while doing it.

Three screens:

    Setup        pick the competencies to be assessed, self-rate each, and say how sure
                 that rating is. The rating seeds the prior; the confidence sets its width.
    Assessment   answer, and watch the queue, the mathematics and the trajectory move.
    Report       on convergence — the same detail, retained rather than cleared, because
                 the interesting part of a session is usually visible only afterwards.

Reads the engine and never reaches into it: everything shown is either returned by the
public API or recomputed from it with the engine's own functions.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

# The backend is a sibling package, not an installed one. Prepended so a tester can run
# `streamlit run streamlit/main.py` from the repository root with no packaging step.
#
# This file is main.py and not app.py deliberately: Streamlit puts the script's own
# directory on sys.path, so an `app.py` here would shadow the backend's `app` PACKAGE and
# every `from app.services...` import would fail with "app is not a package".
HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
# `streamlit run` puts the script's own directory on sys.path; nothing else does, so a test
# runner or a different working directory would fail to import `session_log`. Both are
# added explicitly so the app does not depend on how it was launched.
for path in (str(BACKEND), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

import session_log  # noqa: E402  — must be importable before the engine logs anything

from app.config.settings import settings  # noqa: E402
from app.services.adaptive.irt import ability_band  # noqa: E402
from app.services.code_adaptive import (  # noqa: E402
    CodeAdaptiveSession,
    JsonQuestionRepository,
)
from app.services.orchestrator import GraderAgent, JsonUnifiedBank, Orchestrator  # noqa: E402
from app.services.orchestrator import variables as variables_module  # noqa: E402
from app.services.adaptive.irt import expected_fisher_information  # noqa: E402
from app.services.orchestrator.picker import criterion_for, information_for  # noqa: E402


def fisher_after(item, variable_state, variable) -> float:
    """Fisher information the item carries at the updated estimate.

    Always Fisher, never the phase's criterion: `information_for` returns KL during the
    KL phase, so reporting it in a column headed FI would label one quantity with another
    quantity's name for the first three answers of every competency.
    """
    posterior = np.asarray(variable_state.posterior, dtype=float)
    return float(
        expected_fisher_information(posterior, item.cat.a, item.cat.b, item.cat.c)
    ) * item.loading(variable)

st.set_page_config(page_title="Adaptive assessment — tester", layout="wide")
session_log.install()

_ST_VERSION = tuple(int(p) for p in st.__version__.split(".")[:2] if p.isdigit())
WIDE = {"width": "stretch"} if _ST_VERSION >= (1, 49) else {"use_container_width": True}

CRITERION_EXPLAINED = {
    "KL": "Kullback-Leibler information. Used for the first 3 observations, while the "
          "estimate is still vague: Fisher information is local — it asks which item is "
          "most informative exactly here, when 'here' is barely known. KL integrates over "
          "a neighbourhood, so it prefers items that separate a whole region of ability.",
    "E[Fisher]": "Expected Fisher information, averaged over the posterior rather than "
                 "taken at its mean. Used once the estimate is worth localising around. "
                 "Information at a point estimate is only the right criterion if the point "
                 "estimate is right.",
}


# --- engine wiring ----------------------------------------------------------
@st.cache_resource
def engine() -> Orchestrator:
    return Orchestrator(
        JsonUnifiedBank(), GraderAgent(CodeAdaptiveSession(JsonQuestionRepository()))
    )


@st.cache_resource
def bank() -> JsonUnifiedBank:
    return JsonUnifiedBank()


def run_async(coro):
    """Streamlit is synchronous; the orchestrator's queue fill is not."""
    return asyncio.run(coro)


def use_llm() -> bool:
    """Whether the picking agent may call the model.

    Off automatically without a gateway key: otherwise every pick would time out, fall back
    to the engine's choice, and produce a session that looks deterministic for a reason the
    tester cannot see.
    """
    return bool(st.session_state.get("use_llm")) and bool(settings.litellm_api_key)


# --- setup screen -----------------------------------------------------------
def render_setup() -> None:
    st.title("Adaptive assessment")
    st.caption(
        "Choose what to be assessed on, rate yourself, and say how sure that rating is. "
        "The rating seeds the starting estimate; the confidence sets how easily evidence "
        "overrides it. Neither is ever reported as a measurement."
    )

    coverage = bank().coverage()
    if not coverage:
        st.error("The question bank is empty.")
        return

    def describe(variable: str) -> str:
        modalities = coverage[variable]
        detail = ", ".join(f"{n} {m}" for m, n in sorted(modalities.items()))
        return f"{variable} — {detail}"

    mixed = [v for v, m in coverage.items() if len(m) > 1]
    chosen = st.multiselect(
        "Competencies to be assessed",
        options=sorted(coverage),
        default=mixed[:2] or sorted(coverage)[:2],
        format_func=describe,
        help="Competencies carrying both modalities let the engine choose between an MCQ "
             "item and a coding question on information alone — which is the behaviour "
             "most worth testing.",
    )
    if not chosen:
        st.info("Select at least one competency.")
        return

    st.markdown("#### Self-rating")
    intake: dict[str, int] = {}
    confident: dict[str, bool] = {}
    for variable in chosen:
        left, middle, right = st.columns([2, 3, 2])
        left.markdown(f"**{variable}**")
        left.caption(", ".join(f"{n} {m}" for m, n in sorted(coverage[variable].items())))
        intake[variable] = middle.slider(
            "Your level", 1, 5, 3, key=f"level_{variable}",
            help="1 novice to 5 expert. Mapped onto the ability scale as a starting point.",
        )
        confident[variable] = right.radio(
            "How sure?", ["High", "Low"], index=1, key=f"conf_{variable}",
            help="High narrows the starting estimate; low leaves it wide. Either way a "
                 "few answers override it.",
        ) == "High"

    st.markdown("---")
    left, right = st.columns(2)
    left.checkbox(
        "Let the picking agent use the model", value=bool(settings.litellm_api_key),
        key="use_llm",
        help="Off runs the engine's own deterministic choice, which is also the fallback "
             "whenever the model is unavailable or returns something unusable.",
        disabled=not settings.litellm_api_key,
    )
    if not settings.litellm_api_key:
        right.caption("No LITELLM_API_KEY configured — picking runs deterministically.")
    if not settings.e2b_api_key:
        st.warning(
            "No E2B_API_KEY configured. Coding questions can still be selected and shown, "
            "but running one will report a sandbox failure — which correctly moves no "
            "estimate, and is itself worth testing."
        )

    # A single rating confidence for the session: the engine takes one flag, and pretending
    # otherwise in the UI would imply a per-variable control that does not exist.
    if st.button("Begin assessment", type="primary"):
        state = engine().begin(
            chosen, intake=intake, rating_confident=any(confident.values())
        )
        session_log.clear()
        st.session_state.update(
            run=state, steps=[], rng_seed=0, finished=False, stop_reason="", pending=None
        )
        st.rerun()


# --- shared panels ----------------------------------------------------------
def render_queue(state) -> None:
    """What is waiting, why it was shortlisted, and why the picker took it."""
    if not state.queue:
        st.caption("The queue is empty.")
        return

    rows = []
    for variable, candidate in sorted(state.queue.items()):
        item = bank().get(candidate.item_id)
        rows.append(
            {
                "competency": variable,
                "item": candidate.item_id,
                "modality": candidate.modality,
                "criterion": candidate.criterion,
                "information": round(candidate.information, 5),
                "best available": round(candidate.best_information, 5),
                "regret": f"{candidate.normalized_regret:.1%}",
                "engine's pick": candidate.engine_top_pick,
                "picked by": "model" if candidate.chosen_by_llm else "engine",
                "reason": candidate.reason_code,
                "a": item.cat.a if item else None,
                "b": item.cat.b if item else None,
                "c": item.cat.c if item else None,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, **WIDE)
    st.caption(
        "One slot per open competency. `regret` is the fraction of the best available "
        "information given up — 0 means the engine's own top pick was taken. A pick below "
        f"{settings.orchestrator_minimum_relative_utility:.0%} of the best is overridden."
    )

    for variable, candidate in sorted(state.queue.items()):
        with st.expander(f"Why these were shortlisted for {variable} — {candidate.criterion}"):
            st.caption(CRITERION_EXPLAINED.get(candidate.criterion, ""))
            variable_state = state.variables[variable]
            shortlist_rows = []
            for rank, item_id in enumerate(candidate.shortlist_ids, start=1):
                item = bank().get(item_id)
                if item is None:
                    continue
                shortlist_rows.append(
                    {
                        "rank": rank,
                        "item": item_id,
                        "modality": item.modality,
                        "information": round(
                            information_for(item, variable_state, variable), 5
                        ),
                        "difficulty b": item.cat.b,
                        "discrimination a": item.cat.a,
                        "guessing c": item.cat.c,
                        "loading on competency": item.loading(variable),
                        "administered": "◀" if item_id == candidate.item_id else "",
                    }
                )
            st.dataframe(pd.DataFrame(shortlist_rows), hide_index=True, **WIDE)
            st.caption(
                f"Ranked by {candidate.criterion} at ability "
                f"{variable_state.theta_hat:+.3f}, scaled by how much each item measures "
                "this competency. Information peaks where the outcome is genuinely "
                "uncertain, so an item far above or below the estimate ranks low whatever "
                "its difficulty."
            )
            if candidate.reason:
                st.markdown(f"**Picker's reason** — {candidate.reason}")


def steps_frame() -> pd.DataFrame:
    return pd.DataFrame(st.session_state.get("steps", []))


def render_mathematics() -> None:
    """Every update, in the order it happened."""
    frame = steps_frame()
    if frame.empty:
        st.caption("No questions answered yet.")
        return
    st.dataframe(frame, hide_index=True, **WIDE)
    st.caption(
        "One row per competency updated by each answer — a coding question updates several, "
        "because one submission genuinely evidences several. `weight` is how much of an "
        "observation the answer was worth: 1.0 for multiple choice, less for code that did "
        "not compile, and 0 for an infrastructure failure, which moves nothing at all. "
        "`FI after` is the Fisher information the administered item carried at the updated "
        "estimate — how much that item was still worth once the answer was known."
    )
    st.download_button(
        "Download this table (CSV)",
        frame.to_csv(index=False).encode(),
        file_name=f"{st.session_state['run'].session_id}_updates.csv",
        mime="text/csv",
    )


def render_trajectory(state) -> None:
    frame = steps_frame()
    if frame.empty:
        st.caption("No questions answered yet.")
        return

    left, right = st.columns(2)
    with left:
        st.markdown("**Ability by competency**")
        pivot = frame.pivot_table(
            index="step", columns="competency", values="ability after", aggfunc="last"
        ).ffill()
        st.line_chart(pivot)
        st.caption("Ability on the −4 to +4 scale. Every competency is on the same scale "
                   "whichever modality measured it — that is what makes them comparable.")
    with right:
        st.markdown("**Confidence by competency**")
        pivot = frame.pivot_table(
            index="step", columns="competency", values="confidence after", aggfunc="last"
        ).ffill()
        st.line_chart(pivot)
        st.caption(
            "Confidence rises only as evidence arrives. A competency finalises when it "
            f"reaches the precision target (SE ≤ {settings.cat_se_target}, which reads as "
            "90%) or its band stops moving."
        )

    st.markdown("**Where each competency stands**")
    rows = []
    for variable, variable_state in sorted(state.variables.items()):
        level, band = ability_band(variable_state.theta_hat)
        measured = variable_state.observations > 0
        rows.append(
            {
                "competency": variable,
                "ability": round(variable_state.theta_hat, 4),
                "std error": round(variable_state.standard_error, 4),
                "confidence": f"{variables_module.certainty(variable_state):.1f}%",
                "level": str(level) if measured else "—",
                "band": band if measured else "Not assessed",
                "answers": variable_state.observations,
                "criterion now": criterion_for(variable_state.observations),
                "status": (
                    f"finalised · {variable_state.stop_reason}"
                    if variable_state.finalised else "open"
                ),
                "converged": variable_state.converged,
            }
        )
    st.dataframe(pd.DataFrame(rows), hide_index=True, **WIDE)
    st.caption(
        "`level` and `band` stay blank until something has actually been observed: the "
        "starting estimate is a prior, not a measurement, and showing a band for it would "
        "read as one. `converged` is true only for a stop earned on precision or a settled "
        "band — running out of items is a budget outcome, not a measurement."
    )


def render_logs() -> None:
    tally = session_log.counts()
    st.caption(" · ".join(f"{k} {v}" for k, v in sorted(tally.items())) or "No events yet.")
    records = session_log.records()
    if not records:
        st.caption(
            "Nothing logged yet. Fallbacks, finalisations and model failures appear here."
        )
        return
    for record in reversed(records[-80:]):
        line = f"`{record['time']}` **{record['source']}** — {record['message']}"
        if record["level"] in ("WARNING", "ERROR", "CRITICAL"):
            st.warning(line)
        else:
            st.caption(line)


def render_diagnostics() -> None:
    """Everything a tester needs to judge whether the engine is behaving."""
    st.markdown("**Configuration in force**")
    st.dataframe(
        pd.DataFrame(
            [
                {"setting": "precision target (SE)", "value": str(settings.cat_se_target),
                 "meaning": "a competency finalises at or below this standard error"},
                {"setting": "min / max questions", "value": f"{settings.cat_min_questions} / {settings.cat_max_questions}",
                 "meaning": "per-competency bounds before the band-stability rule may fire"},
                {"setting": "shortlist size", "value": str(settings.orchestrator_shortlist_size),
                 "meaning": "items offered to the picking agent per competency"},
                {"setting": "minimum relative utility",
                 "value": str(settings.orchestrator_minimum_relative_utility),
                 "meaning": "below this fraction of the best information, the model's pick "
                            "is overridden"},
                {"setting": "whole-assessment cap", "value": str(settings.orchestrator_max_items),
                 "meaning": "items across every competency before the session stops"},
                {"setting": "code scoring split", "value": str(settings.code_approach),
                 "meaning": "tests own functional correctness; the model judges quality"},
                {"setting": "picking agent", "value": "model" if use_llm() else "engine only",
                 "meaning": "the engine's deterministic choice is always the fallback"},
            ]
        ),
        hide_index=True, **WIDE,
    )

    st.markdown("**Bank coverage and information parity**")
    st.caption(
        "A modality can lose a ranking for two reasons and only one is a fault. "
        "`rarely_selected` means it loses once loading is applied — expected for a coding "
        "question that only lightly measures a competency. `miscalibrated` means it loses "
        "even at full loading, which is a problem with the item parameters themselves. "
        "Coding items carry authored parameters, not ones fitted to real responses."
    )
    parity = bank().parity_report()
    if parity:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "competency": entry["variable"],
                        "modalities": ", ".join(entry["modalities"]),
                        "relative (with loading)": entry["relative_effective"],
                        "relative (at full loading)": entry["relative_intrinsic"],
                        "rarely selected": ", ".join(entry["rarely_selected"]) or "—",
                        "miscalibrated": ", ".join(entry["miscalibrated"]) or "—",
                    }
                    for entry in parity
                ]
            ),
            hide_index=True, **WIDE,
        )

    with st.expander("Raw session state"):
        st.json(st.session_state["run"].model_dump(), expanded=False)


# --- answering --------------------------------------------------------------
def record(state, item, candidate, response) -> None:
    """Grade one response and record everything the tables need."""
    before = {v: s for v, s in state.variables.items()}
    new_state, graded = engine().record_response(state, item, response)

    step = len(st.session_state["steps"]) and max(r["step"] for r in st.session_state["steps"])
    step = step + 1

    detail = graded.detail or {}
    score_shown = (
        detail.get("overall_score")
        if graded.modality == "code"
        else (1.0 if detail.get("correct") else 0.0)
    )
    outcome_by_variable = {o["variable"]: o for o in graded.outcomes}

    for variable, after in sorted(new_state.variables.items()):
        outcome = outcome_by_variable.get(variable)
        if outcome is None:
            continue
        was = before[variable]
        level, band = ability_band(after.theta_hat)
        st.session_state["steps"].append(
            {
                "step": step,
                "competency": variable,
                "item": item.item_id,
                "modality": item.modality,
                "criterion": candidate.criterion if candidate else "—",
                "score": None if score_shown is None else round(float(score_shown), 4),
                "outcome score": round(float(outcome["score"]), 4),
                "weight": round(float(outcome["weight"]), 4),
                "ability before": round(was.theta_hat, 4),
                "ability after": round(after.theta_hat, 4),
                "Δ ability": round(after.theta_hat - was.theta_hat, 4),
                "std error before": round(was.standard_error, 4),
                "std error after": round(after.standard_error, 4),
                "confidence after": round(variables_module.certainty(after), 2),
                "level after": level if after.observations else None,
                "band after": band if after.observations else "Not assessed",
                "FI after": round(fisher_after(item, after, variable), 5),
                "answers": after.observations,
                "finalised": after.finalised,
            }
        )

    st.session_state["run"] = new_state
    st.session_state["pending"] = None
    st.session_state["last_graded"] = {
        "item": item.item_id,
        "modality": graded.modality,
        "score": score_shown,
        "detail": detail,
        "flags": graded.flags,
    }


def render_question(state) -> None:
    """Present whatever the orchestrator chose, and collect an answer."""
    nxt = engine().next_item(state)
    if nxt is None:
        return
    item, candidate = nxt
    variable_state = state.variables[candidate.variable]

    st.markdown(f"### {candidate.variable} · {item.modality.upper()}")
    st.caption(
        f"Chosen because this competency has the lowest confidence "
        f"({variables_module.certainty(variable_state):.1f}%). Item selected by "
        f"{'the model' if candidate.chosen_by_llm else 'the engine'} using "
        f"{candidate.criterion}."
    )

    payload = item.payload
    if item.modality == "mcq":
        st.markdown(payload.get("stem", ""))
        options = payload.get("options", [])
        choice = st.radio("Your answer", options, index=None, key=f"mcq_{item.item_id}")
        if st.button("Submit answer", type="primary", disabled=choice is None):
            record(state, item, candidate, options.index(choice))
            st.rerun()
    else:
        st.markdown(payload.get("prompt", ""))
        starter = payload.get("starter_code") or f"def {payload.get('function_name', 'solve')}():\n    ...\n"
        code = st.text_area("Your solution", value=starter, height=260, key=f"code_{item.item_id}")
        st.caption(
            "Run in a sandbox against the question's tests. Test results own functional "
            "correctness; the model only judges quality."
        )
        if st.button("Submit solution", type="primary"):
            with st.spinner("Running your code in the sandbox…"):
                record(state, item, candidate, code)
            st.rerun()


def render_last_result() -> None:
    last = st.session_state.get("last_graded")
    if not last:
        return
    with st.expander(f"Result of the previous answer — {last['item']}", expanded=True):
        score = last["score"]
        st.metric("Score", "—" if score is None else f"{float(score):.3f}")
        if last["modality"] == "code":
            detail = last["detail"]
            columns = st.columns(4)
            columns[0].metric(
                "Tests", f"{detail.get('passed_tests', 0)}/{detail.get('total_tests', 0)}"
            )
            columns[1].metric("Compiled", str(detail.get("compiled")))
            columns[2].metric("Model used", str(detail.get("llm_used")))
            columns[3].metric("Weights", detail.get("weight_fingerprint", "—"))
            if detail.get("criterion_scores"):
                st.dataframe(
                    pd.DataFrame(
                        [{"criterion": k, "score": v}
                         for k, v in detail["criterion_scores"].items()]
                    ),
                    hide_index=True, **WIDE,
                )
            if detail.get("misconception_codes"):
                st.warning("Misconceptions: " + ", ".join(detail["misconception_codes"]))
            if detail.get("diagnostic"):
                st.caption(detail["diagnostic"])
        else:
            st.caption(
                f"Chose option {last['detail'].get('chosen_index')}, "
                f"correct was {last['detail'].get('answer_index')}."
            )
        for flag in last["flags"]:
            st.error(flag)


# --- assessment screen ------------------------------------------------------
def render_assessment() -> None:
    state = st.session_state["run"]

    if not st.session_state["finished"]:
        seed = st.session_state["rng_seed"]
        st.session_state["rng_seed"] = seed + 1
        state = run_async(
            engine().fill_queue(
                state, use_llm=use_llm(), rng=np.random.default_rng(seed)
            )
        )
        st.session_state["run"] = state

        stop, reason = engine().should_stop(state)
        if stop:
            st.session_state.update(finished=True, stop_reason=reason)

    state = st.session_state["run"]
    finished = st.session_state["finished"]

    # --- sidebar
    st.sidebar.title("Session")
    st.sidebar.caption(f"`{state.session_id}`")
    st.sidebar.metric("Questions answered", state.items_administered)
    open_count = len(state.open_variables)
    st.sidebar.metric("Competencies open", f"{open_count} of {len(state.variables)}")
    for variable, variable_state in sorted(state.variables.items()):
        certainty = variables_module.certainty(variable_state)
        st.sidebar.progress(
            min(certainty / 100.0, 1.0),
            text=f"{variable} — {certainty:.0f}%"
                 + (" ✓" if variable_state.finalised else ""),
        )
    st.sidebar.markdown("---")
    if st.sidebar.button("End session"):
        st.session_state.update(finished=True, stop_reason="ended_by_tester")
        st.rerun()
    if st.sidebar.button("Start over"):
        for key in ("run", "steps", "finished", "stop_reason", "pending", "last_graded"):
            st.session_state.pop(key, None)
        st.rerun()

    # --- main
    if finished:
        report = engine().summarise(state, st.session_state["stop_reason"])
        st.title("Assessment complete")
        st.success(
            f"Stopped: **{report.stop_reason}** after {report.items_administered} questions. "
            + ("Every competency finalised." if report.all_finalised
               else "Some competencies did not reach the precision target.")
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "competency": v.variable,
                        "level": str(v.level) if v.observations else "—",
                        "band": v.band + ("" if v.converged or not v.observations
                                          else "  (provisional)"),
                        "ability": v.theta_hat,
                        "std error": v.standard_error,
                        "confidence": f"{v.certainty_pct:.1f}%",
                        "answers": v.observations,
                        "modalities": ", ".join(v.modalities_used) or "—",
                        "finalised": v.finalised,
                        "converged": v.converged,
                        "why it stopped": v.stop_reason or "—",
                    }
                    for v in report.variables
                ]
            ),
            hide_index=True, **WIDE,
        )
        st.caption(
            "`converged` separates a measurement from a budget outcome: it is true only "
            "when precision or band stability was reached. A band marked *provisional* "
            "comes from a competency that ran out of questions before reaching the "
            "precision target — the level is the best estimate available, not a measured "
            "result, and the confidence column says how far short it fell."
        )
    else:
        render_last_result()
        render_question(state)

    st.markdown("---")
    tabs = st.tabs(
        ["Queue", "Mathematics", "Trajectory", "Engine log", "Diagnostics"]
    )
    with tabs[0]:
        render_queue(state)
    with tabs[1]:
        render_mathematics()
    with tabs[2]:
        render_trajectory(state)
    with tabs[3]:
        render_logs()
    with tabs[4]:
        render_diagnostics()


# --- entry ------------------------------------------------------------------
if "run" not in st.session_state:
    render_setup()
else:
    render_assessment()
