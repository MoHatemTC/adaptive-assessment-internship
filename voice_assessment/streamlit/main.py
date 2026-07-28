"""Streamlit tester for the standalone voice / open-ended CAT engine.

Reference: cat-engine-combined-streamlit/streamlit/main.py — same intake → queue →
answer → report loop, adapted for modality=open.

Gemini Live (gemini-3.1-flash-live-preview) is used ONLY if the candidate chooses the
voice path AND GEMINI_API_KEY is set. Picker + rubric grader always use LiteLLM.
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
# Package root only — do not put app/ on path or `import app` breaks.
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.config.settings import settings  # noqa: E402
from app.config.voice_settings import voice_settings  # noqa: E402
from app.services.orchestrator.bank import JsonUnifiedBank  # noqa: E402
from app.services.orchestrator import variables as variables_module  # noqa: E402
from app.services.voice.session import VoiceAssessmentRunner  # noqa: E402
from app.services.voice_live.transcribe import transcribe_audio_bytes  # noqa: E402
from app.schemas.voice import VoiceResponsePackage  # noqa: E402

import httpx  # noqa: E402
from urllib.parse import urlencode  # noqa: E402

st.set_page_config(page_title="Voice / open CAT — tester", layout="wide")
st.session_state.setdefault("use_llm_choice", bool(settings.litellm_api_key))
st.session_state.setdefault("rng_seed", 0)
st.session_state.setdefault("debug_logs", [])

_ST_VERSION = tuple(int(p) for p in st.__version__.split(".")[:2] if p.isdigit())
WIDE = {"width": "stretch"} if _ST_VERSION >= (1, 49) else {"use_container_width": True}


@st.cache_resource
def runner() -> VoiceAssessmentRunner:
    return VoiceAssessmentRunner(JsonUnifiedBank())


@st.cache_resource
def bank() -> JsonUnifiedBank:
    return JsonUnifiedBank()


def run_async(coro):
    return asyncio.run(coro)


def use_llm() -> bool:
    return bool(st.session_state.get("picker_uses_llm")) and bool(settings.litellm_api_key)


def log_debug(event: str, **fields) -> None:
    line = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "streamlit",
        "event": event,
        **fields,
    }
    logs = st.session_state.setdefault("debug_logs", [])
    logs.append(line)
    st.session_state["debug_logs"] = logs[-120:]


def fetch_live_debug(limit: int = 40) -> list[dict]:
    try:
        r = httpx.get(
            f"{voice_settings.live_server_base}/api/live/debug",
            params={"limit": limit},
            timeout=3.0,
            verify=False,
        )
        if r.status_code == 200:
            return list(r.json().get("logs") or [])
    except Exception as exc:  # noqa: BLE001
        return [
            {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "source": "streamlit",
                "event": "live_debug_fetch_failed",
                "error": str(exc),
            }
        ]
    return []


def merge_debug_logs() -> list[dict]:
    local = list(st.session_state.get("debug_logs") or [])
    remote = fetch_live_debug(50)
    # Deduplicate by (ts, source, event, error/room_id)
    seen: set[tuple] = set()
    merged: list[dict] = []
    for row in local + remote:
        key = (
            row.get("ts"),
            row.get("source"),
            row.get("event"),
            row.get("error"),
            row.get("room_id"),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(row)
    merged.sort(key=lambda r: r.get("ts") or "")
    return merged[-60:]


def render_debug_panel() -> None:
    if st.sidebar.button("Refresh debug"):
        st.rerun()
    if st.sidebar.button("Clear Streamlit debug"):
        st.session_state["debug_logs"] = []
        st.session_state.pop("last_audio_error", None)
        st.rerun()
    if st.sidebar.button("Clear Live-server debug"):
        try:
            httpx.delete(
                f"{voice_settings.live_server_base}/api/live/debug",
                timeout=3.0,
                verify=False,
            )
        except Exception:  # noqa: BLE001
            pass
        st.rerun()
    if st.sidebar.button("Emit test debug event"):
        log_debug("debug_probe", note="manual test from sidebar")
        st.rerun()

    logs = merge_debug_logs()
    last_err = st.session_state.get("last_audio_error")
    st.sidebar.caption(f"Debug logs: {len(logs)}")
    st.sidebar.caption(f"LiteLLM SSL verify: {settings.litellm_ssl_verify}")
    st.sidebar.caption(f"Live server: {voice_settings.live_server_base}")

    if last_err:
        st.error(f"Last audio/live error: {last_err}")

    with st.expander("Debug logs", expanded=True):
        if not logs:
            st.caption("No debug events yet. Click Refresh debug after a Live attempt.")
        else:
            for row in reversed(logs[-30:]):
                label = f"{row.get('ts', '?')} · {row.get('source', '?')} · {row.get('event', '?')}"
                with st.expander(label, expanded=bool(row.get("error"))):
                    st.json(row, expanded=False)


def render_setup() -> None:
    st.title("Voice / open-ended adaptive assessment")
    st.caption(
        "Select main competencies, self-rate each, then answer. "
        "Voice mode runs a spoken Gemini Live interview (ask → answer → probe). "
        "Picker and rubric grader use LiteLLM; Gemini Live is interviewer-only."
    )

    tracks = bank().tracks()
    coverage = bank().coverage()
    if not tracks:
        st.error("Question bank is empty.")
        return

    by_code = {t["code"]: t for t in tracks}
    chosen = st.multiselect(
        "Main competencies",
        options=[t["code"] for t in tracks],
        default=[t["code"] for t in tracks[:2]],
        format_func=lambda c: f"{c} · {by_code[c]['name']} ({by_code[c]['items']} items)",
        key="mains",
    )
    if not chosen:
        st.info("Select at least one competency.")
        return

    intake: dict[str, int] = {}
    confident: dict[str, bool] = {}
    st.markdown("#### Self-rating")
    for variable in chosen:
        left, mid, right = st.columns([2, 3, 2])
        left.markdown(f"**{variable} · {by_code[variable]['name']}**")
        left.caption(
            ", ".join(f"{n} {m}" for m, n in sorted(coverage.get(variable, {}).items()))
        )
        intake[variable] = mid.slider("Level", 1, 5, 3, key=f"lvl_{variable}")
        confident[variable] = (
            right.radio("Sure?", ["High", "Low"], index=1, key=f"cf_{variable}") == "High"
        )

    with st.expander("Tester options"):
        wants_llm = st.checkbox(
            "Let picker / rubric grader use LiteLLM",
            key="use_llm_choice",
            disabled=not settings.litellm_api_key,
        )
        if not settings.litellm_api_key:
            st.caption("No LITELLM_API_KEY — picker + grader run heuristically / deterministically.")
        live_ok = bool(voice_settings.gemini_api_key.strip())
        st.caption(
            f"Gemini Live realtime server: {voice_settings.live_server_base} — "
            + ("key set" if live_ok else "set GEMINI_API_KEY")
        )
        st.caption(
            f"Voice SE target={voice_settings.voice_se_target} · "
            f"session cap={voice_settings.voice_session_time_limit_minutes} min"
        )

    if st.button("Begin assessment", type="primary"):
        state = runner().begin(chosen, intake=intake, confidence=confident)
        st.session_state.update(
            run=state,
            steps=[],
            finished=False,
            stop_reason="",
            competencies=chosen,
            competency_names={c: by_code[c]["name"] for c in chosen},
            picker_uses_llm=wants_llm,
            last_graded=None,
            rng_seed=0,
        )
        st.rerun()


def record_text(state, item, candidate, text: str) -> None:
    seed = st.session_state["rng_seed"]
    st.session_state["rng_seed"] = seed + 1
    before = {v: s for v, s in state.variables.items()}
    with st.spinner("Grading answer…"):
        new_state, graded_voice, graded = run_async(
            runner().submit_text(state, item, text, use_llm=use_llm(), seed=seed)
        )
    _append_step_and_store(state, new_state, before, item, graded_voice, graded)


def record_package(state, item, candidate, package) -> None:
    seed = st.session_state["rng_seed"]
    st.session_state["rng_seed"] = seed + 1
    before = {v: s for v, s in state.variables.items()}
    with st.spinner("Grading live interview…"):
        new_state, graded_voice, graded = run_async(
            runner().submit_package(state, item, package, use_llm=use_llm(), seed=seed)
        )
    _append_step_and_store(state, new_state, before, item, graded_voice, graded)


def _append_step_and_store(state, new_state, before, item, graded_voice, graded) -> None:
    step = (max((r["step"] for r in st.session_state["steps"]), default=0)) + 1
    detail = graded.detail or {}
    for variable, after in sorted(new_state.variables.items()):
        if after.observations == before[variable].observations:
            continue
        was = before[variable]
        st.session_state["steps"].append(
            {
                "step": step,
                "competency": variable,
                "item": item.item_id,
                "modality": item.modality,
                "ability before": round(was.theta_hat, 4),
                "ability after": round(after.theta_hat, 4),
                "std error after": round(after.standard_error, 4),
                "confidence after": round(variables_module.certainty(after), 2),
                "answers": after.observations,
                "finalised": after.finalised,
            }
        )

    st.session_state["run"] = new_state
    st.session_state["last_graded"] = {
        "item": item.item_id,
        "flags": graded.flags,
        "detail": detail,
        "evaluation": graded_voice.evaluation.model_dump(),
    }
    st.session_state.pop("live_room_id", None)
    st.session_state.pop("live_active_item", None)


def live_server_ok() -> bool:
    try:
        r = httpx.get(f"{voice_settings.live_server_base}/health", timeout=2.0, verify=False)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False


def create_live_room(item_id: str, question: str, save_recording: bool) -> str:
    r = httpx.post(
        f"{voice_settings.live_server_base}/api/live/rooms",
        json={
            "item_id": item_id,
            "question": question,
            "save_recording": save_recording,
            "mode": "interview",
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


def render_question(state) -> None:
    nxt = runner().next_item(state)
    if nxt is None:
        return
    item, candidate = nxt
    vs = state.variables[candidate.variable]
    st.markdown(f"### {candidate.variable} · OPEN")
    st.caption(
        f"Lowest confidence ({variables_module.certainty(vs):.1f}%). "
        f"Selected by {'model' if candidate.chosen_by_llm else 'engine'} · {candidate.criterion}."
    )
    st.markdown(item.payload.get("prompt") or item.payload.get("question", ""))
    if item.payload.get("answer_format"):
        st.info(item.payload["answer_format"])

    mode = st.radio(
        "Answer mode",
        [
            "Realtime live interview",
            "Type transcript (tester)",
            "Recorded mic (testing only)",
        ],
        horizontal=True,
        key=f"mode_{item.item_id}",
    )

    if mode.startswith("Realtime"):
        render_live_interview(state, item, candidate)
        return

    if mode.startswith("Recorded"):
        render_whisper_fallback(state, item, candidate)

    text = st.text_area("Your answer / transcript", height=220, key=f"ans_{item.item_id}")
    if st.button("Submit answer", type="primary", disabled=len(text.strip()) < 12):
        record_text(state, item, candidate, text.strip())
        st.rerun()


def render_live_interview(state, item, candidate) -> None:
    if not voice_settings.gemini_api_key.strip():
        st.warning("Set GEMINI_API_KEY in voice_assessment/.env for realtime live interviews.")
        return

    if not live_server_ok():
        st.error(
            f"Realtime Live server is not running at `{voice_settings.live_server_base}`. "
            "Start it with:\n\n"
            "`uvicorn app.main:app --host 127.0.0.1 --port 8765`\n\n"
            "from the `voice_assessment` directory."
        )
        return

    st.info(
        "Realtime duplex interview: continuous mic ↔ Gemini Live interviewer. "
        "Use **Finish answer** inside the panel when done, then **Grade finished interview** here. "
        "Recording is optional and for testing only."
    )
    save_rec = st.checkbox("Save mic recording for testing", value=False, key=f"save_rec_{item.item_id}")

    room_id = st.session_state.get("live_room_id")
    if st.session_state.get("live_active_item") != item.item_id:
        room_id = None

    cols = st.columns(3)
    with cols[0]:
        if st.button("Open realtime interview", type="primary"):
            try:
                question = item.payload.get("prompt") or item.payload.get("question", "")
                room_id = create_live_room(item.item_id, question, save_rec)
                st.session_state["live_room_id"] = room_id
                st.session_state["live_active_item"] = item.item_id
                log_debug("live_room_created", room_id=room_id, item=item.item_id, save_recording=save_rec)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                log_debug("live_room_create_error", error=str(exc), traceback=traceback.format_exc(limit=6))
                st.session_state["last_audio_error"] = str(exc)
                st.rerun()
    with cols[1]:
        if st.button("Refresh status", disabled=not room_id):
            st.rerun()
    with cols[2]:
        if st.button("Grade finished interview", type="primary", disabled=not room_id):
            try:
                data = fetch_live_room(room_id)
                if data.get("status") != "finished" or not data.get("package"):
                    st.session_state["last_audio_error"] = (
                        f"Interview not finished yet (status={data.get('status')}). "
                        "Click Finish answer in the live panel first."
                    )
                    st.rerun()
                package = VoiceResponsePackage.model_validate(data["package"])
                log_debug(
                    "live_grade",
                    room_id=room_id,
                    turns=len(data.get("turns") or []),
                    transcript_chars=len(data.get("transcript") or ""),
                )
                record_package(state, item, candidate, package)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                log_debug("live_grade_error", error=str(exc), traceback=traceback.format_exc(limit=6))
                st.session_state["last_audio_error"] = str(exc)
                st.rerun()

    if not room_id:
        st.caption("Click **Open realtime interview** to start continuous talking.")
        return

    question = item.payload.get("prompt") or item.payload.get("question", "")
    qs = urlencode({"item_id": item.item_id, "question": question, "room_id": room_id})
    iframe_url = f"{voice_settings.live_server_base}/interview?{qs}"
    st.iframe(iframe_url, height=480)

    try:
        data = fetch_live_room(room_id)
        st.caption(
            f"Room `{room_id}` · status **{data.get('status')}** · turn **{data.get('turn_state', '—')}**"
        )
        if data.get("status") == "error" and data.get("error"):
            st.session_state["last_audio_error"] = data["error"]
            log_debug(
                "live_room_error_polled",
                room_id=room_id,
                error=data["error"],
                turn_state=data.get("turn_state"),
            )
            st.error(data["error"])
        if data.get("turns"):
            with st.expander("Live transcripts", expanded=True):
                for t in data["turns"]:
                    who = "Interviewer" if t.get("role") == "interviewer" else "You"
                    st.markdown(f"**{who}:** {t.get('text', '')}")
        if data.get("status") == "finished":
            st.success("Interview finished — click **Grade finished interview**.")
    except Exception as exc:  # noqa: BLE001
        st.warning(f"Could not poll room status: {exc}")
        log_debug("live_room_poll_failed", room_id=room_id, error=str(exc))

    # Always pull Live-server debug into the Streamlit view while a room is open.
    remote = fetch_live_debug(20)
    if remote:
        st.caption(f"Live-server debug events available: {len(remote)} (see Debug logs expander)")


def render_whisper_fallback(state, item, candidate) -> None:
    st.caption("Testing-only fallback: record → Whisper via LiteLLM → grade (not realtime Live).")
    audio = st.audio_input("Record your answer", key=f"whisper_mic_{item.item_id}")
    if audio is not None:
        st.audio(audio.getvalue(), format="audio/wav")
        if st.button("Transcribe and submit audio", type="primary"):
            try:
                with st.spinner(
                    f"Transcribing with {settings.litellm_transcribe_model} via LiteLLM…"
                ):
                    wav = audio.getvalue()
                    log_debug(
                        "audio_transcribe_start",
                        bytes=len(wav),
                        filename=audio.name or "answer.wav",
                        base_url=settings.litellm_base_url,
                        model=settings.litellm_transcribe_model,
                    )
                    transcript = transcribe_audio_bytes(wav, audio.name or "answer.wav")
                    log_debug(
                        "audio_transcribe_success",
                        transcript_chars=len(transcript),
                        transcript_words=len(transcript.split()),
                    )
                st.session_state.pop("last_audio_error", None)
                st.session_state[f"ans_{item.item_id}"] = transcript
                record_text(state, item, candidate, transcript.strip())
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc(limit=8)
                log_debug(
                    "audio_transcribe_error",
                    error_type=type(exc).__name__,
                    error=str(exc),
                    base_url=settings.litellm_base_url,
                    model=settings.litellm_transcribe_model,
                    ssl_verify=settings.litellm_ssl_verify,
                    traceback=tb,
                )
                st.session_state["last_audio_error"] = str(exc)
                st.rerun()


def render_assessment() -> None:
    state = st.session_state["run"]
    if not st.session_state["finished"]:
        seed = st.session_state["rng_seed"]
        st.session_state["rng_seed"] = seed + 1
        state = run_async(runner().fill_queue(state, use_llm=use_llm(), seed=seed))
        state = runner().ensure_presenting(state)
        st.session_state["run"] = state
        stop, reason = runner().should_stop(state)
        if stop:
            st.session_state.update(finished=True, stop_reason=reason)

    state = st.session_state["run"]
    finished = st.session_state["finished"]

    st.sidebar.title("Session")
    st.sidebar.caption(state.session_id)
    st.sidebar.metric("Answered", state.items_administered)
    render_debug_panel()
    for variable, vs in sorted(state.variables.items()):
        c = variables_module.certainty(vs)
        st.sidebar.progress(
            min(c / 100.0, 1.0),
            text=f"{variable} — {c:.0f}%" + (" ✓" if vs.finalised else ""),
        )
    if st.sidebar.button("End session"):
        st.session_state.update(finished=True, stop_reason="ended_by_tester")
        st.rerun()
    if st.sidebar.button("Start over"):
        for k in (
            "run",
            "steps",
            "finished",
            "stop_reason",
            "last_graded",
            "live_room_id",
            "live_active_item",
            "last_audio_error",
        ):
            st.session_state.pop(k, None)
        st.rerun()

    if finished:
        report = runner().summarise(state, st.session_state["stop_reason"])
        st.title("Assessment complete")
        st.success(
            f"Stopped: **{report.stop_reason}** after {report.items_administered} items."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "competency": v.variable,
                        "level": str(v.level) if v.observations else "—",
                        "band": v.band,
                        "ability": v.theta_hat,
                        "std error": v.standard_error,
                        "confidence": f"{v.certainty_pct:.1f}%",
                        "answers": v.observations,
                        "converged": v.converged,
                        "stop": v.stop_reason or "—",
                    }
                    for v in report.variables
                ]
            ),
            hide_index=True,
            **WIDE,
        )
    else:
        last = st.session_state.get("last_graded")
        if last:
            with st.expander(f"Previous result — {last['item']}", expanded=True):
                st.json(last["detail"], expanded=False)
                if last["flags"]:
                    st.warning(", ".join(last["flags"]))
        render_question(state)

    st.markdown("---")
    tabs = st.tabs(["Queue", "Trajectory", "Steps"])
    with tabs[0]:
        if state.presenting:
            st.caption(
                f"Being answered: {state.presenting.variable} · {state.presenting.item_id}"
            )
        if not state.queue:
            st.caption("Queue empty.")
        else:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "competency": v,
                            "item": c.item_id,
                            "modality": c.modality,
                            "criterion": c.criterion,
                            "information": c.information,
                            "by": "model" if c.chosen_by_llm else "engine",
                        }
                        for v, c in sorted(state.queue.items())
                    ]
                ),
                hide_index=True,
                **WIDE,
            )
    with tabs[1]:
        frame = pd.DataFrame(st.session_state.get("steps", []))
        if frame.empty:
            st.caption("No answers yet.")
        else:
            left, right = st.columns(2)
            left.line_chart(
                frame.pivot_table(
                    index="step", columns="competency", values="ability after", aggfunc="last"
                ).ffill()
            )
            right.line_chart(
                frame.pivot_table(
                    index="step", columns="competency", values="confidence after", aggfunc="last"
                ).ffill()
            )
    with tabs[2]:
        st.dataframe(pd.DataFrame(st.session_state.get("steps", [])), hide_index=True, **WIDE)


if "run" not in st.session_state:
    render_setup()
else:
    render_assessment()
