"""Standard error envelope, exception hierarchy, handlers, trace-id middleware.

Every error response uses the envelope:

    {"error": {"code": str, "message": str, "details": list, "traceId": str}}

A per-request trace id (uuid) is assigned by TraceIdMiddleware, echoed in the
`X-Trace-Id` response header, and stored in a contextvar that audit/events
read as the correlation id.
"""

import contextvars
import uuid

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "stp_trace_id", default=None
)


def current_trace_id() -> str | None:
    """Trace id of the in-flight request (None outside request context)."""
    return trace_id_var.get()


class ApiError(Exception):
    status_code: int = 500
    code: str = "INTERNAL_ERROR"
    default_message: str = "internal server error"

    def __init__(self, message: str | None = None, details: list | None = None):
        self.message = message or self.default_message
        self.details = details or []
        super().__init__(self.message)


class ValidationError(ApiError):
    status_code = 400
    code = "VALIDATION_ERROR"
    default_message = "validation error"


class Unauthenticated(ApiError):
    status_code = 401
    code = "UNAUTHENTICATED"
    default_message = "authentication required"


class Forbidden(ApiError):
    status_code = 403
    code = "FORBIDDEN"
    default_message = "access denied"


class NotFound(ApiError):
    status_code = 404
    code = "NOT_FOUND"
    default_message = "resource not found"


class StateConflict(ApiError):
    status_code = 409
    code = "STATE_CONFLICT"
    default_message = "state conflict"


class BusinessRuleViolation(ApiError):
    status_code = 422
    code = "BUSINESS_RULE_VIOLATION"
    default_message = "business rule violation"


class RateLimited(ApiError):
    status_code = 429
    code = "RATE_LIMITED"
    default_message = "rate limit exceeded"


class DependencyUnavailable(ApiError):
    status_code = 503
    code = "DEPENDENCY_UNAVAILABLE"
    default_message = "dependency unavailable"


class InternalError(ApiError):
    status_code = 500
    code = "INTERNAL_ERROR"
    default_message = "internal server error"


def error_envelope(code: str, message: str, details: list | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details or [],
            "traceId": current_trace_id(),
        }
    }


async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(exc.code, exc.message, exc.details),
    )


async def _validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=error_envelope(
            "VALIDATION_ERROR",
            "request validation failed",
            jsonable_encoder(exc.errors()),
        ),
    )


_HTTP_CODE_MAP = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "STATE_CONFLICT",
    422: "BUSINESS_RULE_VIOLATION",
    429: "RATE_LIMITED",
    503: "DEPENDENCY_UNAVAILABLE",
}


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    code = _HTTP_CODE_MAP.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content=error_envelope(code, str(exc.detail)),
    )


async def _unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=error_envelope("INTERNAL_ERROR", "internal server error"),
    )


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Assigns a trace id per request, sets X-Trace-Id, feeds the contextvar."""

    async def dispatch(self, request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or uuid.uuid4().hex
        token = trace_id_var.set(trace_id)
        try:
            try:
                response = await call_next(request)
            except Exception:
                # Last-resort 500 so the envelope + trace header always hold.
                response = JSONResponse(
                    status_code=500,
                    content=error_envelope("INTERNAL_ERROR", "internal server error"),
                )
        finally:
            trace_id_var.reset(token)
        response.headers["X-Trace-Id"] = trace_id
        return response


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_error_handler)
