"""Shared LiteLLM HTTP clients — SSL verify is configurable.

The gateway is often reached by IP (`https://3.75.224.129/...`) with a certificate
whose SAN does not match that host. Set `LITELLM_SSL_VERIFY=false` in that case
(equivalent to `curl --insecure`).
"""

from __future__ import annotations

import httpx

from app.config.settings import settings

_sync: httpx.Client | None = None
_async: httpx.AsyncClient | None = None


def sync_http() -> httpx.Client:
    global _sync
    if _sync is None:
        _sync = httpx.Client(
            verify=settings.litellm_ssl_verify,
            timeout=settings.litellm_timeout_seconds,
        )
    return _sync


def async_http() -> httpx.AsyncClient:
    global _async
    if _async is None:
        _async = httpx.AsyncClient(
            verify=settings.litellm_ssl_verify,
            timeout=settings.litellm_timeout_seconds,
        )
    return _async


def reset_http() -> None:
    """Drop cached clients after a settings change (tests / Streamlit restart)."""
    global _sync, _async
    if _sync is not None:
        _sync.close()
    _sync = None
    _async = None
