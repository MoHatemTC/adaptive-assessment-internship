from __future__ import annotations

import pytest

from app.services.voice_live.gemini_live import LiveInterviewResult
from app.services.voice_live.streamlit_live import StreamlitLiteLLMLiveBridge


def test_finish_is_idempotent_after_transport_has_closed() -> None:
    bridge = object.__new__(StreamlitLiteLLMLiveBridge)
    result = LiveInterviewResult(
        item_id="open_py_001",
        transcript="A list is mutable and a tuple is immutable.",
    )
    bridge._session = None
    bridge._finished_result = result

    assert bridge.finish() is result
    assert bridge.finish() is result


def test_finish_without_session_or_cached_result_still_rejects() -> None:
    bridge = object.__new__(StreamlitLiteLLMLiveBridge)
    bridge._session = None
    bridge._finished_result = None

    with pytest.raises(RuntimeError, match="no active interview"):
        bridge.finish()
