"""One error shape, so a client has one error path.

FastAPI's default body is `{"detail": ...}`, where `detail` is a string for an
`HTTPException` and a list of objects for a validation failure. A client that wants to
show the user what went wrong therefore has to branch on the TYPE of a field, which is the
kind of thing that works until the first 422 in production.

`ErrorResponse` adds a stable `code` beside the human-readable `detail`. `detail` is for a
person; `code` is for a client's `if`.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class ServiceError(HTTPException):
    """An `HTTPException` that also carries a greppable code."""

    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(status_code=status_code, detail=detail)
        self.code = code


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http_error(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": str(exc.detail),
                "code": getattr(exc, "code", "") or f"http_{exc.status_code}",
            },
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "detail": "; ".join(
                    f"{'.'.join(str(p) for p in error['loc'])}: {error['msg']}"
                    for error in exc.errors()
                )
                or "request failed validation",
                "code": "request_invalid",
            },
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        # The message is deliberately not the exception's. An unhandled error can carry a
        # file path, a query or a fragment of a bank, and this response may reach a
        # candidate's browser. The log gets everything; the client gets a code.
        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "internal error", "code": "internal_error"},
        )
