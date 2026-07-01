# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Stdlib-only HTTP client for the Penfield v2 API.

Design constraints (see ADR-0002):

* **No third-party HTTP deps.** ``urllib.request`` / ``urllib.parse`` only,
  so the plugin installs cleanly into any Python 3.10+ environment without
  dependency conflicts. This is a hard requirement from the v1.0 spec.
* **Client-side rate limiting.** Sliding 60s window at the documented RPM.
* **Exponential backoff with jitter.** Retries 429 and 5xx; never retries
  4xx client errors.
* **Envelope-aware.** Every successful API call returns
  ``{"status": "success", "data": ..., "meta": ...}``; this class unwraps
  ``data`` and raises typed errors on failure.
"""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import TYPE_CHECKING, Any

from .constants import (
    BACKOFF_BASE,
    BACKOFF_FACTOR,
    BACKOFF_MAX,
    DEFAULT_MAX_RPM,
    ENDPOINTS,
    MAX_RETRIES,
    REQUEST_TIMEOUT,
    USER_AGENT,
)
from .exceptions import (
    APIError,
    AuthError,
    NotFoundError,
    PenfieldError,
    RateLimitError,
    ValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from .auth import PenfieldAuth
    from .config import PenfieldConfig


class PenfieldClient:
    """Stateful API client.

    A single instance is held by the provider for the session lifetime.
    All requests route through :meth:`request`, which applies auth, rate
    limiting, retries, and envelope unwrapping.
    """

    def __init__(
        self,
        auth: PenfieldAuth,
        config: PenfieldConfig,
        *,
        max_rpm: int = DEFAULT_MAX_RPM,
        timeout: float = REQUEST_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        """Create a client bound to auth + config and a rate-limit window."""
        self._auth = auth
        self._config = config
        self._base = config.api_base
        self._max_rpm = max_rpm
        self._timeout = timeout
        self._max_retries = max_retries
        # Sliding-window rate limiter state.
        self._request_times: list[float] = []
        # Allow tests to inject a fake sleep without monkeypatching globals.
        self._sleep = sleep or time.sleep

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------
    def is_authenticated(self) -> bool:
        """Return whether the underlying auth holds a usable token."""
        return self._auth.is_authenticated()

    def _enforce_rate_limit(self) -> None:
        """Block until we are under the RPM cap (sliding 60s window)."""
        now = time.monotonic()
        cutoff = now - 60.0
        self._request_times = [t for t in self._request_times if t > cutoff]
        if len(self._request_times) >= self._max_rpm:
            # Sleep until the oldest request ages out of the window.
            sleep_for = self._request_times[0] - cutoff
            if sleep_for > 0:
                self._sleep(sleep_for)
        self._request_times.append(time.monotonic())

    # ------------------------------------------------------------------
    # Primary request method
    # ------------------------------------------------------------------
    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Make an authenticated request and return unwrapped ``data``.

        Args:
            method: HTTP method (case-insensitive).
            path: Path relative to the API base. May be a name in
                :data:`~penfield.constants.ENDPOINTS` (e.g.
                ``"memory_create"``) or a literal path.
            body: JSON body (sent as ``application/json``).
            query: Query parameters, URL-encoded.
            timeout: Per-request timeout override.

        Returns:
            The ``data`` field of the success envelope.

        Raises:
            APIError: On a non-retriable failure (mapped to subclasses).
            RateLimitError: On 429 after exhausting retries.
            AuthError: If the auth header cannot be obtained.
        """
        method = method.upper()
        resolved = self._resolve_path(method, path)
        url = self._build_url(resolved, query)
        body_bytes = json.dumps(body).encode() if body is not None else None

        delay = BACKOFF_BASE
        last_exc: Exception | None = None
        refreshed = False  # refresh at most once per request

        for attempt in range(self._max_retries + 1):
            self._enforce_rate_limit()
            header = self._auth.get_header()
            req = self._build_request(method, url, body_bytes, header)

            try:
                with urllib.request.urlopen(req, timeout=timeout or self._timeout) as resp:
                    raw = resp.read()
                    return _unwrap(raw)
            except urllib.error.HTTPError as exc:
                status = exc.code
                detail = self._read_error(exc)
                last_exc = self._classify_error(status, detail, exc)

                if status == 401 and not refreshed:
                    # Token expired mid-flight; refresh once and retry.
                    refreshed = True
                    self._auth.refresh()
                    continue
                if status == 429 or status >= 500:
                    if attempt == self._max_retries:
                        raise last_exc from exc
                    retry_after = self._retry_after(exc, delay)
                    self._sleep(retry_after)
                    delay = min(delay * BACKOFF_FACTOR, BACKOFF_MAX)
                    continue
                # Non-retriable client error.
                raise last_exc from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_exc = APIError(f"transport error: {exc}", status=0)
                if attempt == self._max_retries:
                    raise last_exc from exc
                self._sleep(delay)
                delay = min(delay * BACKOFF_FACTOR, BACKOFF_MAX)

        # Should be unreachable; loop exits via return or raise.
        raise last_exc  # type: ignore[misc]

    # ------------------------------------------------------------------
    # Endpoint-name convenience
    # ------------------------------------------------------------------
    def call(
        self,
        endpoint_name: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        path_params: dict[str, str] | None = None,
    ) -> Any:
        """Call an endpoint by its name in :data:`ENDPOINTS`.

        Args:
            endpoint_name: Key like ``"memory_create"`` or
                ``"memory_get"``.
            body: JSON request body.
            query: Query parameters.
            path_params: Values for ``{placeholders}`` in the path.
        """
        if endpoint_name not in ENDPOINTS:
            raise APIError(f"unknown endpoint {endpoint_name!r}", status=0)
        method, path = ENDPOINTS[endpoint_name]
        if path_params:
            for key, val in path_params.items():
                path = path.replace("{" + key + "}", urllib.parse.quote(str(val), safe=""))
        return self.request(method, path, body=body, query=query)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _resolve_path(self, method: str, path: str) -> str:
        # Accept either an endpoint name or a literal path.
        if path in ENDPOINTS:
            _, resolved = ENDPOINTS[path]
            return resolved
        return path

    def _build_url(self, path: str, query: dict[str, Any] | None) -> str:
        url = f"{self._base}{path}" if not path.startswith("http") else path
        if query:
            qs = urllib.parse.urlencode(
                {k: _query_value(v) for k, v in query.items() if v is not None}
            )
            if qs:
                url = f"{url}?{qs}"
        return url

    def _build_request(
        self,
        method: str,
        url: str,
        body_bytes: bytes | None,
        auth_header: str,
    ) -> urllib.request.Request:
        headers: dict[str, str] = {
            "Authorization": auth_header,
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if body_bytes is not None:
            headers["Content-Type"] = "application/json"
        return urllib.request.Request(url, data=body_bytes, headers=headers, method=method)

    def _read_error(self, exc: urllib.error.HTTPError) -> str:
        try:
            return exc.read().decode(errors="replace")
        except OSError:
            return ""

    def _classify_error(
        self,
        status: int,
        detail: str,
        exc: urllib.error.HTTPError,
    ) -> PenfieldError:
        """Map an HTTP error to a typed :class:`PenfieldError` subclass."""
        code: str | None = None
        request_id: str | None = None
        message = detail[:500]
        try:
            parsed = json.loads(detail)
            err = parsed.get("error", {}) if isinstance(parsed, dict) else {}
            if isinstance(err, dict):
                code = str(err.get("code")) if err.get("code") else None
                message = str(err.get("message", message))
                request_id = (
                    str(parsed.get("meta", {}).get("request_id"))
                    if isinstance(parsed.get("meta"), dict)
                    else None
                )
        except (json.JSONDecodeError, ValueError):
            pass

        if status == 404:
            return NotFoundError(message, status=status, code=code, request_id=request_id)
        if status == 422:
            return ValidationError(message, status=status, code=code, request_id=request_id)
        if status == 429:
            rl = RateLimitError(retry_after=None)
            rl.args = (message,)
            return rl
        if status in (401, 403):
            return AuthError(f"{status}: {message}")
        return APIError(message, status=status, code=code, request_id=request_id)

    @staticmethod
    def _retry_after(exc: urllib.error.HTTPError, delay: float) -> float:
        """Honor a Retry-After header when present, else backoff + jitter."""
        header = exc.headers.get("Retry-After") if exc.headers else None
        if header:
            try:
                return float(header)
            except ValueError:
                pass
        jitter = random.uniform(0, delay * 0.1)
        return delay + jitter


def _query_value(v: Any) -> str:
    """Render a query-param value to its URL-encoded string form."""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _unwrap(raw: bytes) -> Any:
    """Parse the success envelope and return ``data``.

    A 204 (DELETE) returns an empty body -> return ``None``.
    """
    if not raw:
        return None
    parsed: Any = json.loads(raw.decode())
    if isinstance(parsed, dict) and parsed.get("status") == "error":
        err = parsed.get("error", {})
        raise APIError(
            str(err.get("message", "unknown error")),
            status=200,  # envelope-level error on a 2xx is rare but possible
            code=str(err.get("code")) if err.get("code") else None,
            request_id=(
                str(parsed.get("meta", {}).get("request_id"))
                if isinstance(parsed.get("meta"), dict)
                else None
            ),
        )
    if isinstance(parsed, dict) and "data" in parsed:
        return parsed["data"]
    return parsed
