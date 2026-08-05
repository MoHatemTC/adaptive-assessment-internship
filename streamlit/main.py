"""Tester harness for the orchestrated multi-modality adaptive assessment.

Built for people who need to CHECK the engine, not for candidates. Every number a decision
rested on is on screen: what is queued and why it was shortlisted, which information
criterion is in force, what each update did to the posterior, and what the engine logged
while doing it.

Three screens:

    Setup        choose one or more main competencies from the bank, self-rate each, and
                 say how sure that rating is. The rating seeds the prior; the confidence
                 sets its width. Sub-competencies are never chosen by the candidate.
    Assessment   answer MCQ / code / Live voice, and watch the queue, mathematics and
                 trajectory move. Open items prefer Live; typed fallback if Live is down.
    Report       on convergence — the same detail, retained rather than cleared, because
                 the interesting part of a session is usually visible only afterwards.

Reads the engine and never reaches into it: everything shown is either returned by the
public API or recomputed from it with the engine's own functions.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from pathlib import Path
from urllib.parse import urlencode

import httpx
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

# Conda envs with openai 0.x explode on `from openai import APIConnectionError`. Require
# the repo `.venv` (openai>=1.50) before importing the engine.
try:
    import openai as _openai_pkg
    from openai import APIConnectionError as _APIConnectionError  # noqa: F401
except ImportError as _openai_exc:
    raise SystemExit(
        f"Incompatible openai package ({_openai_exc}). "
        "From the repo root use: `.venv/bin/streamlit run streamlit/main.py` "
        "or `./scripts/run_streamlit.sh` — not a bare conda `streamlit` with openai 0.x."
    ) from _openai_exc

from app.config.settings import settings  # noqa: E402
from app.config.voice_settings import voice_settings  # noqa: E402
from app.schemas.voice import VoiceResponsePackage  # noqa: E402
from app.services import observability  # noqa: E402
from app.services.adaptive.irt import ability_band  # noqa: E402
from app.services.code_adaptive import (  # noqa: E402
    CodeAdaptiveSession,
    JsonQuestionRepository,
)
from app.services.code_adaptive import trial  # noqa: E402
from app.services.orchestrator import GraderAgent, Orchestrator  # noqa: E402
from app.services.orchestrator import registry, session_dump  # noqa: E402
from app.services.orchestrator import variables as variables_module  # noqa: E402
from app.services.adaptive.irt import expected_fisher_information  # noqa: E402
from app.services.orchestrator.picker import criterion_for, information_for  # noqa: E402
from app.services.orchestrator.competency import rollup_outcomes  # noqa: E402
from app.services.competency_graph.coverage import unmeasured_required_nodes  # noqa: E402
from app.services.voice.evaluator import evaluate as evaluate_voice  # noqa: E402
from app.services.voice.evaluator import package_from_text  # noqa: E402
from app.services.voice_live.streamlit_live import StreamlitLiteLLMLiveBridge  # noqa: E402

import graph_view  # noqa: E402 — Streamlit-local helper beside session_log


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

# The tester options checkbox has no `value=`; this is its default, and living here rather
# than at the widget means the setting has a defined value from the first line of the first
# run — before any screen has decided to render the control that owns it.
st.session_state.setdefault("use_llm_choice", bool(settings.litellm_api_key))

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
def active_bank_id() -> str:
    """The bank this run is administered against.

    Read from a plain session key fixed at Begin, for the same reason as `use_llm()`:
    Streamlit garbage-collects keys belonging to widgets that stopped rendering, and a
    measurement must not be able to change which bank it is measuring against halfway
    through. Before Begin it falls back to the configured default.
    """
    return st.session_state.get("bank_id") or registry.resolve_bank_id()


@st.cache_resource
def engine(bank_id: str) -> Orchestrator:
    return Orchestrator(
        registry.get_bank(bank_id),
        GraderAgent(CodeAdaptiveSession(JsonQuestionRepository())),
        graph=registry.get_graph_service(bank_id),
        coverage_critical_only=registry.profile(bank_id).coverage_critical_only,
        bank_id=bank_id,
    )


@st.cache_resource
def bank(bank_id: str):
    return registry.get_bank(bank_id)


def run_async(coro):
    """Streamlit is synchronous; the orchestrator's queue fill is not."""
    return asyncio.run(coro)


def use_llm() -> bool:
    """Whether the picking agent may call the model.

    Read from a plain session key fixed when the run began — NOT from the setup checkbox's
    own key. Two reasons, and the second is the one that matters:

    Streamlit garbage-collects state belonging to widgets that stopped rendering, so the
    checkbox's key vanishes the moment the setup screen is replaced, and every later read
    of it either fails or silently reports False.

    More importantly, selection policy must not be able to change halfway through a
    measurement. Captured once, at Begin, the session is administered under one policy and
    the report describes what actually happened.

    Off automatically without a gateway key: otherwise every pick would time out, fall back
    to the engine's choice, and produce a session that looks deterministic for a reason the
    tester cannot see.
    """
    return bool(st.session_state.get("picker_uses_llm")) and bool(settings.litellm_api_key)


def render_tester_options(container, *, locked: bool) -> bool:
    """The engine controls, rendered on EVERY screen — settable on setup, locked after.

    Rendered throughout rather than only on setup for two reasons that happen to coincide.

    A tester needs to see which selection policy a running session is actually under; a
    control that vanishes once the session starts leaves them inferring it from behaviour.

    And a widget's state belongs to that widget: Streamlit collects it once the widget
    stops rendering, so a key that appears only on the setup screen is *gone* by the second
    question, and anything still referring to it — including Streamlit's own test runner
    replaying the element tree — reads a key that no longer exists.

    Locked after Begin because selection policy must not change mid-measurement. The value
    that governs the run is `picker_uses_llm`, fixed once; this widget is the input to that
    decision beforehand and a readout of it afterwards.
    """
    left, right = container.columns(2) if container is st else (container, container)
    wants_llm = left.checkbox(
        "Let the picking agent use the model",
        key="use_llm_choice",
        help="Off runs the engine's own deterministic choice, which is also the fallback "
             "whenever the model is unavailable or returns something unusable. Selection "
             "quality is bounded either way: a pick below "
             f"{settings.orchestrator_minimum_relative_utility:.0%} of the best available "
             "information is overridden.",
        disabled=locked or not settings.litellm_api_key,
    )
    if not settings.litellm_api_key:
        right.caption("No LITELLM_API_KEY configured — picking runs deterministically.")
    container.caption(
        "Tracing: "
        + (
            f"Langfuse, {settings.langfuse_environment} @ {settings.langfuse_host}"
            if observability.enabled()
            else "off (set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to enable)"
        )
    )
    if not settings.e2b_api_key and not locked:
        container.warning(
            "No E2B_API_KEY configured. Coding questions can still be selected and shown, "
            "but running one will report a sandbox failure — which correctly moves no "
            "estimate, and is itself worth testing."
        )
    return wants_llm


# --- setup screen -----------------------------------------------------------
def render_setup() -> None:
    st.title("Adaptive assessment — MCQ · Code · Voice")
    st.caption(
        "Full CAT tester (same loop as cat-engine-combined-streamlit): mixed-modality "
        "item selection, queue, θ updates, and report. Open items are answered by Live "
        f"voice via LiteLLM (`{settings.litellm_live_preview_model}`); picker/grader use "
        f"`{settings.litellm_model}`."
    )
    st.caption(
        "Choose the main competencies to be assessed, rate your level in each, and say how "
        "sure you are. The rating seeds the starting estimate; the confidence sets how "
        "easily evidence overrides it. Neither is ever reported as a measurement."
    )

    st.markdown("#### 0 · Question bank")
    catalogue = [b for b in registry.describe() if "error" not in b]
    if not catalogue:
        st.error("No question bank could be loaded.")
        return
    bank_ids = [b["bank_id"] for b in catalogue]
    by_bank = {b["bank_id"]: b for b in catalogue}
    default_bank = registry.resolve_bank_id()
    selected_bank = st.selectbox(
        "Bank",
        options=bank_ids,
        index=bank_ids.index(default_bank) if default_bank in bank_ids else 0,
        format_func=lambda b: (
            f"{b} · {by_bank[b]['title']} — {by_bank[b]['items']} questions, "
            f"{', '.join(by_bank[b]['mains'])}"
        ),
        key="bank_choice",
        help="A bank and its competency graph are selected together. The choice is "
             "locked once the assessment begins.",
    )
    # Read through the plain key, not the widget key: the widget stops rendering after
    # Begin and Streamlit collects its state (see `use_llm`).
    st.session_state["bank_id"] = selected_bank

    coverage = bank(active_bank_id()).coverage()
    tracks = bank(active_bank_id()).tracks()
    if not coverage or not tracks:
        st.error("The question bank is empty.")
        return

    by_code = {t["code"]: t for t in tracks}

    st.markdown("#### 1 · Main competencies")
    track_codes = [t["code"] for t in tracks]
    default_tracks = track_codes[: min(2, len(track_codes))]
    chosen = st.multiselect(
        "Competencies to be assessed",
        options=track_codes,
        default=default_tracks,
        format_func=lambda code: (
            f"{code} · {by_code[code]['name']}  "
            f"— {by_code[code]['items']} questions, "
            f"{', '.join(by_code[code]['modalities']) or '—'}"
        ),
        key="main_competencies",
        help="Select one or more main competencies. Sub-competencies are inferred by the "
             "engine from the questions administered — you never choose them here.",
    )
    if not chosen:
        st.info("Select at least one main competency.")
        return

    st.markdown("#### 2 · Self-rating")

    intake: dict[str, int] = {}
    confident: dict[str, bool] = {}
    for variable in chosen:
        left, middle, right = st.columns([2, 3, 2])
        left.markdown(f"**{variable} · {by_code[variable]['name']}**")
        left.caption(
            ", ".join(f"{n} {m}" for m, n in sorted(coverage.get(variable, {}).items()))
            or "no items"
        )
        intake[variable] = middle.slider(
            "Your level", 1, 5, 3, key=f"level_{variable}",
            help="1 novice to 5 expert. Mapped onto the ability scale as a starting point.",
        )
        confident[variable] = right.radio(
            "How sure?", ["High", "Low"], index=1, key=f"conf_{variable}",
            help="High narrows the starting estimate for this competency; low leaves it "
                 "wide. Either way a few answers override it.",
        ) == "High"

    st.markdown("---")

    with st.expander("Tester options — not part of the examinee's flow"):
        wants_llm = render_tester_options(st, locked=False)

    if st.button("Begin assessment", type="primary"):
        state = engine(active_bank_id()).begin(chosen, intake=intake, confidence=confident)
        session_log.clear()
        st.session_state.update(
            run=state, steps=[], rng_seed=0, finished=False, stop_reason="", pending=None,
            competencies=chosen,
            competency_names={c: by_code[c]["name"] for c in chosen},
            trials={},
            picker_uses_llm=wants_llm,
            bank_id=selected_bank,
        )
        st.rerun()


# --- shared panels ----------------------------------------------------------
def render_queue(state) -> None:
    """What is waiting, why it was shortlisted, and why the picker took it."""
    if state.presenting:
        st.caption(
            f"**Being answered:** {state.presenting.variable} · "
            f"{state.presenting.item_id} ({state.presenting.modality}) — "
            "not in the queue while the candidate answers."
        )
    if not state.queue:
        if not state.presenting:
            st.caption("The queue is empty.")
        else:
            st.caption("No other competencies queued yet.")
        return

    rows = []
    for variable, candidate in sorted(state.queue.items()):
        item = bank(active_bank_id()).get(candidate.item_id)
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
        "One slot per open competency other than the one being answered. `regret` is the "
        "fraction of the best available information given up — 0 means the engine's own top "
        "pick was taken. A pick below "
        f"{settings.orchestrator_minimum_relative_utility:.0%} of the best is overridden."
    )

    for variable, candidate in sorted(state.queue.items()):
        with st.expander(f"Why these were shortlisted for {variable} — {candidate.criterion}"):
            st.caption(CRITERION_EXPLAINED.get(candidate.criterion, ""))
            variable_state = state.variables[variable]
            shortlist_rows = []
            for rank, item_id in enumerate(candidate.shortlist_ids, start=1):
                item = bank(active_bank_id()).get(item_id)
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


def graph_node_status(state, node_id: str) -> str:
    """Live graph status first, then analysis-only shadow status."""
    if node_id in getattr(state, "graph_contradicted_nodes", []):
        return "contradicted"
    if node_id in getattr(state, "graph_blocked_nodes", []):
        return "blocked"
    if node_id in getattr(state, "graph_direct_mastered_nodes", []):
        return "mastered"
    if node_id in getattr(state, "graph_direct_not_mastered_nodes", []):
        return "direct not mastered"
    if node_id in getattr(state, "graph_shadow_contradicted_nodes", []):
        return "shadow · contradicted"
    if node_id in getattr(state, "graph_shadow_blocked_nodes", []):
        return "shadow · blocked"
    if node_id in getattr(state, "graph_shadow_direct_mastered_nodes", []):
        return "shadow · direct mastered"
    if node_id in getattr(state, "graph_shadow_direct_not_mastered_nodes", []):
        return "shadow · direct not mastered"
    if node_id in getattr(state, "graph_shadow_inferred_mastered_nodes", []):
        return "shadow · inferred mastered"
    # Last, and named as a forecast. Every state above it was computed from evidence the
    # engine acted on; this one is what an untrusted edge would have said.
    if node_id in (getattr(state, "graph_preview_inferred_nodes", {}) or {}):
        return "preview · would be inferred"
    if node_id in getattr(state, "graph_preview_blocked_nodes", []):
        return "preview · would be blocked"
    return "unknown"


def render_competency_math(state) -> None:
    """Live CAT mathematics: mains, related subs, item parameters, queued candidates."""
    st.markdown("**Main competencies — live CAT estimates**")
    st.latex(r"\hat\theta = \sum_k \theta_k\, p(\theta_k),\quad"
             r"\mathrm{SE}=\sqrt{\sum_k(\theta_k-\hat\theta)^2 p(\theta_k)}")
    st.caption(
        "Each main competency holds a posterior on the shared θ grid. "
        f"Precision stop targets SE ≤ {settings.cat_se_target} after at least "
        f"{settings.cat_precision_min_questions} observations. "
        "3PL item response: "
    )
    st.latex(r"P(\theta)=c+(1-c)\,\frac{1}{1+e^{-a(\theta-b)}}")

    main_rows = []
    for variable, vs in sorted(state.variables.items()):
        level, band = ability_band(vs.theta_hat)
        main_rows.append(
            {
                "main": variable,
                "θ̂": round(vs.theta_hat, 4),
                "SE": round(vs.standard_error, 4),
                "precision index": round(variables_module.certainty(vs), 1),
                "95% CI": (
                    "—"
                    if not vs.observations
                    else "[{:.1f}, {:.1f}]".format(*variables_module.posterior_interval(vs))
                ),
                "n": vs.observations,
                "criterion": criterion_for(vs.observations),
                "level": level if vs.observations else None,
                "band": band if vs.observations else "Not assessed",
                "finalised": vs.finalised,
                "stop": vs.stop_reason or "—",
            }
        )
    st.dataframe(pd.DataFrame(main_rows), hide_index=True, **WIDE)

    # Presenting item → related sub-competencies + CAT parameters
    presenting = state.presenting
    if presenting is not None:
        item = bank(active_bank_id()).get(presenting.item_id)
        st.markdown(
            f"**Presenting item** `{presenting.item_id}` · {presenting.modality} · "
            f"criterion `{presenting.criterion}`"
        )
        if item is not None:
            st.latex(
                rf"a={item.cat.a:.3f},\quad b={item.cat.b:.3f},\quad c={item.cat.c:.3f}"
            )
            vs = state.variables.get(presenting.variable)
            if vs is not None:
                info = information_for(item, vs, presenting.variable)
                st.caption(
                    f"Information under {presenting.criterion} at "
                    f"θ̂={vs.theta_hat:+.3f}: **{info:.5f}** "
                    f"(loading on {presenting.variable} = {item.loading(presenting.variable):.2f})"
                )
            measure_rows = [
                {
                    "sub-competency": m.variable,
                    "loading w": m.weight,
                    "main": m.variable.split(".")[0],
                    "graph status": graph_node_status(state, m.variable),
                }
                for m in item.measures
            ]
            st.markdown("**Sub-competencies measured by this item**")
            st.dataframe(pd.DataFrame(measure_rows), hide_index=True, **WIDE)

    # Queued candidates — full CAT parameter + picker info
    st.markdown("**Queued candidates — CAT parameters & picker math**")
    if not state.queue:
        st.caption("No queued candidates right now.")
    else:
        cand_rows = []
        for variable, candidate in sorted(state.queue.items()):
            item = bank(active_bank_id()).get(candidate.item_id)
            vs = state.variables[variable]
            cand_rows.append(
                {
                    "main": variable,
                    "item": candidate.item_id,
                    "modality": candidate.modality,
                    "θ̂": round(vs.theta_hat, 4),
                    "SE": round(vs.standard_error, 4),
                    "n": vs.observations,
                    "criterion": candidate.criterion,
                    "I / utility": round(candidate.utility, 5),
                    "best I": round(candidate.best_information, 5),
                    "regret": round(candidate.normalized_regret, 4),
                    "a": item.cat.a if item else None,
                    "b": item.cat.b if item else None,
                    "c": item.cat.c if item else None,
                    "loading": item.loading(variable) if item else None,
                    "subs": ", ".join(m.variable for m in item.measures) if item else "—",
                    "by": "model" if candidate.chosen_by_llm else "engine",
                    "reason": candidate.reason_code,
                }
            )
        st.dataframe(pd.DataFrame(cand_rows), hide_index=True, **WIDE)

    # Graph node ledger for session mains' related subs
    try:
        service = graph_view.graph_service(active_bank_id())
        session_mains = set(state.variables)
        sub_rows = []
        for nid, node in sorted(service.graph.nodes.items()):
            if node.node_type != "sub_competency":
                continue
            mains = set(node.main_competencies) or {nid.split(".")[0]}
            if not (mains & session_mains):
                continue
            parents = service.prerequisites_parents(nid)
            children = service.prerequisites_children(nid)
            sub_rows.append(
                {
                    "sub": nid,
                    "title": node.title,
                    "mains": ", ".join(mains),
                    "critical": node.critical,
                    "prerequisites": ", ".join(parents) or "—",
                    "unlocks": ", ".join(children) or "—",
                    "status": graph_node_status(state, nid),
                }
            )
        if sub_rows:
            st.markdown("**Related sub-competency nodes (graph)**")
            st.dataframe(pd.DataFrame(sub_rows), hide_index=True, **WIDE)
    except Exception as exc:  # noqa: BLE001
        session_log.note_ui_error("graph_view", "failed to list related sub-nodes", exc=exc)
        st.warning(f"Could not load graph sub-nodes: {exc}")


def render_mathematics() -> None:
    """Every update, in the order it happened — plus live CAT / graph math."""
    state = st.session_state.get("run")
    if state is not None:
        render_competency_math(state)
        st.markdown("---")

    st.markdown("**Update history (fractional likelihood steps)**")
    st.latex(
        r"L(\theta)=\bigl[P(\theta)^{s}\,(1-P(\theta))^{1-s}\bigr]^{w}"
    )
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


def render_edge_preview(
    service,
    preview_inferred: dict,
    preview_blocked: set[str],
    preview_refuted: list[dict],
) -> None:
    """What the unvalidated prerequisite edges would have concluded, and what refuted it.

    Every edge in this bank ships with `allow_upward_inference` and
    `allow_downward_blocking` false, because the numbering order they encode is an
    authoring convention and not measured dependency data. So the live inferred and
    blocked counts are structurally zero, and will stay zero until a corpus says an edge
    is real. This panel is how that corpus gets built: it runs the same inference with the
    validation gate waived, records the result, and marks every belief the session went on
    to measure and disprove.
    """
    with st.expander(
        f"Unvalidated-edge forecast — {len(preview_inferred)} would be inferred, "
        f"{len(preview_blocked)} would be blocked, {len(preview_refuted)} refuted",
        expanded=bool(preview_refuted),
    ):
        st.caption(
            "Reporting only. Nothing here reached the ability estimate, the item "
            "selection or the coverage gate — it is what WOULD have happened had the "
            "prerequisite edges been trusted. `scripts/validate_prerequisite_edges.py` "
            "turns a corpus of these into a per-edge verdict."
        )
        if not preview_inferred and not preview_blocked:
            st.caption(
                "Nothing yet. Inference needs a strong success on a node that has a "
                "prerequisite parent, in a modality permitted to infer — one multiple-"
                "choice hit is not."
            )
            return

        refuted_kind = {row.get("node"): row.get("kind") for row in preview_refuted}
        rows = [
            {
                "node": nid,
                "title": service.graph.nodes[nid].title if nid in service.graph.nodes else nid,
                "would be": "blocked" if nid in preview_blocked else "inferred mastered",
                "from": provenance.get("source_node", "—"),
                "hops": provenance.get("distance"),
                "strength": provenance.get("strength"),
                "via": provenance.get("modality", "—"),
                "refuted by direct evidence": refuted_kind.get(nid, "—"),
            }
            for nid, provenance in sorted(preview_inferred.items())
        ]
        rows.extend(
            {
                "node": nid,
                "title": service.graph.nodes[nid].title if nid in service.graph.nodes else nid,
                "would be": "blocked",
                "from": "—",
                "hops": None,
                "strength": None,
                "via": "—",
                "refuted by direct evidence": refuted_kind.get(nid, "—"),
            }
            for nid in sorted(preview_blocked - set(preview_inferred))
        )
        st.dataframe(pd.DataFrame(rows), hide_index=True, **WIDE)

        wrong = [r for r in preview_refuted if r.get("kind") == "wrong_inference"]
        false_blocks = [r for r in preview_refuted if r.get("kind") == "false_block"]
        if wrong:
            st.warning(
                f"{len(wrong)} node(s) the graph would have called mastered were measured "
                "directly and failed. Each is evidence against the edge that carried the "
                "inference — and, had that edge been live, a competency the candidate "
                "would never have been asked about."
            )
        if false_blocks:
            st.warning(
                f"{len(false_blocks)} node(s) the graph would have declared unreachable "
                "were reached. This is the false-blocking rate, and it is measurable only "
                "because the block was never enforced: the item was still served."
            )


def render_graph_dag(state) -> None:
    """Realtime competency DAG focused on the current item path."""
    st.markdown("**Competency dependency DAG**")
    st.caption(
        "Focus = nodes measured by the presenting item (or last graded item if no presenting item). "
        "Path = prerequisites + descendants. "
        "Mastered / blocked / contradicted come from persisted graph session state."
    )
    try:
        service = graph_view.graph_service(active_bank_id())
    except Exception as exc:  # noqa: BLE001
        session_log.note_ui_error("graph_view", "failed to load competency graph", exc=exc)
        st.error(f"Graph failed to load: {exc}")
        return
    if service is None:
        st.info(f"Bank `{active_bank_id()}` declares no competency graph.")
        return

    option_col1, option_col2 = st.columns(2)
    with option_col1:
        include_shadow = st.checkbox("Include shadow state", value=False)
    with option_col2:
        show_full_dag = st.checkbox(
            "Show full DAG",
            value=False,
            help="Otherwise show only the current item's prerequisite and dependent path.",
        )

    focus: set[str] = set()
    presenting = state.presenting
    if presenting is not None:
        item = bank(active_bank_id()).get(presenting.item_id)
        if item is not None:
            focus.update(m.variable for m in item.measures)
        focus.add(presenting.variable)
    else:
        last = st.session_state.get("last_graded") or {}
        for o in last.get("outcomes") or []:
            if isinstance(o, dict) and o.get("variable"):
                focus.add(str(o["variable"]))

    if not focus:
        # Session mains → their critical / own subs
        for main in state.variables:
            focus.add(main)
            for nid, node in service.graph.nodes.items():
                if main in node.main_competencies:
                    focus.add(nid)

    displayed_blocked = set(getattr(state, "graph_blocked_nodes", []))
    displayed_mastered = set(getattr(state, "graph_direct_mastered_nodes", []))
    displayed_not_mastered = set(getattr(state, "graph_direct_not_mastered_nodes", []))
    displayed_contradicted = set(getattr(state, "graph_contradicted_nodes", []))
    # Inferred is its own colour, never folded into mastered. The whole point of the
    # direct/inferred split is that a deduction did not enter the estimate; a view that
    # paints them the same hides exactly what a reviewer is here to check.
    displayed_inferred = set(getattr(state, "graph_inferred_mastered_nodes", []))
    # The unvalidated-edge forecast, in its own colour and its own metric. Every AI
    # Engineer prerequisite edge ships inert, so `displayed_inferred` is empty for every
    # session this bank will ever run — which looks exactly like inference being broken.
    preview_inferred = dict(getattr(state, "graph_preview_inferred_nodes", {}) or {})
    preview_blocked = set(getattr(state, "graph_preview_blocked_nodes", []))
    preview_refuted = list(getattr(state, "graph_preview_refuted_nodes", []))

    if include_shadow:
        displayed_blocked |= set(getattr(state, "graph_shadow_blocked_nodes", []))
        displayed_mastered |= set(getattr(state, "graph_shadow_direct_mastered_nodes", []))
        displayed_inferred |= set(getattr(state, "graph_shadow_inferred_mastered_nodes", []))
        displayed_not_mastered |= set(
            getattr(state, "graph_shadow_direct_not_mastered_nodes", [])
        )
        displayed_contradicted |= set(getattr(state, "graph_shadow_contradicted_nodes", []))
    diagram_focus = set() if show_full_dag else focus
    diagram = graph_view.svg_for_subgraph(
        service,
        focus_nodes=diagram_focus,
        blocked=displayed_blocked,
        mastered=displayed_mastered,
        not_mastered=displayed_not_mastered,
        inferred=displayed_inferred,
        preview_inferred=set(preview_inferred),
        preview_blocked=preview_blocked,
        contradicted=displayed_contradicted,
    )
    graph_view.render_svg(diagram)
    rendered_nodes, rendered_edges = graph_view.related_subgraph(service, diagram_focus)
    if not rendered_nodes:
        rendered_nodes = set(service.graph.nodes)
        rendered_edges = [
            (edge.from_id, edge.to_id, edge.relation) for edge in service.graph.edges
        ]
    st.caption(
        f"Rendered {len(rendered_nodes)} nodes and {len(rendered_edges)} directed edges. "
        "Scroll horizontally inside the graph when viewing the full DAG."
    )
    st.download_button(
        "Download DAG as SVG",
        data=diagram.encode("utf-8"),
        file_name=f"{state.session_id}_competency_dag.svg",
        mime="image/svg+xml",
    )

    col1, col2, col3, col4, col5, col6 = st.columns(6)
    col1.metric("Focus nodes", len(focus))
    col2.metric("Mastered", len(displayed_mastered), help="Directly measured.")
    col3.metric("Not mastered", len(displayed_not_mastered), help="Directly measured.")
    col4.metric(
        "Inferred",
        len(displayed_inferred),
        help="Deduced from a prerequisite relationship. Never measured, and never folded "
             "into the ability estimate.",
    )
    col5.metric("Blocked", len(displayed_blocked))
    col6.metric("Contradicted", len(displayed_contradicted))

    render_edge_preview(service, preview_inferred, preview_blocked, preview_refuted)

    with st.expander("Node status and dependency detail", expanded=True):
        related_nodes, _ = graph_view.related_subgraph(service, diagram_focus)
        if not related_nodes:
            related_nodes = set(service.graph.nodes)
        rows = []
        for nid in sorted(related_nodes):
            node = service.graph.nodes.get(nid)
            status = "unknown"
            if nid in displayed_contradicted:
                status = "contradicted"
            elif nid in displayed_blocked:
                status = "blocked"
            elif nid in displayed_mastered:
                status = "mastered"
            elif nid in displayed_not_mastered:
                status = "direct not mastered"
            elif nid in preview_inferred:
                status = "preview · would be inferred"
            elif nid in preview_blocked:
                status = "preview · would be blocked"
            elif nid in focus:
                status = "focus"
            rows.append(
                {
                    "node": nid,
                    "title": node.title if node else "—",
                    "type": node.node_type if node else "—",
                    "status": status,
                    # An unmeasured node is only a gap if coverage required it. This bank
                    # gates on critical nodes only — C6 declares more sub-competencies than
                    # a session has questions — so most "unknown" rows are budget, not a
                    # hole. Without this column the two are indistinguishable.
                    "required for coverage": bool(node.critical) if node else False,
                    "ancestors": ", ".join(sorted(service.ancestors(nid))) or "—",
                    "descendants": ", ".join(sorted(service.descendants(nid))) or "—",
                    "mains": ", ".join(service.mains_for_node(nid)) or "—",
                }
            )
        st.dataframe(pd.DataFrame(rows), hide_index=True, **WIDE)


def render_tracebook() -> None:
    """Error / warning Tracebook — engine + graph + UI catch blocks."""
    tally = session_log.counts()
    err_count = sum(tally.get(k, 0) for k in ("WARNING", "ERROR", "CRITICAL"))
    st.markdown("**Tracebook**")
    st.caption(
        f"{err_count} warning/error events · "
        + (" · ".join(f"{k} {v}" for k, v in sorted(tally.items())) or "no events yet")
    )

    errors = session_log.errors()
    if not errors:
        st.success("No warnings or errors in this session yet.")
        if observability.enabled():
            st.caption("Langfuse tracing is on — engine generations are grouped by session id.")
        return

    for record in reversed(errors[-100:]):
        header = f"`{record['time']}` **{record['level']}** · `{record['source']}`"
        body = record["message"]
        if record.get("exc_text"):
            body = f"{body}\n\n```\n{record['exc_text'][-2000:]}\n```"
        if record["level"] in ("ERROR", "CRITICAL"):
            st.error(f"{header}\n\n{body}")
        else:
            st.warning(f"{header}\n\n{body}")

    if st.button("Clear Tracebook"):
        session_log.clear()
        st.rerun()


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
        st.markdown("**Posterior precision index by competency**")
        precision_col = (
            "precision index"
            if "precision index" in frame.columns
            else "confidence after"
        )
        pivot = frame.pivot_table(
            index="step", columns="competency", values=precision_col, aggfunc="last"
        ).ffill()
        st.line_chart(pivot)
        st.caption(
            "This is a remapped posterior SE (precision index), **not** a calibrated "
            "probability that the band is correct. It ramps while evidence is thin "
            f"(below {settings.cat_precision_min_questions} items it stays provisional and "
            f"under 90). A competency finalises on precision only when SE ≤ "
            f"{settings.cat_se_target} **and** at least "
            f"{settings.cat_precision_min_questions} items were answered — or when its "
            "band settles under the stable-band rule."
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
                "precision index": f"{variables_module.certainty(variable_state):.1f}",
                "95% CI": (
                    "—"
                    if not measured
                    else "[{:.1f}, {:.1f}]".format(
                        *variables_module.posterior_interval(variable_state)
                    )
                ),
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
                 "meaning": "posterior SD that maps to precision-index 90; "
                            "precision stop also needs the min-questions floor"},
                {"setting": "precision min questions", "value": str(settings.cat_precision_min_questions),
                 "meaning": "precision stop cannot fire before this many scored items"},
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
                {"setting": "trial runs per question",
                 "value": f"{settings.code_trial_runs_per_question} × "
                          f"{settings.code_trial_run_tests} public cases",
                 "meaning": "candidates may run the example cases before submitting; "
                            "hidden cases are never reachable and nothing is graded"},
                {"setting": "tracing",
                 "value": "langfuse" if observability.enabled() else "off",
                 "meaning": "every model call is traced and grouped by session id; "
                            "candidate source code is never sent"},
                {"setting": "graph shadow mode",
                 "value": str(settings.graph_shadow_mode),
                 "meaning": "runs graph propagation in analysis-only mode (no posterior changes)"},
                {"setting": "graph filtering",
                 "value": str(settings.graph_filtering_enabled),
                 "meaning": "filters candidate items using persisted blocked / mastered nodes"},
                {"setting": "graph utility",
                 "value": str(settings.graph_utility_enabled),
                 "meaning": "adds graph-based utility modifiers on top of KL / E[Fisher]"},
                {"setting": "graph master switch",
                 "value": str(settings.competency_graph_enabled),
                 "meaning": "off disables every graph effect below, whatever they say"},
                {"setting": "graph convergence gate",
                 "value": str(settings.graph_convergence_gate_enabled),
                 "meaning": "prevents finalisation while graph contradictions remain"},
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
    parity = bank(active_bank_id()).parity_report()
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

    with st.expander("Graph-augmented CAT state"):
        run_state = st.session_state["run"]
        graph = graph_view.graph_service(active_bank_id())
        missing_by_main = {
            main: sorted(
                unmeasured_required_nodes(
                    graph,
                    main,
                    measured=getattr(run_state, "graph_direct_measured_nodes", []),
                    mastered=run_state.graph_direct_mastered_nodes,
                    not_mastered=getattr(
                        run_state, "graph_direct_not_mastered_nodes", []
                    ),
                    critical_only=settings.graph_coverage_critical_only,
                )
            )
            for main in run_state.variables
        }
        st.caption(
            "Operational Phase C+ state is listed first. Shadow fields are analysis-only "
            "and do not affect CAT selection, posterior updates, or convergence."
        )
        st.json(
            {
                "blocked_nodes": run_state.graph_blocked_nodes,
                "direct_measured_nodes": getattr(
                    run_state, "graph_direct_measured_nodes", []
                ),
                "coverage_missing_by_main": missing_by_main,
                "direct_mastered_nodes": run_state.graph_direct_mastered_nodes,
                "direct_not_mastered_nodes": getattr(
                    run_state, "graph_direct_not_mastered_nodes", []
                ),
                "contradicted_nodes": run_state.graph_contradicted_nodes,
                "shadow_direct_mastered_nodes": (
                    getattr(run_state, "graph_shadow_direct_mastered_nodes", [])
                ),
                "shadow_inferred_mastered_nodes": (
                    getattr(run_state, "graph_shadow_inferred_mastered_nodes", [])
                ),
                "shadow_blocked_nodes": getattr(
                    run_state, "graph_shadow_blocked_nodes", []
                ),
                "shadow_contradicted_nodes": (
                    getattr(run_state, "graph_shadow_contradicted_nodes", [])
                ),
                "graph_last_affected_mains": run_state.graph_last_affected_mains,
            },
            expanded=False,
        )

    with st.expander("Raw session state"):
        st.json(st.session_state["run"].model_dump(), expanded=False)


# --- answering --------------------------------------------------------------
def record(state, item, candidate, response) -> None:
    """Grade one response and record everything the tables need."""
    before = {v: s for v, s in state.variables.items()}
    try:
        with observability.session(
            state.session_id,
            track=st.session_state.get("competencies"),
            stage="grade",
            item_id=item.item_id,
            modality=item.modality,
            variable=candidate.variable if candidate else None,
            # The submission itself is never sent — see observability.redact_code.
            submission=observability.redact_code(response) if isinstance(response, str) else None,
        ):
            new_state, graded = engine(active_bank_id()).record_response(state, item, response)
    except Exception as exc:  # noqa: BLE001
        session_log.note_ui_error(
            "streamlit.record",
            f"grading failed for {item.item_id} ({item.modality}): {exc}",
            exc=exc,
        )
        st.error(f"Grading failed — see Tracebook. `{type(exc).__name__}: {exc}`")
        return

    step = len(st.session_state["steps"]) and max(r["step"] for r in st.session_state["steps"])
    step = step + 1

    detail = graded.detail or {}
    if graded.modality == "code":
        score_shown = detail.get("overall_score")
    elif graded.modality in ("open", "voice"):
        # Show the graded ability score, not grader self-confidence (those often look
        # like 0.99 even when the answer scored near zero).
        outcome_scores = [
            float(o["score"]) if isinstance(o, dict) else float(o.score)
            for o in graded.outcomes
        ]
        score_shown = (
            sum(outcome_scores) / len(outcome_scores) if outcome_scores else 0.0
        )
    else:
        score_shown = 1.0 if detail.get("correct") else 0.0

    session_variables = set(new_state.variables)
    # Direct evidence only. Inference shapes selection and the report; it does not enter
    # a posterior, so the tester shows exactly the update the engine performed.
    rolled = rollup_outcomes(graded.outcomes, session_variables)
    outcome_by_variable = {
        o.variable: o.__dict__
        for o in rolled
    }

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
                "precision index": round(variables_module.certainty(after), 2),
                "95% CI after": "[{:.1f}, {:.1f}]".format(
                    *variables_module.posterior_interval(after)
                ),
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
        "outcomes": list(graded.outcomes),
    }

    seed = st.session_state["rng_seed"]
    st.session_state["rng_seed"] = seed + 1
    with observability.session(
        new_state.session_id,
        track=st.session_state.get("competencies"),
        stage="refill_queue",
        item_id=item.item_id,
    ):
        new_state = run_async(
            engine(active_bank_id()).after_response(
                new_state, item, use_llm=use_llm(), rng=np.random.default_rng(seed)
            )
        )
    st.session_state["run"] = new_state


def live_server_ok() -> bool:
    try:
        r = httpx.get(f"{voice_settings.live_server_base}/health", timeout=2.0, verify=False)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def open_text_fallback_allowed() -> bool:
    """Typed answers only when explicitly enabled — spoken Live is the default path."""
    return bool(voice_settings.allow_text_fallback)


def native_live_bridge() -> StreamlitLiteLLMLiveBridge:
    """One turn-based LiteLLM Live session per Streamlit browser session."""
    bridge = st.session_state.get("native_live_bridge")
    required_methods = ("start_interview", "send_candidate_audio", "finish", "abort")
    if bridge is None or any(not callable(getattr(bridge, name, None)) for name in required_methods):
        bridge = StreamlitLiteLLMLiveBridge()
        st.session_state["native_live_bridge"] = bridge
    return bridge  # type: ignore[no-any-return]


def render_open_text_fallback(state, item, candidate, *, reason: str) -> None:
    """Grade a typed / pasted answer the same way Live packages are graded."""
    st.warning(reason)
    text = st.text_area(
        "Written answer (fallback)",
        height=220,
        key=f"open_text_{item.item_id}",
        placeholder="Type or paste your explanation here (roughly 150–300 words)…",
    )
    if st.button(
        "Submit written answer",
        type="primary",
        disabled=not (text or "").strip(),
        key=f"open_submit_{item.item_id}",
    ):
        package = package_from_text(item.item_id, text.strip())
        record_live_package(state, item, candidate, package)
        st.rerun()


def create_live_room(item_id: str, question: str, *, assessment_session_id: str = "") -> str:
    r = httpx.post(
        f"{voice_settings.live_server_base}/api/live/rooms",
        json={
            "item_id": item_id,
            "question": question,
            "save_recording": False,
            "mode": "interview",
            "assessment_session_id": assessment_session_id,
        },
        timeout=30.0,
        verify=False,
    )
    r.raise_for_status()
    return r.json()["room_id"]


def fetch_live_room(room_id: str) -> dict:
    r = httpx.get(
        f"{voice_settings.live_server_base}/api/live/rooms/{room_id}",
        timeout=10.0,
        verify=False,
    )
    r.raise_for_status()
    return r.json()


def record_live_package(state, item, candidate, package: VoiceResponsePackage) -> None:
    """Evaluate a finished Live interview (LiteLLM rubric), then update CAT state."""
    with st.spinner("Grading live interview via LiteLLM…"):
        with observability.session(
            state.session_id,
            track=st.session_state.get("competencies"),
            stage="grade",
            item_id=item.item_id,
            modality="open",
            variable=candidate.variable if candidate else None,
        ):
            graded_voice = run_async(evaluate_voice(item, package, use_llm=use_llm()))
            observability.flush()
    record(state, item, candidate, graded_voice)
    st.session_state.pop("live_room_id", None)
    st.session_state.pop("live_active_item", None)
    st.session_state.pop("native_live_item", None)
    st.session_state.pop("native_live_turns", None)
    st.session_state.pop("native_live_result", None)
    st.session_state.pop("native_live_started", None)
    bridge = st.session_state.get("native_live_bridge")
    if callable(getattr(bridge, "abort", None)):
        try:
            bridge.abort()
        except Exception:  # noqa: BLE001
            pass


def render_native_live_interview(state, item, candidate) -> None:
    """Spoken Live inside Streamlit via LiteLLM realtime (works on Streamlit Cloud)."""
    st.info(
        "Spoken Live interview via LiteLLM (in-app, no separate helper). "
        "Start the interview, listen to the question, record your answer, then "
        "**Finish & grade**. "
        f"Model: `{settings.litellm_live_preview_model}`."
    )

    if st.session_state.get("native_live_item") != item.item_id:
        # Switching items — drop any prior bridge session.
        old = st.session_state.get("native_live_bridge")
        if callable(getattr(old, "abort", None)):
            try:
                old.abort()
            except Exception:  # noqa: BLE001
                pass
        st.session_state["native_live_item"] = item.item_id
        st.session_state["native_live_turns"] = []
        st.session_state.pop("native_live_result", None)
        st.session_state.pop("native_live_started", None)

    turns: list[dict] = list(st.session_state.get("native_live_turns") or [])
    started = bool(st.session_state.get("native_live_started"))

    cols = st.columns(3)
    with cols[0]:
        if st.button("Start live interview", type="primary", disabled=started):
            question = item.payload.get("prompt") or item.payload.get("question", "")
            try:
                st.session_state.pop("native_live_result", None)
                st.session_state.pop("last_audio_error", None)
                with st.spinner("Connecting to LiteLLM Live and speaking the question…"):
                    audio = native_live_bridge().start_interview(
                        item.item_id,
                        question,
                        assessment_session_id=state.session_id,
                    )
                turns = []
                if audio.wav_bytes:
                    turns.append(
                        {
                            "role": "interviewer",
                            "text": audio.transcript,
                            "wav": audio.wav_bytes,
                        }
                    )
                elif audio.transcript:
                    turns.append({"role": "interviewer", "text": audio.transcript, "wav": b""})
                st.session_state["native_live_turns"] = turns
                st.session_state["native_live_started"] = True
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                try:
                    native_live_bridge().abort()
                except Exception:  # noqa: BLE001
                    pass
                st.session_state["last_audio_error"] = (
                    f"{exc}\n{traceback.format_exc(limit=4)}"
                )
                st.error(f"Could not start Live interview: {exc}")

    with cols[1]:
        cached_result = st.session_state.get("native_live_result")
        finish_disabled = not started and cached_result is None
        if st.button("Finish & grade", type="primary", disabled=finish_disabled):
            try:
                with st.spinner("Closing Live session and grading…"):
                    result = cached_result
                    if result is None:
                        result = native_live_bridge().finish()
                        # Persist before grading. If grading fails or Streamlit reruns,
                        # retry from this immutable transcript without requiring a live
                        # websocket that finish() has already closed.
                        st.session_state["native_live_result"] = result
                    package = result.as_package()
                    record_live_package(state, item, candidate, package)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.session_state["last_audio_error"] = (
                    f"{exc}\n{traceback.format_exc(limit=4)}"
                )
                st.error(f"Finish/grade failed: {exc}")

    with cols[2]:
        if st.button("Abort interview", disabled=not started):
            try:
                native_live_bridge().abort()
            except Exception:  # noqa: BLE001
                pass
            st.session_state["native_live_started"] = False
            st.session_state["native_live_turns"] = []
            st.session_state.pop("native_live_result", None)
            st.rerun()

    if st.session_state.get("last_audio_error"):
        st.caption(f"Last Live error: {st.session_state['last_audio_error']}")

    if not started:
        st.caption("Click **Start live interview** — the interviewer will speak the question.")
        if open_text_fallback_allowed():
            with st.expander("Typed fallback (ALLOW_TEXT_FALLBACK=true)"):
                render_open_text_fallback(
                    state,
                    item,
                    candidate,
                    reason="Optional typed path — spoken Live is preferred.",
                )
        return

    for i, turn in enumerate(turns):
        who = "Interviewer" if turn.get("role") == "interviewer" else "You"
        st.markdown(f"**{who}:** {turn.get('text') or '*(audio)*'}")
        wav = turn.get("wav") or b""
        if wav:
            st.audio(wav, format="audio/wav")

    st.markdown("#### Your spoken answer")
    st.caption("Record a full turn, then submit it. You can record again after a probe.")
    audio_file = st.audio_input(
        "Microphone",
        key=f"native_mic_{item.item_id}_{len(turns)}",
    )
    if audio_file is not None and st.button("Send recording to interviewer", type="primary"):
        try:
            wav_bytes = audio_file.getvalue()
            with st.spinner("Sending audio and waiting for the interviewer…"):
                reply = native_live_bridge().send_candidate_audio(wav_bytes)
            turns.append({"role": "candidate", "text": "(spoken answer)", "wav": wav_bytes})
            if reply.wav_bytes or reply.transcript:
                turns.append(
                    {
                        "role": "interviewer",
                        "text": reply.transcript,
                        "wav": reply.wav_bytes or b"",
                    }
                )
            # Refresh candidate ASR text from bridge turns if available.
            bridge = native_live_bridge()
            for t in bridge.turns:
                if t.get("role") == "candidate" and t.get("text"):
                    # Update last candidate bubble with ASR.
                    for j in range(len(turns) - 1, -1, -1):
                        if turns[j].get("role") == "candidate":
                            turns[j]["text"] = t["text"]
                            break
            st.session_state["native_live_turns"] = turns
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.session_state["last_audio_error"] = (
                f"{exc}\n{traceback.format_exc(limit=4)}"
            )
            # A closed realtime socket cannot recover in place. Reset the bridge and UI
            # so the next click starts a fresh interview rather than repeatedly sending
            # into the dead session.
            try:
                native_live_bridge().abort()
            except Exception:  # noqa: BLE001
                pass
            st.session_state["native_live_started"] = False
            st.error(
                f"Could not send audio: {exc}. The closed Live session was reset; "
                "click **Start live interview** to reconnect."
            )


def render_iframe_live_interview(state, item, candidate) -> None:
    """Duplex Live via the FastAPI helper iframe (local / dedicated Live host)."""
    st.info(
        "Speak your answer with the Live interviewer. Use **Finish answer** inside the "
        "panel when done, then **Grade finished interview** here. "
        f"Interviewer: `{settings.litellm_live_preview_model}` via LiteLLM · "
        f"Picker/grader: `{settings.litellm_model}` via LiteLLM."
    )

    if open_text_fallback_allowed():
        with st.expander("Prefer to type instead of speaking?"):
            render_open_text_fallback(
                state,
                item,
                candidate,
                reason="Typed fallback enabled (`ALLOW_TEXT_FALLBACK=true`).",
            )

    room_id = st.session_state.get("live_room_id")
    if st.session_state.get("live_active_item") != item.item_id:
        room_id = None

    cols = st.columns(3)
    with cols[0]:
        if st.button("Open realtime interview", type="primary"):
            try:
                question = item.payload.get("prompt") or item.payload.get("question", "")
                room_id = create_live_room(
                    item.item_id,
                    question,
                    assessment_session_id=state.session_id,
                )
                st.session_state["live_room_id"] = room_id
                st.session_state["live_active_item"] = item.item_id
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.session_state["last_audio_error"] = str(exc)
                st.error(f"Could not open Live room: {exc}")
                st.info("Falling back to in-app spoken Live…")
                render_native_live_interview(state, item, candidate)
    with cols[1]:
        if st.button("Refresh status", disabled=not room_id):
            st.rerun()
    with cols[2]:
        if st.button("Grade finished interview", type="primary", disabled=not room_id):
            try:
                data = fetch_live_room(room_id)
                if data.get("status") != "finished" or not data.get("package"):
                    st.warning(
                        f"Interview not finished yet (status={data.get('status')}). "
                        "Click Finish answer in the live panel first."
                    )
                else:
                    package = VoiceResponsePackage.model_validate(data["package"])
                    record_live_package(state, item, candidate, package)
                    st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.session_state["last_audio_error"] = (
                    f"{exc}\n{traceback.format_exc(limit=4)}"
                )
                st.error(f"Grading failed: {exc}")

    if st.session_state.get("last_audio_error"):
        st.caption(f"Last Live error: {st.session_state['last_audio_error']}")

    if not room_id:
        st.caption("Click **Open realtime interview** to start speaking.")
        return

    question = item.payload.get("prompt") or item.payload.get("question", "")
    qs = urlencode({"item_id": item.item_id, "question": question, "room_id": room_id})
    st.iframe(f"{voice_settings.live_public_base}/interview?{qs}", height=480)

    try:
        data = fetch_live_room(room_id)
        st.caption(
            f"Room `{room_id}` · status **{data.get('status')}** · "
            f"turn **{data.get('turn_state', '—')}**"
        )
        if data.get("status") == "error" and data.get("error"):
            st.error(data["error"])
        if data.get("turns"):
            with st.expander("Live conversation", expanded=True):
                for turn in data["turns"]:
                    who = "Interviewer" if turn.get("role") == "interviewer" else "You"
                    st.markdown(f"**{who}:** {turn.get('text', '')}")
        if data.get("status") == "finished":
            st.success("Interview finished — click **Grade finished interview**.")
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not poll room status: {exc}")


def render_live_interview(state, item, candidate) -> None:
    """Open/voice: duplex helper when available, else in-app LiteLLM Live."""
    if not settings.litellm_api_key.strip():
        st.error(
            "Set `LITELLM_API_KEY` and `LITELLM_BASE_URL` (Streamlit secrets / `.env`) "
            f"for Live interviews via `{settings.litellm_live_preview_model}`."
        )
        if open_text_fallback_allowed():
            render_open_text_fallback(
                state,
                item,
                candidate,
                reason="LiteLLM is not configured. Typed fallback is enabled.",
            )
        return

    if live_server_ok():
        render_iframe_live_interview(state, item, candidate)
        return

    # Streamlit Cloud / no helper: spoken Live still works in-process via LiteLLM.
    st.caption(
        "Live helper not at "
        f"`{voice_settings.live_server_base}` — using **in-app spoken Live** "
        "(Streamlit Cloud compatible)."
    )
    render_native_live_interview(state, item, candidate)


def render_question(state) -> None:
    """Present whatever the orchestrator chose, and collect an answer."""
    nxt = engine(active_bank_id()).next_item(state)
    if nxt is None:
        return
    item, candidate = nxt
    variable_state = state.variables[candidate.variable]

    st.markdown(f"### {candidate.variable} · {item.modality.upper()}")
    st.caption(
        f"Chosen because this competency has the lowest precision index "
        f"({variables_module.certainty(variable_state):.1f}). Item selected by "
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
        return

    if item.modality in ("open", "voice"):
        # Without this branch a `voice` item falls through to the code renderer below and
        # is presented as a programming exercise with a solution box.
        st.markdown(payload.get("prompt") or payload.get("question", ""))
        if payload.get("answer_format"):
            st.info(payload["answer_format"])
        render_live_interview(state, item, candidate)
        return

    # code
    st.markdown(payload.get("prompt", ""))
    starter = payload.get("starter_code") or f"def {payload.get('function_name', 'solve')}():\n    ...\n"
    code = st.text_area("Your solution", value=starter, height=260, key=f"code_{item.item_id}")
    st.caption(
        "Run in a sandbox against the question's tests. Test results own functional "
        "correctness; the model only judges quality (code rubrics in backend/app/data/rubrics)."
    )
    render_trial_runs(item, code)
    if st.button("Submit solution", type="primary"):
        with st.spinner("Running your code in the sandbox…"):
            record(state, item, candidate, code)
        st.rerun()


def render_trial_runs(item, code: str) -> None:
    """Let the candidate run the example cases before committing to a submission.

    Deliberately separated from submitting, and the button says so: the graded run is the
    one that moves the estimate, and a candidate must never be unsure which button they
    just pressed.
    """
    budget = settings.code_trial_runs_per_question
    if budget <= 0:
        return

    used = st.session_state.setdefault("trials", {}).get(item.item_id, 0)
    remaining = budget - used
    question = GraderAgent.as_code_question(item)
    examples = trial.public_tests(question)

    left, right = st.columns([1, 3])
    if left.button(
        f"Run example cases ({remaining} left)",
        disabled=remaining <= 0 or not examples,
        key=f"trial_{item.item_id}",
    ):
        with st.spinner("Running the example cases…"):
            result = trial.trial_run(question, code)
        st.session_state["trials"][item.item_id] = used + 1
        st.session_state[f"trial_result_{item.item_id}"] = result
        st.rerun()

    right.caption(
        f"{len(examples)} example case(s), and only these — the rest are hidden, which is "
        "what stops a solution being tuned to the examples. Running them changes no "
        "estimate and is not recorded as an answer."
        if examples else "This question publishes no example cases."
    )

    result = st.session_state.get(f"trial_result_{item.item_id}")
    if result is None:
        return
    if not result.available:
        st.warning(result.error_message)
        return
    if result.compiled is False:
        st.error(f"Your code did not compile — {result.error_message}")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "case": case.test_id,
                    "passed": "✅" if case.passed else "❌",
                    "arguments": repr(case.arguments)[:120],
                    "expected": repr(case.expected)[:120],
                    "what happened": case.detail or "—",
                }
                for case in result.cases
            ]
        ),
        hide_index=True, **WIDE,
    )
    st.caption(f"{result.passed} of {result.total} example cases pass.")


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
        elif last["modality"] in ("open", "voice"):
            detail = last.get("detail") or {}
            columns = st.columns(3)
            columns[0].metric("Eval confidence", f"{float(detail.get('evaluation_confidence') or 0):.2f}")
            columns[1].metric("Degraded", str(detail.get("degraded")))
            columns[2].metric("Status", str(detail.get("outcome_status") or "—"))
            if detail.get("rationale"):
                st.caption(detail["rationale"])
            if last.get("flags"):
                st.caption("Flags: " + ", ".join(last["flags"]))
            preview = detail.get("transcript_preview")
            if preview:
                with st.expander("Transcript preview"):
                    st.write(preview)
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
        with observability.session(
            state.session_id,
            track=st.session_state.get("competencies"),
            stage="fill_queue",
            items_administered=state.items_administered,
        ):
            state = run_async(
                engine(active_bank_id()).fill_queue(
                    state, use_llm=use_llm(), rng=np.random.default_rng(seed)
                )
            )
        state = engine(active_bank_id()).ensure_presenting(state)
        st.session_state["run"] = state

        stop, reason = engine(active_bank_id()).should_stop(state)
        if stop:
            st.session_state.update(finished=True, stop_reason=reason)
            session_dump.record_session(state, active_bank_id(), stop_reason=reason)
            # The session is over; push whatever is still buffered rather than waiting for
            # the interval flush that a stopped Streamlit script may never reach.
            observability.flush()

    state = st.session_state["run"]
    finished = st.session_state["finished"]

    # --- sidebar
    st.sidebar.title("Session")
    st.sidebar.caption(f"`{state.session_id}`")
    if st.session_state.get("competencies"):
        names = st.session_state.get("competency_names", {})
        labels = [
            f"**{code}** · {names.get(code, code)}"
            for code in st.session_state["competencies"]
        ]
        st.sidebar.caption("Competencies: " + ", ".join(labels))
    st.sidebar.metric("Questions answered", state.items_administered)
    open_count = len(state.open_variables)
    st.sidebar.metric("Competencies open", f"{open_count} of {len(state.variables)}")
    for variable, variable_state in sorted(state.variables.items()):
        certainty = variables_module.certainty(variable_state)
        st.sidebar.progress(
            min(certainty / 100.0, 1.0),
            text=f"{variable} — precision {certainty:.0f}"
                 + (" ✓" if variable_state.finalised else ""),
        )
    st.sidebar.markdown("---")
    with st.sidebar.expander("Tester options — locked for this run"):
        render_tester_options(st.sidebar, locked=True)
    if st.sidebar.button("End session"):
        st.session_state.update(finished=True, stop_reason="ended_by_tester")
        st.rerun()
    if st.sidebar.button("Start over"):
        for key in (
            "run",
            "steps",
            "finished",
            "stop_reason",
            "pending",
            "last_graded",
            "live_room_id",
            "live_active_item",
            "last_audio_error",
            "trials",
        ):
            st.session_state.pop(key, None)
        st.rerun()

    # --- main
    if finished:
        report = engine(active_bank_id()).summarise(state, st.session_state["stop_reason"])
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
                        "precision index": f"{v.certainty_pct:.1f}",
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
            "result, and the precision-index column says how far short the SE fell."
        )
    else:
        render_last_result()
        render_question(state)

    st.markdown("---")
    tabs = st.tabs(
        [
            "Queue",
            "Mathematics",
            "DAG",
            "Trajectory",
            "Engine log",
            "Tracebook",
            "Diagnostics",
        ]
    )
    with tabs[0]:
        render_queue(state)
    with tabs[1]:
        render_mathematics()
    with tabs[2]:
        render_graph_dag(state)
    with tabs[3]:
        render_trajectory(state)
    with tabs[4]:
        render_logs()
    with tabs[5]:
        render_tracebook()
    with tabs[6]:
        render_diagnostics()


# --- entry ------------------------------------------------------------------
if "run" not in st.session_state:
    render_setup()
else:
    render_assessment()
