"""Maps domain exceptions to HTTP responses.

The API layer is the only place that knows about HTTP status codes; the domain and
application layers just raise semantic ``AppError`` subclasses.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AlreadyExistsError,
    AppError,
    AuthenticationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitedError,
    ValidationError,
)

_STATUS_MAP: dict[type[AppError], int] = {
    NotFoundError: status.HTTP_404_NOT_FOUND,
    AlreadyExistsError: status.HTTP_409_CONFLICT,
    ConflictError: status.HTTP_409_CONFLICT,
    ValidationError: 422,  # Unprocessable Content
    PermissionDeniedError: status.HTTP_403_FORBIDDEN,
    AuthenticationError: status.HTTP_401_UNAUTHORIZED,
    RateLimitedError: status.HTTP_429_TOO_MANY_REQUESTS,
    ExternalServiceError: status.HTTP_502_BAD_GATEWAY,
}


def _status_for(exc: AppError) -> int:
    for exc_type, code in _STATUS_MAP.items():
        if isinstance(exc, exc_type):
            return code
    return status.HTTP_400_BAD_REQUEST


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_for(exc),
            # ``detail`` duplicates ``error.message`` because the two clients read
            # different keys: vocably-admin reads ``error.code``, while the Flutter
            # client's ApiClient._extractDetail reads ``detail`` and falls back to
            # "Request failed (409)" without it. 4xx messages are user-visible copy
            # on that client, so the key it actually parses has to be there.
            content={
                "error": {"code": exc.code, "message": exc.message},
                "detail": exc.message,
            },
        )
