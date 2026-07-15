"""Streamlit MCQ adaptive testing harness for the Masaar IRT/CAT engine."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st

from bank_synth import synthesize_bank
from certainty import certainty_label, combined_certainty_pct, posterior_certainty_pct
from engine import (
    CONFIDENCE_TARGET,
    GRID,
    MAX_QUESTIONS,
    MIN_QUESTIONS,
    SE_TARGET,
    STABILITY_FLOOR,
    STABLE_WINDOW,
    Convergence,
    calibrated_prior_sd,
    check_convergence,
    fisher_info,
    level_and_band,
    p_correct,
    prior_from_level,
    resolve_a,
    resolve_b,
    resolve_c,
)
from engine_log import get_logger, log_selection, log_session_start, log_synthesis, log_update
from llm_client import (
    get_model,
    get_usage,
    llm_configured,
    model_pricing,
    provider_label,
    test_connection,
)
from llm_math import (
    DEVIATION_REJECT,
    DEVIATION_WARN,
    llm_math_update,
    posterior_from_theta_se,
)
from selection_pipeline import SelectionResult, select_next_item
from tracing import trace_final, trace_selection, trace_session_start, trace_update

MIN_ITEMS_PER_COMPETENCY = 8
BANK_EXHAUSTION_THRESHOLD = 4
# Fixed: the CAT bank is the only bank. See render_llm_gate/screen_setup for why an
# uploaded bank is not accepted.
DEFAULT_BANK = Path(__file__).parent / "enriched_bank_cat.json"
# This branch: LLM math, coded selection — one LLM call per answered question.
LLM_CALLS_PER_QUESTION = 1


def enrich_question(raw: dict) -> dict:
    """Attach IRT parameters a, b, c from bank metadata (supports extended difficulty ladder)."""
    q = dict(raw)
    q["b"] = resolve_b(q)
    q["a"] = resolve_a(q)
    q["c"] = resolve_c(q)
    return q


def validate_bank(questions: list[dict]) -> tuple[dict[str, list[dict]], list[str]]:
    """Group by competency and return validation warnings."""
    warnings: list[str] = []
    by_comp: dict[str, list[dict]] = defaultdict(list)

    for i, q in enumerate(questions):
        required = {"id", "competency", "stem", "options", "answer_index"}
        missing = required - set(q.keys())
        if missing:
            warnings.append(f"Question #{i + 1} ({q.get('id', '?')}) missing fields: {sorted(missing)}")
            continue
        if not isinstance(q["options"], list) or len(q["options"]) < 2:
            warnings.append(f"Question {q['id']} must have at least 2 options.")
            continue
        ai = q["answer_index"]
        if not isinstance(ai, int) or ai < 0 or ai >= len(q["options"]):
            warnings.append(f"Question {q['id']} has invalid answer_index.")
            continue
        by_comp[q["competency"]].append(enrich_question(q))

    for comp, items in sorted(by_comp.items()):
        if len(items) < MIN_ITEMS_PER_COMPETENCY:
            warnings.append(
                f"**{comp}** has only {len(items)} items (recommend ≥{MIN_ITEMS_PER_COMPETENCY})."
            )
        diffs = {item.get("difficulty", "medium") for item in items}
        if not {"easy", "medium", "hard"}.issubset(diffs):
            missing_diffs = {"easy", "medium", "hard"} - diffs
            warnings.append(
                f"**{comp}** is missing difficulty level(s): {sorted(missing_diffs)}. "
                "A bank without full difficulty variety degrades accuracy for candidates far from the average."
            )
        extremes = {"very_easy", "very_hard"}
        if not extremes & diffs and len(items) >= MIN_ITEMS_PER_COMPETENCY:
            warnings.append(
                f"**{comp}** has no very_easy/very_hard items — high/low ability candidates "
                "may converge slowly (Fisher information collapses away from the bank's b-range)."
            )

    return dict(by_comp), warnings


def certainty_for_state(state: dict, self_confidence: str) -> float:
    return combined_certainty_pct(
        state["se"],
        self_confidence,
        state["q_count"],
        state.get("prior_sd", 2.0),
        se_start=state.get("se_start"),
    )


def llm_enabled() -> bool:
    """Persistent flag — must NOT use a widget key that disappears off Setup screen."""
    return bool(st.session_state.get("llm_enabled", False))


def set_llm_enabled(value: bool) -> None:
    st.session_state["llm_enabled"] = bool(value)


def _apply_selection(state: dict, sel: SelectionResult | None, competency: str = "") -> None:
    if sel is None:
        state.update({"current_item": None, "done": True, "bank_exhausted": True})
        return
    state["current_item"] = sel.item
    state["current_fisher_i"] = sel.info_score
    state["last_selection"] = {
        "id": sel.item["id"],
        "criterion": sel.criterion,
        "info_score": sel.info_score,
        "fisher_i": sel.fisher_i,
        "llm_used": sel.llm_used,
        "procedure_steps": sel.procedure_steps,
        "adaptation_note": sel.adaptation_note,
        "rule_applied": sel.rule_applied,
        "shortlist_ids": sel.shortlist_ids,
    }
    if competency:
        log_selection(
            competency,
            state["q_count"],
            sel.item,
            sel.fisher_i,
            state["theta_hat"],
            mode="LLM" if sel.llm_used else "ENGINE",
            criterion=sel.criterion,
        )
        trace_selection(
            competency,
            state["q_count"],
            state["theta_hat"],
            state["se"],
            sel,
        )


def _pick_next(
    state: dict,
    pool: list[dict],
    competency: str,
    *,
    use_llm: bool = True,
) -> SelectionResult | None:
    return select_next_item(
        state["theta_hat"],
        state["se"],
        state["q_count"],
        competency,
        pool,
        state["served_ids"],
        state.get("history", []),
        state.get("posterior"),
        use_llm=use_llm,
    )


def init_competency_state(
    rating: int,
    confidence: str,
    pool: list[dict],
    competency: str = "",
    *,
    use_llm: bool | None = None,
    defer_selection: bool = False,
) -> dict:
    """Build initial posterior; optionally defer first-item selection (lazy)."""
    sd = calibrated_prior_sd(rating, confidence)
    posterior = prior_from_level(rating, sd)
    theta_hat = float(np.sum(GRID * posterior))
    se = float(np.sqrt(np.sum((GRID - theta_hat) ** 2 * posterior)))
    served_ids: list[str] = []
    base = {
        "posterior": posterior,
        "theta_hat": theta_hat,
        "se": se,
        "se_start": se,
        "prior_sd": sd,
        "self_confidence": confidence,
        "q_count": 0,
        "served_ids": served_ids,
        "history": [],
        "level_history": [],
        "stop_reason": "",
        "converged": False,
        "current_item": None,
        "current_fisher_i": 0.0,
        "bank_exhausted": False,
        "done": False,
        "level": None,
        "pct": None,
        "band": None,
        "low_confidence": None,
        "certainty_pct": combined_certainty_pct(se, confidence, 0, sd, se_start=se),
        "last_selection": None,
        "needs_selection": False,
    }

    if len(pool) < BANK_EXHAUSTION_THRESHOLD:
        base.update({"bank_exhausted": True, "done": True, "current_item": None})
        return base

    if defer_selection:
        base["needs_selection"] = True
        return base

    sel = _pick_next(base, pool, competency, use_llm=False)
    _apply_selection(base, sel, competency)
    base["bank_exhausted"] = base["current_item"] is None
    base["done"] = base["current_item"] is None
    return base


def ensure_current_item(state: dict, pool: list[dict], competency: str) -> None:
    """Lazy-select the next item when entering a competency (avoids N LLM calls at start)."""
    if state.get("done"):
        return
    if state.get("current_item") is not None:
        state["needs_selection"] = False
        return
    if not state.get("needs_selection"):
        # Nothing pending
        return

    with st.spinner("Selecting next question with coded CAT logic…"):
        sel = _pick_next(state, pool, competency, use_llm=False)
    _apply_selection(state, sel, competency)
    state["needs_selection"] = False
    if state["current_item"] is None:
        finalize_competency(state, bank_exhausted=True, competency=competency)


def unserved_count(pool: list[dict], served_ids: list[str]) -> int:
    return sum(1 for q in pool if q["id"] not in served_ids)


def finalize_competency(
    state: dict,
    bank_exhausted: bool = False,
    competency: str = "",
    conv: Convergence | None = None,
) -> None:
    level, pct, band, low_conf = level_and_band(state["theta_hat"], state["se"])
    state["done"] = True
    state["bank_exhausted"] = bank_exhausted
    state["level"] = level
    state["pct"] = pct
    state["band"] = band
    state["stop_reason"] = conv.reason if conv else ("bank_exhausted" if bank_exhausted else "")
    state["converged"] = bool(conv and conv.converged)
    # low_confidence tracks the *estimate*, not the exit route. A stability stop is a
    # legitimate exit that can still land above SE_TARGET, and the report must say so
    # rather than let "converged" imply a precision it never reached.
    state["low_confidence"] = low_conf or not state["converged"]
    state["certainty_pct"] = certainty_for_state(state, state.get("self_confidence", "low"))
    state["current_item"] = None
    state["current_fisher_i"] = 0.0
    if competency:
        trace_final(competency, state, bank_exhausted=bank_exhausted)


def should_stop(state: dict, pool: list[dict]) -> Convergence:
    """Apply the three convergence rules, then bank exhaustion.

    Exhaustion is checked last and only when no measurement rule fired: a competency
    that reached the confidence target on its final available item converged, and
    should not be reported as having run out of questions.
    """
    conv = check_convergence(state["certainty_pct"], state["level_history"], state["q_count"])
    if conv.stop:
        return conv
    if unserved_count(pool, state["served_ids"]) < BANK_EXHAUSTION_THRESHOLD:
        return Convergence(True, "bank_exhausted", False)
    return Convergence(False)


def grade_and_advance(
    state: dict,
    pool: list[dict],
    selected_index: int,
    competency: str = "",
    *,
    use_llm: bool | None = None,
) -> None:
    item = state["current_item"]
    if item is None:
        return

    is_correct = selected_index == item["answer_index"]
    if use_llm is None:
        use_llm = llm_enabled()

    math_result = llm_math_update(state, item, is_correct, use_llm=use_llm)
    theta_hat = math_result.theta_hat
    se = math_result.se
    # The exact grid posterior, not a Gaussian rebuilt from (theta, se). Round-tripping
    # the belief through two moments every step discarded the posterior's shape — 3PL
    # posteriors are skewed near the guessing floor — and left the E[Fisher] selection
    # integral running over a Gaussian fiction. The LLM still owns theta_hat.
    posterior = math_result.posterior
    if posterior is None:
        posterior = posterior_from_theta_se(theta_hat, se)
    state["posterior"] = posterior
    state["theta_hat"] = theta_hat
    state["se"] = se
    state["q_count"] += 1
    state["served_ids"].append(item["id"])
    certainty = math_result.certainty_pct
    state["certainty_pct"] = certainty
    level_now = level_and_band(theta_hat, se)[0]
    state["level_history"].append(level_now)
    state["history"].append(
        {
            "id": item["id"],
            "difficulty": item.get("difficulty", "medium"),
            "correct": is_correct,
            "theta_hat": theta_hat,
            "se": se,
            "certainty_pct": certainty,
            "level": level_now,
            "fisher_i": state.get("current_fisher_i", 0.0),
            "math_actor": "coded" if math_result.fallback_used else "llm",
            "math_fallback": math_result.fallback_used,
            "math_note": math_result.math_note,
            "coded_theta": math_result.coded_theta,
            "theta_deviation": math_result.theta_deviation,
            "se_deviation": math_result.se_deviation,
            "invariant_violation": math_result.invariant_violation,
        }
    )
    # Session-level tally: whether the LLM can do IRT maths is the question this branch
    # exists to answer, and it is only visible in aggregate.
    # Every step the model produced a θ̂ for, INCLUDING rejected ones. `not fallback_used`
    # reads like "only count real LLM steps", but a rejection sets fallback_used, so this
    # dropped exactly the largest deviations: the displayed mean could not exceed
    # DEVIATION_REJECT, and a model doing no arithmetic at all displayed a mean of ~0.27
    # against its true ~0.47 -- under the panel's own "correct" cut, so the fraud detector
    # read green on fraud in 76% of degenerate sessions. theta_deviation is None when no
    # usable θ̂ came back, which is the real "nothing to compare" case.
    if math_result.theta_deviation is not None:
        st.session_state.setdefault("math_devs", []).append(math_result.theta_deviation)
    if math_result.invariant_violation:
        st.session_state["math_violations"] = st.session_state.get("math_violations", 0) + 1
    # Counted apart from invariant violations: a direction violation proves the update is
    # wrong, a magnitude rejection judges that the model is not running the procedure.
    # One counter for both would report a number that is not what its label says.
    if math_result.deviation_rejected:
        st.session_state["math_dev_rejects"] = st.session_state.get("math_dev_rejects", 0) + 1
    st.session_state["math_steps"] = st.session_state.get("math_steps", 0) + 1
    if math_result.fallback_used:
        st.session_state["math_fallbacks"] = st.session_state.get("math_fallbacks", 0) + 1

    conv = should_stop(state, pool)
    stop, bank_exhausted = conv.stop, conv.reason == "bank_exhausted"
    log_update(
        competency,
        state["q_count"],
        item["id"],
        is_correct,
        theta_hat,
        se,
        certainty,
        conv.converged,
    )
    trace_update(
        competency,
        state["q_count"],
        item,
        correct=is_correct,
        theta_hat=theta_hat,
        se=se,
        certainty_pct=certainty,
        converged=conv.converged,
    )

    if stop:
        finalize_competency(
            state, bank_exhausted=bank_exhausted, competency=competency, conv=conv
        )
        return

    sel = _pick_next(state, pool, competency, use_llm=False)
    if sel is None:
        finalize_competency(state, bank_exhausted=True, competency=competency)
    else:
        _apply_selection(state, sel, competency)


def posterior_chart_df(posterior: np.ndarray) -> pd.DataFrame:
    return pd.DataFrame({"θ": GRID, "P(θ | data)": posterior})


def history_df(history: list[dict]) -> pd.DataFrame:
    if not history:
        return pd.DataFrame(
            columns=["id", "difficulty", "result", "Fisher I", "θ̂ after", "SE after", "Certainty %"]
        )
    rows = []
    for h in history:
        rows.append(
            {
                "id": h["id"],
                "difficulty": h["difficulty"],
                "result": "✓" if h["correct"] else "✗",
                "Fisher I": round(h.get("fisher_i", 0.0), 4),
                "θ̂ after": round(h["theta_hat"], 3),
                "SE after": round(h["se"], 3),
                "Certainty %": round(h.get("certainty_pct", 0.0), 1),
            }
        )
    return pd.DataFrame(rows)


def run_simulation(
    pool: list[dict],
    true_theta: float,
    rating: int = 3,
    confidence: str = "high",
    competency: str = "simulation",
) -> dict:
    """Run a synthetic candidate through the adaptive loop."""
    state = init_competency_state(rating, confidence, pool, competency=competency, use_llm=False)
    rng = np.random.default_rng()

    while not state["done"]:
        item = state["current_item"]
        if item is None:
            break
        prob = float(p_correct(true_theta, item["a"], item["b"], item["c"]))
        is_correct = bool(rng.random() < prob)
        selected = item["answer_index"] if is_correct else (item["answer_index"] + 1) % len(item["options"])
        grade_and_advance(state, pool, selected, competency=competency, use_llm=False)

    if not state["done"]:
        finalize_competency(
            state,
            bank_exhausted=state.get("bank_exhausted", False),
            competency=competency,
        )

    return state


def reset_session() -> None:
    for key in list(st.session_state.keys()):
        del st.session_state[key]


def render_certainty_gauge(certainty_pct: float, se: float, label_prefix: str = "") -> None:
    """Visual combined assessment certainty."""
    label = certainty_label(certainty_pct)
    st.metric(
        f"{label_prefix}Assessment certainty".strip(),
        f"{certainty_pct:.1f}%",
        delta=f"{label} · SE={se:.3f}",
        delta_color="normal" if certainty_pct >= 65 else "inverse",
    )
    st.progress(certainty_pct / 100.0, text=f"Confidence in θ̂ estimate — {label}")


def render_math_audit(state: dict) -> None:
    """LLM maths vs the coded EAP — the measurement this branch exists to produce.

    The LLM is never shown the coded result, so this is a real comparison rather than a
    check that it copied a field. Shown live because a maths engine that drifts is not
    visible in the ability estimate alone: θ̂ still looks like a plausible number.
    """
    devs = st.session_state.get("math_devs", [])
    violations = st.session_state.get("math_violations", 0)
    dev_rejects = st.session_state.get("math_dev_rejects", 0)
    steps = st.session_state.get("math_steps", 0)
    fallbacks = st.session_state.get("math_fallbacks", 0)
    if not (devs or violations or dev_rejects or steps):
        return

    st.sidebar.markdown("**LLM math audit** (vs coded EAP)")

    # First, because it qualifies every number below it: if the model never ran, this
    # panel is describing the engine. The early return used to hide exactly that.
    if steps:
        rate = fallbacks / steps
        if fallbacks == steps:
            st.sidebar.error(
                f"⚠ Every math step fell back to coded EAP ({fallbacks}/{steps}). "
                "This session measures the engine, not the LLM."
            )
        elif rate > 0.2:
            st.sidebar.warning(f"{steps - fallbacks}/{steps} math steps ran on the model "
                               f"— {rate:.0%} fell back to coded EAP.")
        else:
            st.sidebar.caption(f"LLM ran {steps - fallbacks}/{steps} math steps.")

    c1, c2 = st.sidebar.columns(2)
    if devs:
        mean_dev = sum(devs) / len(devs)
        # The session mean is the detector no single-step gate can be: measured in path,
        # correct maths averages ~0.14 and a model that has stopped doing the arithmetic
        # averages ~0.46, while their per-step distributions overlap all the way to 1.9.
        c1.metric(
            "mean |Δθ̂|", f"{mean_dev:.3f}",
            delta="correct ≈0.14" if mean_dev < 0.30 else "degenerate ≈0.46?",
            delta_color="normal" if mean_dev < 0.30 else "inverse",
            help=("Average gap between the LLM's θ̂ and the coded EAP this session. Drift "
                  "is expected — the prompt specifies a Newton update taken from the LLM's "
                  "own θ̂, so it diverges from the coded EAP cumulatively. Measured in "
                  "path: correct ≈0.14, a model faking the update ≈0.46."),
        )
        # Deliberately no verdict. A single worst step carries almost no signal here:
        # correct maths reaches 1.9, so any threshold that calls one step "suspect" would
        # be calling ~1 correct session in 8 suspect. Read the mean.
        c2.metric(
            "worst |Δθ̂|", f"{max(devs):.3f}",
            help=("The single largest gap this session. Shown for context, not as a "
                  "verdict: a correct implementation reaches 1.9 on rare steps, so no "
                  "per-step threshold separates it from a model doing nothing."),
        )
    if violations:
        st.sidebar.error(
            f"{violations} invariant violation(s): the LLM moved θ̂ the wrong way for a "
            "graded response. Those updates were rejected and the coded EAP used instead."
        )
    if dev_rejects:
        st.sidebar.warning(
            f"{dev_rejects} update(s) rejected for deviating more than {DEVIATION_REJECT} "
            "from the coded EAP; the coded EAP was used instead. This is a damage floor, "
            "not a verdict — correct maths trips it in ~12% of sessions because θ̂ drift "
            "compounds. Read the mean above."
        )


def render_sidebar_live(state: dict, title: str) -> None:
    st.sidebar.subheader(title)
    certainty = state.get(
        "certainty_pct",
        posterior_certainty_pct(state["se"], se_start=state.get("se_start")),
    )
    render_certainty_gauge(certainty, state["se"])
    c1, c2 = st.sidebar.columns(2)
    c1.metric("θ̂ (ability)", f"{state['theta_hat']:.3f}")
    c2.metric("SE (uncertainty)", f"{state['se']:.3f}")
    render_math_audit(state)
    st.sidebar.caption(
        f"Stops when: certainty ≥ {CONFIDENCE_TARGET:.0%} · "
        f"or same level {STABLE_WINDOW}× in a row "
        f"(after {MIN_QUESTIONS} questions, certainty ≥ {STABILITY_FLOOR:.0%}) · "
        f"or {MAX_QUESTIONS} questions · "
        f"selection: engine only (KL→Fisher) · "
        f"math: {'OpenAI' if llm_enabled() else 'coded fallback'}"
    )

    item = state.get("current_item")
    if item and not state.get("done"):
        fi = state.get("current_fisher_i") or fisher_info(state["theta_hat"], item)
        sel_meta = state.get("last_selection") or {}
        st.sidebar.caption(
            f"Next **{item['id']}** ({item.get('difficulty', '?')}) · "
            f"{sel_meta.get('criterion', 'Fisher')} score = **{fi:.4f}**"
        )
        if sel_meta.get("adaptation_note"):
            with st.sidebar.expander("LLM adaptation note", expanded=False):
                st.write(sel_meta["adaptation_note"])
                if sel_meta.get("procedure_steps"):
                    for step in sel_meta["procedure_steps"]:
                        st.caption(step)
                if sel_meta.get("shortlist_ids"):
                    st.caption(f"Shortlist: {', '.join(sel_meta['shortlist_ids'])}")

    st.sidebar.line_chart(posterior_chart_df(state["posterior"]).set_index("θ"))
    st.sidebar.markdown("**Question log**")
    st.sidebar.dataframe(history_df(state["history"]), width="stretch", hide_index=True)

    with st.sidebar.expander("Engine monitor (live log tail)", expanded=False):
        log_path = Path(__file__).parent / "assessment.log"
        if log_path.exists():
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()[-12:]
            st.code("\n".join(lines) if lines else "(no events yet)", language="text")
        else:
            st.caption("Log file will appear after first answer.")


def render_debug_simulate(bank_by_comp: dict[str, list[dict]]) -> None:
    with st.sidebar.expander("Debug / Simulate", expanded=False):
        st.caption("Run a synthetic candidate with a known true θ through the same engine.")
        sim_enabled = st.checkbox("Enable simulation", key="sim_enabled")
        if not sim_enabled:
            return

        comp = st.selectbox("Competency", list(bank_by_comp.keys()), key="sim_comp")
        true_theta = st.slider("True θ", -4.0, 4.0, 0.0, 0.1, key="sim_theta")
        sim_rating = st.slider("Self-rating (prior)", 1, 5, 3, key="sim_rating")
        sim_conf = st.radio(
            "Self-rating confidence",
            ["low", "high"],
            horizontal=True,
            key="sim_conf",
        )

        if st.button("Run simulation", key="sim_run"):
            pool = bank_by_comp[comp]
            sim_state = run_simulation(pool, true_theta, sim_rating, sim_conf, competency=comp)
            st.session_state["sim_result"] = sim_state
            st.session_state["sim_comp_name"] = comp
            st.session_state["sim_true_theta"] = true_theta

        if "sim_result" in st.session_state:
            sim = st.session_state["sim_result"]
            true_t = st.session_state.get("sim_true_theta", 0.0)
            level, pct, band, low_conf = level_and_band(sim["theta_hat"], sim["se"])
            certainty = sim.get("certainty_pct", 0)
            st.markdown(f"**Simulated — {st.session_state.get('sim_comp_name', '')}**")
            st.write(
                f"True θ: {true_t:.2f} → Estimated θ̂: {sim['theta_hat']:.3f} "
                f"(SE {sim['se']:.3f}, certainty {certainty:.1f}%)"
            )
            st.write(f"Level {level} ({pct}%) — {band}")
            if low_conf:
                st.warning("Low confidence")
            if sim.get("bank_exhausted"):
                st.warning("Bank exhausted")
            st.line_chart(posterior_chart_df(sim["posterior"]).set_index("θ"))
            st.dataframe(history_df(sim["history"]), width="stretch", hide_index=True)


def render_llm_gate() -> None:
    """Require a working LLM before the assessment can start.

    There is no "use the LLM" toggle any more, and no engine-only mode to fall back
    to. Each branch exists to measure what happens when the LLM owns part of the CAT;
    a run that silently used coded selection instead produces a number that looks like
    a result and answers a different question. So the LLM is mandatory and its
    availability is proven up front rather than discovered mid-assessment.
    """
    st.subheader("LLM controller (required)")
    st.caption(
        f"Provider: `{provider_label()}` · model: `{get_model()}` · "
        "MCQ grading always stays deterministic (exact index match)."
    )

    if "llm_probe" not in st.session_state:
        st.session_state["llm_probe"] = None

    if not llm_configured():
        st.error(
            "No `OPENAI_API_KEY` found. This app requires a working LLM — there is no "
            "engine-only mode. Copy `.env.example` → `.env` and add your key, or set it "
            "in Streamlit Cloud app secrets."
        )
        set_llm_enabled(False)
        st.stop()

    # Probe once per session, automatically. Previously this was a button the user had
    # to remember to press, and forgetting it silently left the run in engine-only mode.
    if st.session_state["llm_probe"] is None:
        with st.spinner("Verifying LLM connection…"):
            st.session_state["llm_probe"] = test_connection()

    ok, msg = st.session_state["llm_probe"]
    if not ok:
        st.error(f"LLM connection failed — cannot start.\n\n{msg}")
        if st.button("Retry connection", key="llm_retry_btn"):
            st.session_state["llm_probe"] = None
            st.rerun()
        set_llm_enabled(False)
        st.stop()

    st.success(msg)
    set_llm_enabled(True)


def render_cost_panel() -> None:
    """What this run has actually cost, metered from the API's own token counts."""
    usage = get_usage()
    price = model_pricing()
    with st.expander("LLM usage & cost", expanded=False):
        if price is None:
            st.warning(
                f"No pricing on file for `{get_model()}` — add it to "
                "`MODEL_PRICING_USD_PER_1M` in `llm_client.py` to see cost."
            )
        else:
            st.caption(
                f"`{get_model()}` · ${price['input']:.2f} per 1M input tokens · "
                f"${price['output']:.2f} per 1M output tokens"
            )
        c1, c2, c3 = st.columns(3)
        c1.metric("LLM calls", usage.calls)
        c2.metric("Tokens", f"{usage.input_tokens + usage.output_tokens:,}",
                  help=f"{usage.input_tokens:,} in · {usage.output_tokens:,} out")
        cost = usage.cost_usd()
        c3.metric("Cost so far", "—" if cost is None else f"${cost:.4f}")
        st.caption(
            f"Metered from each response's `usage`, not estimated. Expected volume: "
            f"{LLM_CALLS_PER_QUESTION} call(s) per question × up to {MAX_QUESTIONS} "
            f"questions × competencies selected."
        )


def screen_setup() -> None:
    st.header("Setup")
    st.markdown(
        "Rate your proficiency per competency, then start the adaptive assessment."
    )

    # The bank is fixed, not uploaded. The three approach branches only produce a
    # comparable measurement if they run identical items, and an arbitrary uploaded
    # bank may carry no numeric IRT parameters at all — in which case the engine falls
    # back to label maps, collapses b onto 5 discrete values, and the CAT degrades
    # without saying so.
    if not DEFAULT_BANK.exists():
        st.error(
            f"Required bank `{DEFAULT_BANK.name}` is missing. Rebuild it with "
            "`python build_enriched_bank.py bank_items enriched_bank_cat.json`."
        )
        st.stop()

    try:
        questions = json.loads(DEFAULT_BANK.read_text(encoding="utf-8"))
        if not isinstance(questions, list):
            raise ValueError("Root JSON must be an array of questions.")
    except (json.JSONDecodeError, ValueError) as exc:
        st.error(f"`{DEFAULT_BANK.name}` is invalid: {exc}")
        st.stop()

    bank_by_comp, warnings = validate_bank(questions)
    if not bank_by_comp:
        st.error("No valid questions found in the bank.")
        return

    raw_counts = {c: len(v) for c, v in bank_by_comp.items()}
    bank_by_comp, synth_logs = synthesize_bank(bank_by_comp, enrich_question)
    if synth_logs:
        log_synthesis(synth_logs)
        st.session_state["synth_logs"] = synth_logs

    st.session_state["bank_by_comp"] = bank_by_comp
    st.session_state["raw_bank_counts"] = raw_counts
    competencies = sorted(bank_by_comp.keys())

    synth_added = sum(len(bank_by_comp[c]) for c in competencies) - sum(raw_counts.values())
    if synth_added > 0:
        st.success(
            f"Filled **{synth_added}** gap items for difficulty coverage — see expander below."
        )
        with st.expander("Bank synthesis log"):
            for line in synth_logs:
                st.text(line)
            for comp in competencies:
                c = Counter(q.get("difficulty", "medium") for q in bank_by_comp[comp])
                st.caption(
                    f"{comp}: {len(bank_by_comp[comp])} total "
                    f"(ve={c.get('very_easy',0)}, e={c.get('easy',0)}, m={c.get('medium',0)}, "
                    f"h={c.get('hard',0)}, vh={c.get('very_hard',0)})"
                )
    elif synth_logs:
        with st.expander("Bank coverage check"):
            for line in synth_logs:
                st.text(line)
            for comp in competencies:
                c = Counter(q.get("difficulty", "medium") for q in bank_by_comp[comp])
                st.caption(
                    f"{comp}: {len(bank_by_comp[comp])} total "
                    f"(ve={c.get('very_easy',0)}, e={c.get('easy',0)}, m={c.get('medium',0)}, "
                    f"h={c.get('hard',0)}, vh={c.get('very_hard',0)})"
                )

    if warnings:
        st.warning("Bank validation warnings:")
        for w in warnings:
            st.markdown(f"- {w}")

    render_llm_gate()
    render_cost_panel()

    if "selected_competencies_widget" not in st.session_state:
        st.session_state["selected_competencies_widget"] = competencies.copy()
    else:
        valid_selected = [
            comp for comp in st.session_state["selected_competencies_widget"]
            if comp in competencies
        ]
        st.session_state["selected_competencies_widget"] = valid_selected or competencies.copy()

    st.subheader("Competency scope")
    selected_competencies = st.multiselect(
        "Choose competencies to test",
        options=competencies,
        key="selected_competencies_widget",
        help="Select one competency for a focused test, or combine multiple competencies.",
    )
    if selected_competencies:
        st.caption(
            f"Assessment will include **{len(selected_competencies)}** of "
            f"**{len(competencies)}** competencies."
        )
    else:
        st.warning("Select at least one competency before starting the assessment.")

    st.subheader("Self-rating intake")
    if "self_ratings" not in st.session_state:
        st.session_state["self_ratings"] = {}
    if "self_confidences" not in st.session_state:
        st.session_state["self_confidences"] = {}

    for comp in selected_competencies:
        count = len(bank_by_comp[comp])
        st.markdown(f"**{comp}** ({count} questions)")
        st.session_state["self_ratings"][comp] = st.slider(
            f"Rate your proficiency — {comp}",
            1,
            5,
            st.session_state["self_ratings"].get(comp, 3),
            key=f"rating_{comp}",
        )
        st.session_state["self_confidences"][comp] = st.radio(
            f"How confident are you in this self-rating? — {comp}",
            options=["low", "high"],
            format_func=lambda x: "Low" if x == "low" else "High",
            horizontal=True,
            index=0 if st.session_state["self_confidences"].get(comp, "low") == "low" else 1,
            key=f"conf_{comp}",
        )

    if st.button("Start Assessment", type="primary", disabled=not selected_competencies):
        get_logger().info("--- new session ---")
        use_llm = llm_enabled()
        get_logger().info(
            "START | llm_enabled=%s | competencies=%s",
            use_llm,
            selected_competencies,
        )
        comp_states = {}
        for comp in selected_competencies:
            # Defer item selection for all competencies — pick lazily when each starts.
            # Avoids N blocking OpenAI calls at "Start Assessment".
            comp_states[comp] = init_competency_state(
                st.session_state["self_ratings"][comp],
                st.session_state["self_confidences"][comp],
                bank_by_comp[comp],
                competency=comp,
                use_llm=use_llm,
                defer_selection=True,
            )
            if comp_states[comp]["done"] and comp_states[comp]["bank_exhausted"]:
                finalize_competency(comp_states[comp], bank_exhausted=True, competency=comp)
        log_session_start(
            selected_competencies,
            {c: len(bank_by_comp[c]) for c in selected_competencies},
        )
        trace_session_start(
            selected_competencies,
            {c: len(bank_by_comp[c]) for c in selected_competencies},
            llm_enabled=use_llm,
        )
        st.session_state["comp_states"] = comp_states
        st.session_state["competencies"] = selected_competencies
        st.session_state["comp_idx"] = 0
        st.session_state["phase"] = "assessment"
        st.rerun()


def current_competency() -> str | None:
    competencies = st.session_state.get("competencies", [])
    idx = st.session_state.get("comp_idx", 0)
    if idx >= len(competencies):
        return None
    return competencies[idx]


def advance_to_next_competency() -> None:
    st.session_state["comp_idx"] += 1
    idx = st.session_state["comp_idx"]
    competencies = st.session_state["competencies"]
    if idx >= len(competencies):
        st.session_state["phase"] = "report"
    st.rerun()


def screen_assessment() -> None:
    comp = current_competency()
    if comp is None:
        st.session_state["phase"] = "report"
        st.rerun()
        return

    bank_by_comp = st.session_state["bank_by_comp"]
    comp_states = st.session_state["comp_states"]
    state = comp_states[comp]
    pool = bank_by_comp[comp]
    competencies = st.session_state["competencies"]
    comp_idx = st.session_state["comp_idx"]

    render_sidebar_live(state, f"Live — {comp}")
    render_debug_simulate(bank_by_comp)

    # Lazy LLM/engine selection for this competency
    if not state.get("done") and (
        state.get("needs_selection") or state.get("current_item") is None
    ):
        ensure_current_item(state, pool, comp)
        if state.get("done"):
            st.rerun()

    st.header("Adaptive Assessment")
    st.progress((comp_idx) / len(competencies), text=f"Competency {comp_idx + 1} of {len(competencies)}")

    if state["done"]:
        st.success(f"**{comp}** complete!")
        if state.get("bank_exhausted"):
            st.warning("Stopped early — question bank nearly exhausted (< 4 items remaining).")
        level, pct, band, low_conf = (
            state["level"],
            state["pct"],
            state["band"],
            state["low_confidence"],
        )
        st.write(f"Result: Level **{level}** ({pct}%) — {band}")
        render_certainty_gauge(
            state.get("certainty_pct", 0),
            state["se"],
            label_prefix="Final ",
        )
        if low_conf:
            st.warning("Low confidence — estimate may be unreliable.")
        if st.button("Continue to next competency", type="primary", key="next_comp"):
            advance_to_next_competency()
        return

    item = state["current_item"]
    q_num = state["q_count"] + 1
    st.subheader(comp)
    st.caption(
        f"Question {q_num} of up to {MAX_QUESTIONS} · "
        f"info score = {state.get('current_fisher_i', 0):.4f} · "
        f"certainty {state.get('certainty_pct', 0):.1f}%"
    )

    sel_meta = state.get("last_selection")
    if sel_meta and sel_meta.get("llm_used"):
        with st.expander("Why this question? (LLM procedural selection)", expanded=False):
            st.markdown(f"**Criterion:** {sel_meta.get('criterion', '?')}")
            st.markdown(f"**Rule:** {sel_meta.get('rule_applied', '—')}")
            if sel_meta.get("adaptation_note"):
                st.info(sel_meta["adaptation_note"])
            for step in sel_meta.get("procedure_steps", []):
                st.caption(step)

    st.markdown(f"**{item['stem']}**")
    if item.get("sub_competency"):
        st.caption(item["sub_competency"])

    options = item["options"]
    selected = st.radio(
        "Choose an answer",
        range(len(options)),
        format_func=lambda i: options[i],
        key=f"answer_{comp}_{item['id']}_{state['q_count']}",
    )

    if st.button("Submit answer", type="primary"):
        grade_and_advance(state, pool, selected, competency=comp)
        st.rerun()


def stop_reason_label(state: dict) -> str:
    """Human-readable exit route, for a report that should not hide why it ended."""
    return Convergence(True, state.get("stop_reason", "")).label or "—"


def screen_report() -> None:
    st.header("Final Report")
    comp_states = st.session_state["comp_states"]
    competencies = st.session_state["competencies"]

    rows = []
    for comp in competencies:
        s = comp_states[comp]
        rows.append(
            {
                "Competency": comp,
                "Level": s["level"],
                "%": s["pct"],
                "Band": s["band"],
                "θ̂": round(s["theta_hat"], 3),
                "SE": round(s["se"], 3),
                "Certainty %": round(s.get("certainty_pct", 0), 1),
                "Questions": s["q_count"],
                "Stopped because": stop_reason_label(s),
                "Low confidence": s["low_confidence"],
                "Bank exhausted": s.get("bank_exhausted", False),
            }
        )

    df = pd.DataFrame(rows)
    st.dataframe(
        df.drop(columns=["Low confidence"]),
        width="stretch",
        hide_index=True,
    )

    st.subheader("Per-competency details")
    for comp in competencies:
        s = comp_states[comp]
        with st.container(border=True):
            cols = st.columns([3, 1])
            with cols[0]:
                st.markdown(f"### {comp}")
                st.write(f"**Level {s['level']}** · {s['pct']}% · **{s['band']}**")
                cert = s.get("certainty_pct", 0)
                st.caption(
                    f"θ̂ = {s['theta_hat']:.3f}, SE = {s['se']:.3f}, "
                    f"certainty = {cert:.1f}% ({certainty_label(cert)}), "
                    f"{s['q_count']} questions answered · "
                    f"stopped because {stop_reason_label(s)}"
                )
                st.progress(cert / 100.0, text=f"Assessment certainty — {certainty_label(cert)}")
                if s.get("stop_reason") == "stable_level":
                    # A settled band is not a precise estimate. Saying so here keeps the
                    # shorter test from reading as a more certain one.
                    st.caption(
                        f"The level repeated {STABLE_WINDOW}× in a row, which ended the "
                        f"competency early. That is a stable *band*, not a precise θ̂ — "
                        f"read the certainty above for precision."
                    )
            with cols[1]:
                if s["low_confidence"]:
                    st.error("⚠ Low confidence")
                else:
                    st.success("✓ Confident estimate")
                if s.get("bank_exhausted"):
                    st.warning("Bank exhausted")

    overall_pct = int(round(np.mean([s["pct"] for s in comp_states.values()])))
    st.metric("Overall score (mean of competency %)", f"{overall_pct}%")

    if st.button("Run again", type="primary"):
        reset_session()
        st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="Masaar MCQ CAT Test Harness",
        page_icon="📊",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    st.title("Masaar MCQ Adaptive Test Harness")
    st.caption(
        "IRT/CAT + LLM procedural selection · MCQ grading deterministic · OpenAI"
    )

    if "phase" not in st.session_state:
        st.session_state["phase"] = "setup"

    phase = st.session_state["phase"]

    if phase == "setup":
        if "bank_by_comp" in st.session_state:
            render_debug_simulate(st.session_state["bank_by_comp"])
        screen_setup()
    elif phase == "assessment":
        screen_assessment()
    elif phase == "report":
        render_debug_simulate(st.session_state.get("bank_by_comp", {}))
        screen_report()


if __name__ == "__main__":
    main()
