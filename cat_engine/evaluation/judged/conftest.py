"""Lock 3: nothing in this package runs without an explicit, credentialled opt-in."""

import os

import pytest


@pytest.fixture(autouse=True)
def _require_explicit_live_llm_optin():
    if os.environ.get("EVAL_LIVE_LLM") != "1":
        pytest.skip("judged metrics need EVAL_LIVE_LLM=1 (they make billed model calls)")
    key = os.environ.get("LITELLM_API_KEY", "")
    if not key or key == "placeholder":
        pytest.skip("judged metrics need a real LITELLM_API_KEY, not the test placeholder")
