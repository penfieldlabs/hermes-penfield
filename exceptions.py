# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Typed exceptions for the hermes-penfield client stack.

All errors raised by this package subclass :class:`PenfieldError` so
callers (provider, CLI, tests) can catch the family with one clause.
"""

from __future__ import annotations


class PenfieldError(Exception):
    """Base class for all hermes-penfield errors."""


class ConfigError(PenfieldError):
    """Raised when configuration is missing or invalid (no network)."""


class AuthError(PenfieldError):
    """Raised on authentication / token failures."""


class TokenExpiredError(AuthError):
    """Refresh token has expired or been revoked; re-auth required."""


class RateLimitError(PenfieldError):
    """Raised when the API rate limit is still exceeded after backoff."""

    def __init__(self, retry_after: float | None = None) -> None:
        """Initialize with an optional server-advertised retry-after."""
        super().__init__(f"rate limit exceeded (retry_after={retry_after})")
        self.retry_after = retry_after


class APIError(PenfieldError):
    """Non-2xx response from the Penfield API.

    Carries the parsed error code/message (``status:error`` envelope) when
    available, plus the raw status for programmatic handling.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """Initialize with message, HTTP status, and optional API error code."""
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id


class NotFoundError(APIError):
    """404 from the API (memory / relationship / artifact missing)."""


class ValidationError(APIError):
    """422 from the API (request body rejected)."""
