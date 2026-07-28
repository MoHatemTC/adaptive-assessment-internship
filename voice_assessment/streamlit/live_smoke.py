"""Standalone Gemini Live smoke tester — no CAT / assessment.

Run:
  cd voice_assessment && . .venv/bin/activate
  streamlit run streamlit/live_smoke.py --server.port 8503

Requires the Live FastAPI server on :8765 (scripts/run_live_server.sh).
"""

from __future__ import annotations

import asyncio
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
import streamlit as st

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)

from app.config.voice_settings import voice_settings  # noqa: E402
from app.services.voice_live.gemini_live import GeminiLiveInterviewer  # noqa: E402

st.set_page_config(page_title="Gemini Live smoke", layout="wide")

SMOKE_ITEM = "live_smoke"
SMOKE_QUESTION = (
    "This is a Gemini Live connectivity smoke test. "
    "Please greet the candidate in one short sentence and ask them to say "
    "their name, then wait for their reply."
)


def live_base() -> str:
    return voice_settings.live_server_base


def live_ok() -> bool:
    try:
        r = httpx.get(f"{live_base()}/health", timeout=2.0, verify=False)
        return r.status_code == 200 and bool(r.json().get("ok"))
    except Exception:  # noqa: BLE001
        return False


def fetch_debug(limit: int = 40) -> list[dict]:
    try:
        r = httpx.get(
            f"{live_base()}/api/live/debug",
            params={"limit": limit},
            timeout=3.0,
            verify=False,
        )
        if r.status_code == 200:
            return list(r.json().get("logs") or [])
    except Exception as exc:  # noqa: BLE001
        return [{"event": "debug_fetch_failed", "error": str(exc)}]
    return []


def fetch_room(room_id: str) -> dict:
    r = httpx.get(f"{live_base()}/api/live/rooms/{room_id}", timeout=5.0, verify=False)
    r.raise_for_status()
    return r.json()


def create_room(question: str, save_recording: bool) -> str:
    r = httpx.post(
        f"{live_base()}/api/live/rooms",
        json={
            "item_id": SMOKE_ITEM,
            "question": question,
            "save_recording": save_recording,
            "mode": "chat",
        },
        timeout=15.0,
        verify=False,
    )
    r.raise_for_status()
    return r.json()["room_id"]


async def api_smoke_speak(phrase: str) -> dict:
    """Direct Gemini Live: connect → speak → close (no browser, no CAT)."""
    from google.genai import types

    interviewer = GeminiLiveInterviewer()
    if not interviewer.configured:
        raise RuntimeError("GEMINI_API_KEY is not set in voice_assessment/.env")

    model = interviewer.google_model_name()
    client = interviewer._client_or_raise()
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=(
            "You are a smoke-test voice. Speak briefly and clearly. "
            "Do not ask questions. Stop after one short sentence."
        ),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=voice_settings.gemini_live_voice
                )
            )
        ),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True),
        ),
    )

    pcm_chunks: list[bytes] = []
    out_text: list[str] = []

    async with client.aio.live.connect(model=model, config=config) as session:
        await session.send_client_content(
            turns=types.Content(
                role="user",
                parts=[types.Part(text=f"Say exactly this aloud, then stop: {phrase}")],
            ),
            turn_complete=True,
        )

        async def _recv():
            async for msg in session.receive():
                sc = getattr(msg, "server_content", None)
                if sc is None:
                    continue
                ot = getattr(sc, "output_transcription", None)
                if ot and ot.text:
                    out_text.append(ot.text)
                model_turn = getattr(sc, "model_turn", None)
                if model_turn and model_turn.parts:
                    for part in model_turn.parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and inline.data:
                            pcm_chunks.append(inline.data)
                if getattr(sc, "turn_complete", False):
                    return

        await asyncio.wait_for(_recv(), timeout=45.0)

    pcm = b"".join(pcm_chunks)
    return {
        "model": model,
        "voice": voice_settings.gemini_live_voice,
        "transcript": " ".join(out_text).strip(),
        "audio_bytes": len(pcm),
        "audio_seconds": round(len(pcm) / (2 * 24000), 2) if pcm else 0.0,
        "ok": len(pcm) > 0,
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


st.title("Gemini Live smoke tester")
st.caption(
    "Isolated from the CAT assessment. Tests Gemini Live API + optional duplex mic panel only."
)

with st.sidebar:
    st.subheader("Config")
    st.write(f"**API key set:** `{bool(voice_settings.gemini_api_key)}`")
    st.write(f"**Model:** `{voice_settings.gemini_live_model}`")
    st.write(f"**Voice:** `{voice_settings.gemini_live_voice}`")
    st.write(f"**Gating:** `{voice_settings.gating_mode}`")
    st.write(f"**Live server:** `{live_base()}`")
    ok = live_ok()
    if ok:
        st.success("Live server /health OK")
    else:
        st.error("Live server not reachable — start `./scripts/run_live_server.sh`")
    if st.button("Refresh"):
        st.rerun()
    if st.button("Clear Live debug"):
        try:
            httpx.delete(f"{live_base()}/api/live/debug", timeout=3.0, verify=False)
        except Exception:  # noqa: BLE001
            pass
        st.rerun()

tab_api, tab_duplex, tab_debug = st.tabs(
    ["1 · API speak smoke", "2 · Duplex mic panel", "3 · Debug / trace"]
)

with tab_api:
    st.markdown(
        "Calls Google Gemini Live directly from this process (no iframe, no CAT). "
        "Confirms API key, model, and audio+transcript round-trip."
    )
    phrase = st.text_input(
        "Phrase for the model to speak",
        value="Gemini Live smoke test is working.",
    )
    if st.button("Run API smoke", type="primary"):
        try:
            with st.spinner("Connecting to Gemini Live…"):
                result = asyncio.run(api_smoke_speak(phrase))
            st.session_state["api_smoke"] = result
        except Exception as exc:  # noqa: BLE001
            st.session_state["api_smoke"] = {
                "ok": False,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=8),
            }
        st.rerun()

    result = st.session_state.get("api_smoke")
    if result:
        if result.get("ok"):
            st.success(
                f"OK · {result.get('audio_seconds')}s audio · "
                f"transcript: {result.get('transcript')!r}"
            )
        else:
            st.error(result.get("error") or "smoke failed")
        st.json(result)

with tab_duplex:
    st.markdown(
        "Uses the realtime Live server (`:8765`) + browser mic iframe — still **no CAT**. "
        "Click Start talking in the panel, speak, Finish answer when done."
    )
    if not ok:
        st.warning("Start the Live server first.")
    question = st.text_area("Interviewer question", value=SMOKE_QUESTION, height=100)
    save_rec = st.checkbox("Save mic recording (testing only)", value=False)
    cols = st.columns(3)
    with cols[0]:
        if st.button("Open duplex smoke room", type="primary", disabled=not ok):
            try:
                room_id = create_room(question.strip() or SMOKE_QUESTION, save_rec)
                st.session_state["smoke_room_id"] = room_id
                st.session_state.pop("smoke_error", None)
            except Exception as exc:  # noqa: BLE001
                st.session_state["smoke_error"] = str(exc)
            st.rerun()
    with cols[1]:
        if st.button("Clear room"):
            st.session_state.pop("smoke_room_id", None)
            st.rerun()
    with cols[2]:
        if st.button("Poll room"):
            st.rerun()

    if st.session_state.get("smoke_error"):
        st.error(st.session_state["smoke_error"])

    room_id = st.session_state.get("smoke_room_id")
    if room_id:
        qs = urlencode({"room_id": room_id, "mode": "chat"})
        st.iframe(f"{live_base()}/?{qs}", height=520)
        try:
            data = fetch_room(room_id)
            st.caption(
                f"Room `{room_id}` · status **{data.get('status')}** · "
                f"turn **{data.get('turn_state', '—')}**"
            )
            if data.get("error"):
                st.error(data["error"])
            if data.get("turns"):
                with st.expander("Transcripts", expanded=True):
                    for t in data["turns"]:
                        who = "Interviewer" if t.get("role") == "interviewer" else "You"
                        st.markdown(f"**{who}:** {t.get('text', '')}")
            if data.get("status") == "finished":
                st.success("Finished — Live duplex path OK (no grading in this app).")
                if data.get("package"):
                    st.json(data["package"])
        except Exception as exc:  # noqa: BLE001
            st.warning(f"Could not poll room: {exc}")
    else:
        st.caption("Open a duplex smoke room to embed the mic panel.")

with tab_debug:
    st.markdown(
        "Live-server event ring (`/api/live/debug`). Also run CLI: "
        "`python scripts/traced_live_smoke.py`"
    )
    auto = st.checkbox("Auto-refresh every 3s", value=False)
    if st.button("Refresh debug now") or auto:
        pass
    logs = fetch_debug(80)
    st.caption(f"{len(logs)} events")
    if logs:
        st.dataframe(
            [
                {
                    "ts": r.get("ts"),
                    "event": r.get("event"),
                    "room_id": r.get("room_id"),
                    "error": r.get("error"),
                    "text": (r.get("text") or "")[:80],
                }
                for r in logs
            ]
        )
    for row in reversed(logs):
        label = f"{row.get('ts', '?')} · {row.get('event', '?')}"
        with st.expander(label, expanded=bool(row.get("error"))):
            st.json(row)
    if auto:
        import time as _time

        _time.sleep(3)
        st.rerun()
