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
    eap_update,
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
from selection_pipeline import SelectionResult, select_next_item
from tracing import trace_final, trace_selection, trace_session_start, trace_update

MIN_ITEMS_PER_COMPETENCY = 8
BANK_EXHAUSTION_THRESHOLD = 4
# Fixed: the CAT bank is the only bank. See render_llm_gate/screen_setup for why an
# uploaded bank is not accepted.
DEFAULT_BANK = Path(__file__).parent / "enriched_bank_cat.json"
# This branch: coded math, one LLM call per question to select the next item.
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


def rephrase_enabled() -> bool:
    """Adaptive stem rephrasing. OFF by default.

    It was on by default, which made the psychometrically dirty configuration the one
    every run used. `b` is calibrated for specific wording, so a rewritten stem is not
    the item the parameters describe, and rephrase_guard can only check surface fidelity
    (leaks, dropped identifiers, length) — it cannot prove difficulty was preserved.
    Opt in when studying the feature; do not measure a candidate through it by accident.
    """
    return bool(st.session_state.get("rephrase_enabled", False))


def set_rephrase_enabled(value: bool) -> None:
    st.session_state["rephrase_enabled"] = bool(value)


def _apply_selection(state: dict, sel: SelectionResult | None, competency: str = "") -> None:
    if sel is None:
        state.update({"current_item": None, "done": True, "bank_exhausted": True})
        return
    item = dict(sel.item)
    if sel.rephrased_stem and sel.rephrased_stem != sel.item.get("stem", ""):
        item["original_stem"] = sel.item.get("stem", "")
        item["display_stem"] = sel.rephrased_stem
    state["current_item"] = item
    # Two different quantities, kept apart on purpose. info_score is whatever criterion
    # ranked the shortlist — for the first 3 questions that is KL, a sum over the theta
    # grid of order 1-10. fisher_i is Fisher information at theta-hat, which for a 3PL
    # item cannot exceed ~0.6. Storing the criterion score under "fisher" made the UI
    # report "Fisher I = 8.0", which is not a possible value.
    state["current_info_score"] = sel.info_score
    state["current_fisher_i"] = sel.fisher_i
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
        "rephrased_stem": sel.rephrased_stem,
        "original_stem": sel.item.get("stem", ""),
        "rephrase_rejected_reason": sel.rephrase_rejected_reason,
        "rephrase_rejected_code": sel.rephrase_rejected_code,
        "expected_selected_id": sel.expected_selected_id,
        "procedure_followed": sel.procedure_followed,
        "procedure_deviation_reason": sel.procedure_deviation_reason,
        "fallback_used": sel.fallback_used,
        "fallback_reason": sel.fallback_reason,
    }
    st.session_state["selection_steps"] = st.session_state.get("selection_steps", 0) + 1
    if sel.llm_used:
        st.session_state["llm_selection_steps"] = st.session_state.get("llm_selection_steps", 0) + 1
    if sel.fallback_used:
        st.session_state["selection_fallbacks"] = st.session_state.get("selection_fallbacks", 0) + 1
    if not sel.procedure_followed:
        st.session_state["selection_procedure_deviations"] = (
            st.session_state.get("selection_procedure_deviations", 0) + 1
        )
    if sel.rephrase_rejected_reason:
        # Count rejections over the session so a systematically bad prompt is visible
        # rather than showing up once per question and scrolling away.
        st.session_state["rephrase_rejects"] = st.session_state.get("rephrase_rejects", 0) + 1
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
        state.get("certainty_pct"),
        use_llm=use_llm,
        allow_rephrase=rephrase_enabled(),
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
        "current_info_score": 0.0,
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

    if use_llm is None:
        use_llm = llm_enabled()

    sel = _pick_next(base, pool, competency, use_llm=use_llm)
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

    use_llm = llm_enabled()
    with st.spinner(
        "Selecting next question via OpenAI…" if use_llm else "Selecting next question…"
    ):
        sel = _pick_next(state, pool, competency, use_llm=use_llm)
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
    state["current_info_score"] = 0.0
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
    posterior, theta_hat, se = eap_update(state["posterior"], item, is_correct)
    state["posterior"] = posterior
    state["theta_hat"] = theta_hat
    state["se"] = se
    state["q_count"] += 1
    state["served_ids"].append(item["id"])
    conf = state.get("self_confidence", "low")
    certainty = combined_certainty_pct(
        se,
        conf,
        state["q_count"],
        state.get("prior_sd", 2.0),
        se_start=state.get("se_start"),
    )
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
        }
    )

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

    if use_llm is None:
        use_llm = llm_enabled()

    # Defer LLM pick until next render so spinner can show
    if use_llm:
        state["current_item"] = None
        state["needs_selection"] = True
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


def render_cat_state_row(state: dict, item: dict, sel_meta: dict, q_num: int) -> None:
    """The four numbers that explain why this question is on screen.

    Previously this was one dense caption line. The whole point of a CAT is that the
    question is a consequence of theta-hat and SE, so those belong next to the
    question rather than only in the sidebar.
    """
    criterion = sel_meta.get("criterion", "—")
    fisher_i = state.get("current_fisher_i", 0.0)
    se = state["se"]
    target_gap = se - SE_TARGET

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Question", f"{q_num} / {MAX_QUESTIONS}")
    c2.metric("θ̂ ability", f"{state['theta_hat']:+.2f}")
    c3.metric(
        "SE",
        f"{se:.2f}",
        delta=f"{target_gap:+.2f} vs target" if target_gap > 0 else "target reached",
        delta_color="inverse" if target_gap > 0 else "normal",
        help=f"Assessment stops when SE ≤ {SE_TARGET} or {MAX_QUESTIONS} questions are used.",
    )
    # Always Fisher here, never the raw criterion score: KL and Fisher are on different
    # scales, so showing "the criterion's number" would make the value jump an order of
    # magnitude at question 4 for no reason the examinee could interpret.
    c4.metric(
        "Fisher I",
        f"{fisher_i:.3f}",
        help=(
            f"Information this item carries about your ability at θ̂ (3PL, max ≈0.6 with "
            f"4 options). Ranked by **{criterion}** — KL for the first 3 questions while "
            f"θ̂ is still moving, posterior-expected Fisher afterwards."
        ),
    )
    st.progress(
        min(1.0, max(0.0, (state.get("se_start", 2.0) - se) / max(state.get("se_start", 2.0) - SE_TARGET, 1e-6))),
        text=f"Convergence toward SE ≤ {SE_TARGET} · certainty {state.get('certainty_pct', 0):.0f}%",
    )


def render_selection_rationale(state: dict, item: dict, sel_meta: dict) -> None:
    """Show the shortlist the LLM actually chose from, and what it was told."""
    st.markdown(f"**Criterion:** `{sel_meta.get('criterion', '?')}` — "
                f"**rule applied:** {sel_meta.get('rule_applied', '—')}")
    expected_id = sel_meta.get("expected_selected_id") or "—"
    if sel_meta.get("fallback_used"):
        st.warning(f"LLM fallback used: {sel_meta.get('fallback_reason', 'unknown reason')}")
    elif sel_meta.get("procedure_followed", True):
        st.success(f"Procedure audit passed. Expected and selected item: `{expected_id}`.")
    else:
        st.warning(
            "Procedure audit deviation: "
            f"{sel_meta.get('procedure_deviation_reason', 'unknown deviation')}"
        )
    if sel_meta.get("adaptation_note"):
        st.info(sel_meta["adaptation_note"])
    for step in sel_meta.get("procedure_steps", []):
        st.caption(step)

    # The LLM picks from an engine-scored shortlist; showing it makes clear the LLM is
    # executing the CAT procedure, not free-picking questions.
    shortlist_ids = sel_meta.get("shortlist_ids") or []
    if shortlist_ids:
        pool_by_id = {q["id"]: q for q in st.session_state["bank_by_comp"].get(
            item.get("competency", ""), [])}
        rows = []
        for sid in shortlist_ids:
            q = pool_by_id.get(sid)
            if not q:
                continue
            rows.append({
                "id": sid,
                "chosen": "selected" if sid == item["id"] else "",
                "expected": "expected" if sid == expected_id else "",
                "difficulty": q.get("difficulty", ""),
                "a": round(q.get("a", 0), 2),
                "b": round(q.get("b", 0), 2),
                "|b−θ̂|": round(abs(q.get("b", 0) - state["theta_hat"]), 2),
                "Fisher I": round(fisher_info(state["theta_hat"], q), 4),
            })
        if rows:
            st.caption(f"Engine-scored shortlist ({len(rows)} candidates) — the LLM may only pick from these:")
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)


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
    st.sidebar.caption(
        f"Stops when: certainty ≥ {CONFIDENCE_TARGET:.0%} · "
        f"or same level {STABLE_WINDOW}× in a row "
        f"(after {MIN_QUESTIONS} questions, certainty ≥ {STABILITY_FLOOR:.0%}) · "
        f"or {MAX_QUESTIONS} questions · "
        f"selection: {'OpenAI procedural (KL→Fisher)' if llm_enabled() else 'engine only (KL→Fisher)'}"
    )

    item = state.get("current_item")
    if item and not state.get("done"):
        fi = state.get("current_fisher_i") or fisher_info(state["theta_hat"], item)
        sel_meta = state.get("last_selection") or {}
        st.sidebar.caption(
            f"Next **{item['id']}** ({item.get('difficulty', '?')}, b={item.get('b', 0):+.2f}) · "
            f"Fisher I = **{fi:.4f}** · ranked by {sel_meta.get('criterion', 'Fisher')} "
            f"(score {state.get('current_info_score', 0.0):.4f})"
        )
        if sel_meta.get("adaptation_note"):
            with st.sidebar.expander("LLM adaptation note", expanded=False):
                st.write(sel_meta["adaptation_note"])
                if sel_meta.get("procedure_steps"):
                    for step in sel_meta["procedure_steps"]:
                        st.caption(step)
                if sel_meta.get("shortlist_ids"):
                    st.caption(f"Shortlist: {', '.join(sel_meta['shortlist_ids'])}")
                if sel_meta.get("fallback_used"):
                    st.warning(f"Fallback: {sel_meta.get('fallback_reason', 'unknown reason')}")
                elif not sel_meta.get("procedure_followed", True):
                    st.warning(sel_meta.get("procedure_deviation_reason", "Procedure deviation"))

    with st.sidebar.expander("LLM selection audit", expanded=False):
        total = st.session_state.get("selection_steps", 0)
        llm_steps = st.session_state.get("llm_selection_steps", 0)
        fallbacks = st.session_state.get("selection_fallbacks", 0)
        deviations = st.session_state.get("selection_procedure_deviations", 0)
        rephrase_rejects = st.session_state.get("rephrase_rejects", 0)
        c1, c2 = st.columns(2)
        c1.metric("Selections", total)
        c2.metric("LLM selections", llm_steps)
        c3, c4 = st.columns(2)
        c3.metric("Fallbacks", fallbacks)
        c4.metric("Procedure deviations", deviations)
        st.metric("Rephrase rejects", rephrase_rejects)
        st.caption(
            "A valid LLM-selected shortlist id is administered, but deviations from the "
            "documented procedure are counted here and traced to Langfuse."
        )

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

    rephrase_toggled = st.checkbox(
        "Adaptively rephrase question stems",
        value=rephrase_enabled(),
        disabled=not llm_enabled(),
        help=(
            "The LLM rewrites the stem to suit the examinee's level. Every rewrite is "
            "checked for polarity flips, answer leaks and dropped identifiers before "
            "display; rejected ones fall back to the original wording. Off by default: "
            "the guard cannot prove a rewrite preserved difficulty, and the item is "
            "still scored with the `b` calibrated on the original stem."
        ),
        key="rephrase_toggle_widget",
    )
    set_rephrase_enabled(bool(rephrase_toggled) and llm_enabled())
    if rephrase_enabled():
        st.caption(
            "⚠️ Rephrasing is an **uncalibrated deviation from IRT**: `b` is calibrated for the "
            "original wording, so a rewritten stem is not strictly the item that was calibrated. "
            "`rephrase_guard.py` rejects rewrites that leak the key, drop identifiers the item "
            "turns on, or change length drastically — but it cannot prove difficulty is preserved. "
            "Turn this off for a psychometrically clean run."
        )

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
        st.session_state["selection_steps"] = 0
        st.session_state["llm_selection_steps"] = 0
        st.session_state["selection_fallbacks"] = 0
        st.session_state["selection_procedure_deviations"] = 0
        st.session_state["rephrase_rejects"] = 0
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
    sel_meta = state.get("last_selection") or {}
    st.subheader(comp)

    render_cat_state_row(state, item, sel_meta, q_num)

    if sel_meta.get("llm_used"):
        with st.expander("Why this question? (LLM procedural selection)", expanded=False):
            render_selection_rationale(state, item, sel_meta)

    # A rejected rephrase means the examinee is seeing the calibrated wording. Say so
    # here rather than only in the log — it is the difference between the item the
    # parameters describe and one they do not.
    if sel_meta.get("rephrase_rejected_reason"):
        st.warning(
            f"Rephrasing rejected ({sel_meta.get('rephrase_rejected_code', '?')}): "
            f"{sel_meta['rephrase_rejected_reason']}. Showing the original calibrated stem."
        )

    display_stem = item.get("display_stem", item["stem"])
    was_rephrased = "display_stem" in item
    if was_rephrased:
        st.caption("✍️ Stem adapted to your level — technical content and answer unchanged.")
    st.markdown(f"**{display_stem}**")
    if item.get("sub_competency"):
        st.caption(item["sub_competency"])
    if was_rephrased:
        with st.expander("Show the original wording", expanded=False):
            st.markdown(item.get("original_stem", item["stem"]))

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
