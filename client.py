# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Stdlib-only HTTP client for the Penfield v2 API.

Design constraints (see ADR-0002):

* **No third-party HTTP deps.** ``urllib.request`` / ``urllib.parse`` only,
  so the plugin installs cleanly into any Python 3.10+ environment without
  dependency conflicts. This is a hard requirement from the v1.0 spec.
* **Client-side rate limiting.** Sliding 60s window at the documented RPM.
* **Bounded retries.** 429 is always retried. 5xx and transport failures are
  retried only when repeating the request cannot create a duplicate. Other
  4xx errors are never retried.
* **Safe from several threads.** The provider fetches in the background
  while the agent makes tool calls.
* **Envelope-aware.** Every successful API call returns
  ``{"status": "success", "data": ..., "meta": ...}``; this class unwraps
  ``data`` and raises typed errors on failure.
"""

from __future__ import annotations

import json
import random
import threading
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
    WRITE_ONCE_ENDPOINTS,
)
from .exceptions import (
    APIError,
    AuthError,
    AuthTransportError,
    NotFoundError,
    PenfieldError,
    PermissionDeniedError,
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
        self._rate_lock = threading.Lock()
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
        with self._rate_lock:
            now = time.monotonic()
            cutoff = now - 60.0
            self._request_times = [t for t in self._request_times if t > cutoff]
            sleep_for = 0.0
            if len(self._request_times) >= self._max_rpm:
                # Wait until the oldest request ages out of the window.
                sleep_for = max(self._request_times[0] - cutoff, 0.0)
            self._request_times.append(now + sleep_for)
        if sleep_for > 0:
            self._sleep(sleep_for)

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
        retries: int | None = None,
        repeatable: bool | None = None,
    ) -> Any:
        """Make an authenticated request and return unwrapped ``data``.

        Args:
            method: HTTP method (case-insensitive).
            path: Path relative to the API base. May be a name in
                :data:`~penfield.constants.ENDPOINTS` (e.g.
                ``"memory_create"``) or a literal path.
            body: JSON body (sent as ``application/json``).
            query: Query parameters, URL-encoded.
            timeout: Per-request timeout override, in seconds.
            retries: How many times to retry; defaults to the client's
                ``max_retries``. ``0`` means a single attempt.
            repeatable: Whether sending the request twice is harmless. When
                false, a 5xx or transport failure is not retried because the
                first attempt may have been applied. Defaults to true for
                every method except ``POST``.

        Returns:
            The ``data`` field of the success envelope.

        Raises:
            APIError: On a non-retriable failure (mapped to subclasses).
            RateLimitError: On 429 after exhausting retries.
            AuthError: If the auth header cannot be obtained. An auth server
                that does not answer is tried again like the request itself.
        """
        method = method.upper()
        url = self._build_url(self._resolve_path(method, path), query)
        body_bytes = json.dumps(body).encode() if body is not None else None
        if repeatable is None:
            repeatable = method != "POST"
        retries_left = self._max_retries if retries is None else retries
        delay = BACKOFF_BASE
        refreshed = False  # refresh at most once per request

        while True:
            self._enforce_rate_limit()
            try:
                header = self._auth.get_header()
            except AuthTransportError:
                # Signing in changes nothing at Penfield, so it is safe to
                # repeat whatever the request itself is.
                if retries_left <= 0:
                    raise
                retries_left -= 1
                self._sleep(delay)
                delay = min(delay * BACKOFF_FACTOR, BACKOFF_MAX)
                continue
            req = self._build_request(method, url, body_bytes, header)
            try:
                with urllib.request.urlopen(req, timeout=timeout or self._timeout) as resp:
                    return _unwrap(resp.read())
            except urllib.error.HTTPError as exc:
                status = exc.code
                wait = self._retry_after(exc, delay)
                error = self._classify_error(status, self._read_error(exc))
                if status == 401 and not refreshed:
                    # The token was rejected before the request was processed;
                    # get a fresh one and try once more.
                    try:
                        self._auth.refresh()
                    except AuthTransportError:
                        # The auth server did not answer. Wait and go round
                        # again if attempts remain.
                        if retries_left <= 0:
                            raise
                    else:
                        refreshed = True
                        continue
                else:
                    # A 429 was refused before it was processed, so it is
                    # always safe to repeat. A 5xx may have been applied.
                    retriable = status == 429 or (status >= 500 and repeatable)
                    if not retriable or retries_left <= 0:
                        raise error from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if not repeatable or retries_left <= 0:
                    raise APIError(f"transport error: {exc}", status=0) from exc
                wait = delay
            retries_left -= 1
            self._sleep(wait)
            delay = min(delay * BACKOFF_FACTOR, BACKOFF_MAX)

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
        timeout: float | None = None,
        retries: int | None = None,
    ) -> Any:
        """Call an endpoint by its name in :data:`ENDPOINTS`.

        Args:
            endpoint_name: Key like ``"memory_create"`` or
                ``"memory_get"``.
            body: JSON request body.
            query: Query parameters.
            path_params: Values for ``{placeholders}`` in the path.
            timeout: Per-request timeout override, in seconds.
            retries: Retry count override; ``0`` means a single attempt.
        """
        if endpoint_name not in ENDPOINTS:
            raise APIError(f"unknown endpoint {endpoint_name!r}", status=0)
        method, path = ENDPOINTS[endpoint_name]
        if path_params:
            for key, val in path_params.items():
                path = path.replace("{" + key + "}", urllib.parse.quote(str(val), safe=""))
        return self.request(
            method,
            path,
            body=body,
            query=query,
            timeout=timeout,
            retries=retries,
            repeatable=endpoint_name not in WRITE_ONCE_ENDPOINTS,
        )

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
        """Read an error body and release its connection."""
        try:
            return exc.read().decode(errors="replace")
        except OSError:
            return ""
        finally:
            exc.close()

    def _classify_error(self, status: int, detail: str) -> PenfieldError:
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
                # A validation error says which field is wrong only here.
                fields = err.get("field_errors")
                if isinstance(fields, dict) and fields:
                    reasons = "; ".join(
                        f"{name}: {why[0] if isinstance(why, list) and why else why}"
                        for name, why in fields.items()
                    )
                    message = f"{message} ({reasons})"
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
        if status == 401:
            return AuthError(f"{status}: {message}")
        if status == 403:
            return PermissionDeniedError(message, status=status, code=code, request_id=request_id)
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
