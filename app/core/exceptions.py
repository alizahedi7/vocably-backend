"""Domain-level exceptions.

These are framework-agnostic: the domain and application layers raise them, and the API
layer maps them to HTTP responses (see ``app.api.errors``). Nothing here imports FastAPI.
"""

from __future__ import annotations


class AppError(Exception):
    """Base class for all expected application errors."""

    #: Machine-readable, stable error code returned to clients.
    code: str = "app_error"
    #: Default human-readable message.
    message: str = "An application error occurred."

    def __init__(self, message: str | None = None) -> None:
        self.message = message or self.message
        super().__init__(self.message)


class NotFoundError(AppError):
    code = "not_found"
    message = "The requested resource was not found."


class AlreadyExistsError(AppError):
    code = "already_exists"
    message = "The resource already exists."


class ConflictError(AppError):
    """The request is well-formed but the current state forbids it.

    Distinct from :class:`AlreadyExistsError`, which is specifically a
    duplicate; this is "not while that is true" — deleting an account that
    still owns a class deck, for instance.
    """

    code = "conflict"


class ValidationError(AppError):
    code = "validation_error"
    message = "The request was invalid."


class PermissionDeniedError(AppError):
    code = "permission_denied"
    message = "You do not have access to this resource."


class AuthenticationError(AppError):
    code = "authentication_error"
    message = "Authentication failed."


class InvalidOTPError(AuthenticationError):
    code = "invalid_otp"
    message = "The verification code is invalid or has expired."


class InvalidTokenError(AuthenticationError):
    code = "invalid_token"
    message = "The token is invalid or has expired."


class RateLimitedError(AppError):
    code = "rate_limited"
    message = "Too many requests. Please wait before retrying."


class ExternalServiceError(AppError):
    code = "external_service_error"
    message = "An external service failed."


class AllProvidersUnavailableError(ExternalServiceError):
    """Every AI gateway in the failover chain refused or failed this request.

    A **subclass**, deliberately, and both halves of that matter. It carries its
    own ``code`` so a client can tell "every gateway is down" from "one call
    failed" and say something truer than "try again". And it stays an
    :class:`ExternalServiceError`, so the two places that already reason about
    provider failure keep working unchanged: ``app.api.errors`` maps it to
    **502** through an ``isinstance`` walk, and
    ``DeckBuildService._is_provider_failure`` halts a build on it — which
    becomes *more* correct here, since it now means the whole fleet is down
    rather than one gateway having a bad minute.
    """

    code = "ai_all_providers_unavailable"
    message = "The AI service is unavailable right now. Please try again shortly."
