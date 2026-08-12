"""Shared LiteLLM HTTP clients — SSL verify is configurable.

The gateway is often reached by IP (`https://3.75.224.129/...`) with a certificate
whose SAN does not match that host. Set `LITELLM_SSL_VERIFY=false` in that case
(equivalent to `curl --insecure`).

Keepalive is disabled for this gateway: exported Langfuse traces showed first attempts
failing in 1-4 ms after idle periods, followed by successful retries. That signature is
a dead pooled connection, not gateway unavailability. A fresh TLS connection costs less
than a failed generation + retry and removes misleading ERROR spans.
"""

from __future__ import annotations

import logging

import httpx

from cat_engine.engine.config.settings import settings

_sync: httpx.Client | None = None
_async: httpx.AsyncClient | None = None
logger = logging.getLogger(__name__)

# Fresh connections beat stale keepalives against this proxy. The gateway dominates request
# latency anyway, so connection reuse is not worth intermittent instant failures.
_LIMITS = httpx.Limits(
    max_connections=20,
    max_keepalive_connections=0,
)


def _timeout() -> httpx.Timeout:
    # Connect failures were instant "Connection error" in Langfuse; give the handshake
    # its own budget so a slow proxy is not confused with a dead pool entry.
    return httpx.Timeout(
        settings.litellm_timeout_seconds,
        connect=min(20.0, float(settings.litellm_timeout_seconds)),
    )


def sync_http() -> httpx.Client:
    global _sync
    if _sync is None:
        _sync = httpx.Client(
            verify=settings.litellm_ssl_verify,
            timeout=_timeout(),
            limits=_LIMITS,
        )
    return _sync


def async_http() -> httpx.AsyncClient:
    global _async
    if _async is None:
        _async = httpx.AsyncClient(
            verify=settings.litellm_ssl_verify,
            timeout=_timeout(),
            limits=_LIMITS,
        )
    return _async


def reset_http() -> None:
    """Drop cached clients after a settings change (tests / Streamlit restart)."""
    global _sync, _async
    if _sync is not None:
        try:
            _sync.close()
        except Exception:
            logger.debug(
                "could not close cached synchronous LiteLLM client", exc_info=True
            )
    if _async is not None:
        try:
            # AsyncClient.close is async; best-effort sync drop for restart paths.
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is None:
                asyncio.run(_async.aclose())
            else:
                loop.create_task(_async.aclose())
        except Exception:
            logger.debug(
                "could not close cached asynchronous LiteLLM client", exc_info=True
            )
    _sync = None
    _async = None
