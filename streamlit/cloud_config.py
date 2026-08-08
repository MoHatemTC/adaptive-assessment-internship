"""Small, testable bridge from Streamlit Cloud secrets to backend settings.

Pydantic reads environment variables when its singleton settings objects are imported.
Applying the allowlisted root secrets first makes Cloud and local ``backend/.env`` use the
same configuration path.  Existing environment variables win, which keeps shell-provided
local configuration authoritative.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping

ALLOWED_SECRET_KEYS = frozenset(
    {
        "ACTIVE_BANK",
        "ALLOW_TEXT_FALLBACK",
        "CAT_EXPOSURE_TOP_K",
        "CAT_SESSION_DUMP_DIR",
        "E2B_API_KEY",
        "LANGFUSE_ENVIRONMENT",
        "LANGFUSE_HOST",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LITELLM_API_KEY",
        "LITELLM_BASE_URL",
        "LITELLM_LIVE_PREVIEW_MODEL",
        "LITELLM_MODEL",
        "LITELLM_SSL_VERIFY",
        "LIVE_PUBLIC_BASE",
        "LIVE_SERVER_BASE",
        "ORCHESTRATOR_MODALITY_MINIMUMS",
        "STREAMLIT_TESTER_MODE",
    }
)


def apply_root_secrets(
    secrets: Mapping[str, object], environ: MutableMapping[str, str]
) -> None:
    """Copy supported scalar secrets into ``environ`` without overwriting it."""
    for key in ALLOWED_SECRET_KEYS:
        value = secrets.get(key)
        if value is None or not isinstance(value, (str, bool, int, float)):
            continue
        rendered = str(value).lower() if isinstance(value, bool) else str(value)
        environ.setdefault(key, rendered)
