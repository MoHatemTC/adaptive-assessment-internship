"""What every client here shares: a session, a timeout, and how failure is reported.

WHY SYNCHRONOUS

The seams these clients plug into are synchronous. `UnifiedBankRepository.shortlist`,
`GraderAgent.grade` and the propagation call are all called from `record_response`, which
is sync because the posterior update is arithmetic and making it async would colour every
caller for nothing. FastAPI runs a sync endpoint in a threadpool, so a blocking client in a
handler costs a thread, not a request.

WHY THE ERRORS ARE TYPED THE WAY THEY ARE

`ServiceUnavailable` and `ServiceRefused` are different because the caller's correct
response differs. Unavailable is retryable and, for the bank, degradable — the orchestrator
holds a version-keyed cache precisely so a registry outage costs stale parameters rather
than an abandoned assessment. Refused is the service saying "this request was wrong", and
retrying it produces the same answer more slowly.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

#: Generous for a same-cluster hop and tight enough that a hung dependency surfaces as an
#: error rather than as an assessment that stops responding. The grader overrides it: a
#: sandboxed code submission legitimately takes 30 seconds.
DEFAULT_TIMEOUT = 10.0


class ClientError(RuntimeError):
    """Anything that stopped a call from returning a usable answer."""


class ServiceUnavailable(ClientError):
    """The service could not be reached, or failed. Retryable; sometimes degradable."""


class ServiceRefused(ClientError):
    """The service answered, and the answer is that the request was wrong."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(f"{status_code} {code}: {detail}")
        self.status_code = status_code
        self.code = code
        self.detail = detail


class BaseClient:
    """One `httpx.Client` per service client, reused across calls.

    A client per call would pay TCP and TLS setup on every question, which is most of the
    budget the split is allowed to spend.
    """

    def __init__(
        self,
        base_url: str = "",
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        """`http` lets a caller supply a prepared client, and is how the tests avoid a
        socket entirely: Starlette's `TestClient` IS an `httpx.Client` wired to an ASGI app
        through a SYNCHRONOUS transport, which `httpx.ASGITransport` is not — it implements
        only `handle_async_request`, so a sync client cannot drive it.

        That matters beyond convenience. The parity suite runs an entire assessment through
        every one of these clients against the real service apps with nothing listening on
        a port, which is the only way to compare an in-process run against a service run
        without the comparison itself being flaky.
        """
        self._http = http or httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            transport=transport,
            headers={"accept": "application/json"},
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ServiceUnavailable(f"{method} {path}: {exc}") from exc

        if response.status_code >= 500:
            raise ServiceUnavailable(
                f"{method} {path}: {response.status_code} {response.text[:200]}"
            )
        if response.status_code >= 400:
            body: dict[str, Any] = {}
            try:
                body = response.json()
            except ValueError:
                pass
            raise ServiceRefused(
                response.status_code,
                str(body.get("code") or ""),
                str(body.get("detail") or response.text[:200]),
            )
        return response

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._request("POST", path, **kwargs)
