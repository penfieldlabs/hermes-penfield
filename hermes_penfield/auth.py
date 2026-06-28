# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Authentication for the Penfield API.

Two flows, both returning the same ``{access_token, refresh_token, ...}``
shape so the client does not care which produced the tokens:

* **API key exchange** (``POST /api/v2/auth/token``): the verified,
  testable default. Sends the API key as a bearer header; the API returns
  a JWT + refresh token. Used in CI / headless and during development.
* **OAuth 2.1 device code** (RFC 8628): for interactive CLI login.
  Discovers endpoints dynamically, optionally uses dynamic client
  registration, polls the token endpoint until the user authorizes. Not
  fully exercised against the live dev IdP during v0.1.0 because it needs
  a browser step — implemented to the documented contract and unit-tested
  with mocked HTTP. See ADR-0006.

Tokens are cached to ``{hermes_home}/penfield/tokens.json`` with mode
0o600. Refresh uses the documented rotation behavior (RFC 9700): each
refresh invalidates the old refresh token and returns a new one.
"""

from __future__ import annotations

import contextlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hermes_penfield.constants import REFRESH_SKEW_SECONDS, USER_AGENT
from hermes_penfield.exceptions import AuthError, TokenExpiredError

if TYPE_CHECKING:
    from hermes_penfield.config import PenfieldConfig

TOKEN_FILE_MODE = 0o600


@dataclass
class TokenSet:
    """A cached token pair with its expiry.

    ``expires_at`` is an absolute ``time.time()`` epoch second, derived from
    the response ``expires_in`` at storage time (not from JWT claims, so we
    don't depend on a JWT library).
    """

    access_token: str
    refresh_token: str
    expires_at: float
    token_type: str = "Bearer"
    tenant_id: str | None = None

    def expired(self, *, skew: float = REFRESH_SKEW_SECONDS) -> bool:
        """True if the access token is within ``skew`` seconds of expiry."""
        return time.time() >= (self.expires_at - skew)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the token set to a JSON-friendly dict."""
        d: dict[str, Any] = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }
        if self.tenant_id is not None:
            d["tenant_id"] = self.tenant_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TokenSet:
        """Reconstruct a :class:`TokenSet` from a cached dict."""
        return cls(
            access_token=str(d["access_token"]),
            refresh_token=str(d["refresh_token"]),
            expires_at=float(d["expires_at"]),
            token_type=str(d.get("token_type", "Bearer")),
            tenant_id=str(d["tenant_id"]) if d.get("tenant_id") else None,
        )


class PenfieldAuth:
    """Owns the current token set and the refresh-exchange logic.

    The :class:`~hermes_penfield.client.PenfieldClient` calls
    :meth:`get_header` before every request and :meth:`refresh` on a 401.
    """

    def __init__(
        self,
        config: PenfieldConfig,
        hermes_home: str | Path | None = None,
        *,
        tokens: TokenSet | None = None,
    ) -> None:
        """Bind config, optional home dir, and optional pre-loaded tokens."""
        self._config = config
        self._hermes_home = Path(hermes_home) if hermes_home else None
        self._tokens: TokenSet | None = tokens
        self._api_key = config.api_key
        # If no tokens were passed but a cache exists, load lazily on first use.
        if self._tokens is None and self._hermes_home is not None:
            cached = self._load_cached()
            if cached is not None:
                self._tokens = cached

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    @property
    def has_api_key(self) -> bool:
        """True if an API key is configured."""
        return bool(self._api_key)

    def is_authenticated(self) -> bool:
        """True if we hold a usable access token (network not touched)."""
        return self._tokens is not None

    def get_header(self) -> str:
        """Return an ``Authorization`` header value (``Bearer <access>``)."""
        if self._tokens is None:
            self.authenticate()
        assert self._tokens is not None  # narrowed by authenticate()
        return f"{self._tokens.token_type} {self._tokens.access_token}"

    def authenticate(self) -> TokenSet:
        """Obtain a token set, refreshing or exchanging as needed.

        Priority:

        1. Valid cached access token -> return it.
        2. Refresh token present -> attempt refresh (handles rotation).
        3. API key present -> key exchange.
        4. Otherwise -> :class:`AuthError` (caller must run device flow).
        """
        if self._tokens is not None and not self._tokens.expired():
            return self._tokens
        if self._tokens is not None and self._tokens.refresh_token:
            try:
                return self._refresh(self._tokens.refresh_token)
            except TokenExpiredError:
                # Refresh token dead; fall through to key exchange if possible.
                self._tokens = None
        if self._api_key:
            return self._exchange_api_key(self._api_key)
        raise AuthError(
            "no credentials available: run `hermes penfield login` or set PENFIELD_API_KEY"
        )

    def refresh(self) -> TokenSet:
        """Force a refresh; called by the client on a 401.

        RFC 9700 rotates refresh tokens, so a cached refresh token can become
        permanently invalid (process restart mid-rotation, clock skew, server
        side revoke). When that happens and an API key is configured, fall back
        to a fresh key exchange rather than stranding the provider until a
        manual logout.
        """
        if self._tokens is None or not self._tokens.refresh_token:
            if self._api_key:
                return self._exchange_api_key(self._api_key)
            raise TokenExpiredError("no refresh token and no API key; re-authenticate")
        try:
            return self._refresh(self._tokens.refresh_token)
        except TokenExpiredError:
            # Refresh token dead (rotated/expired/revoked). Fall back to a
            # fresh API-key exchange if we have one; otherwise propagate.
            if self._api_key:
                self._tokens = None
                return self._exchange_api_key(self._api_key)
            raise

    def logout(self) -> None:
        """Drop cached tokens from memory and disk."""
        self._tokens = None
        if self._hermes_home is not None:
            path = self._config.token_path(self._hermes_home)
            with contextlib.suppress(OSError):
                path.unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # API key exchange — the verified default path
    # ------------------------------------------------------------------
    def _exchange_api_key(self, api_key: str) -> TokenSet:
        url = self._url("/auth/token")
        body = b""  # empty body; key is in the Authorization header
        resp = _http("POST", url, body=body, headers=_default_headers(api_key))
        # _http already unwrapped the envelope; resp is the token object.
        ts = self._tokens_from_exchange(resp)
        self._tokens = ts
        self._persist()
        return ts

    def _refresh(self, refresh_token: str) -> TokenSet:
        """Refresh an API-key-derived token via /auth/refresh.

        Note: OAuth-derived tokens refresh via the OAuth token endpoint
        (see :meth:`device_code_flow`). For the v0.1.0 verified path every
        token is API-key-derived, so /auth/refresh is correct here.
        """
        url = self._url("/auth/refresh")
        body = json.dumps({"refresh_token": refresh_token}).encode()
        try:
            resp = _http("POST", url, body=body, headers=_default_headers())
        except AuthError as exc:
            # invalid_grant on refresh means the refresh token is dead.
            raise TokenExpiredError(str(exc)) from exc
        ts = self._tokens_from_exchange(resp)
        self._tokens = ts
        self._persist()
        return ts

    # ------------------------------------------------------------------
    # OAuth 2.1 device code flow (RFC 8628) — interactive login
    # ------------------------------------------------------------------
    def device_code_flow(
        self,
        *,
        client_id: str | None = None,
        scope: str = "read write offline_access",
        poll_interval_override: float | None = None,
    ) -> TokenSet:
        """Run the OAuth device code flow.

        Performs dynamic client registration when ``client_id`` is not
        supplied, then requests a device code, prints the verification URL,
        and polls until the user authorizes or the code expires.

        Args:
            client_id: Pre-registered client id. If absent, registers one.
            scope: OAuth scopes; ``offline_access`` is required for refresh.
            poll_interval_override: Tests inject this to avoid real sleeps.

        Returns:
            The token set obtained on success.

        Raises:
            AuthError: If the user denies, the code expires, or registration
                fails.
        """
        discovery = self._discovery()
        cid = client_id or self._register_client(discovery, scope)

        device_auth_url = discovery["device_authorization_endpoint"]
        token_url = discovery["token_endpoint"]

        data = urllib.parse.urlencode({"client_id": cid, "scope": scope}).encode()
        resp = _http(
            "POST",
            device_auth_url,
            body=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        device_code = resp["device_code"]
        user_code = resp.get("user_code", "")
        verification_uri = resp.get("verification_uri_complete") or resp.get(
            "verification_uri", ""
        )
        interval = float(resp.get("interval", 5))
        expires_in = float(resp.get("expires_in", 900))
        if poll_interval_override is not None:
            interval = poll_interval_override

        if verification_uri:
            # Surface to the user via stdout for the CLI; tests ignore this.
            print(
                f"To authorize, visit: {verification_uri}"
                + (f" (code: {user_code})" if user_code else "")
            )

        deadline = time.time() + expires_in
        while time.time() < deadline:
            form = urllib.parse.urlencode(
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": cid,
                }
            ).encode()
            try:
                token_resp = _http(
                    "POST",
                    token_url,
                    body=form,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    raw_error=True,
                )
            except _OAuthPendingError:
                time.sleep(interval)
                continue
            except _OAuthSlowDownError:
                interval += 5
                time.sleep(interval)
                continue
            ts = self._tokens_from_oauth(token_resp)
            self._tokens = ts
            self._persist()
            return ts
        raise AuthError("device code expired before user authorized")

    def _discovery(self) -> dict[str, str]:
        """Fetch OAuth authorization-server metadata from the API host.

        The docs direct clients to ``https://api(-dev).penfield.app/.well-known/
        oauth-authorization-server``. We resolve the well-known URL from the
        configured api_base (stripping the /api/v2 prefix).
        """
        base = self._config.api_base.removesuffix("/api/v2").removesuffix(API_PREFIX)
        well_known = f"{base}/.well-known/oauth-authorization-server"
        return _http("GET", well_known, headers=_default_headers())

    def _register_client(self, discovery: dict[str, str], scope: str) -> str:
        reg_url = discovery.get("registration_endpoint")
        if not reg_url:
            raise AuthError(
                "no client_id given and server offers no dynamic registration; provide a client_id"
            )
        body = json.dumps(
            {
                "client_name": "hermes-penfield",
                "redirect_uris": ["http://localhost:0/callback"],
                "grant_types": [
                    "urn:ietf:params:oauth:grant-type:device_code",
                    "refresh_token",
                ],
                "token_endpoint_auth_method": "none",
                "scope": scope,
            }
        ).encode()
        resp = _http(
            "POST",
            reg_url,
            body=body,
            headers=_default_headers(),
        )
        return str(resp["client_id"])

    # ------------------------------------------------------------------
    # Token shape normalization
    # ------------------------------------------------------------------
    @staticmethod
    def _tokens_from_exchange(data: dict[str, Any]) -> TokenSet:
        return TokenSet(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=time.time() + float(data.get("expires_in", 86400)),
            token_type=str(data.get("token_type", "Bearer")),
            tenant_id=str(data["tenant_id"]) if data.get("tenant_id") else None,
        )

    @staticmethod
    def _tokens_from_oauth(data: dict[str, Any]) -> TokenSet:
        return TokenSet(
            access_token=str(data["access_token"]),
            refresh_token=str(data["refresh_token"]),
            expires_at=time.time() + float(data.get("expires_in", 86400)),
            token_type=str(data.get("token_type", "Bearer")),
        )

    # ------------------------------------------------------------------
    # Cache persistence
    # ------------------------------------------------------------------
    def _persist(self) -> None:
        if self._hermes_home is None or self._tokens is None:
            return
        path = self._config.token_path(self._hermes_home)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._tokens.to_dict()))
        with contextlib.suppress(OSError):
            path.chmod(TOKEN_FILE_MODE)

    def _load_cached(self) -> TokenSet | None:
        path = self._config.token_path(self._hermes_home)  # type: ignore[arg-type]
        if not path.exists():
            return None
        try:
            return TokenSet.from_dict(json.loads(path.read_text()))
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return None

    def _url(self, path: str) -> str:
        return f"{self._config.api_base}{path}"


# ---------------------------------------------------------------------------
# Internal: minimal stdlib HTTP for the auth module.
# ---------------------------------------------------------------------------
# Auth has slightly different needs from the client (form-encoded bodies,
# raw OAuth error envelopes) so it ships its own small helper rather than
# importing the client (which would create a circular dependency).

from hermes_penfield.constants import API_PREFIX  # noqa: E402  (local to module)


class _OAuthPendingError(Exception):
    """RFC 8628 ``authorization_pending`` — keep polling."""


class _OAuthSlowDownError(Exception):
    """RFC 8628 ``slow_down`` — increase interval."""


def _default_headers(auth_value: str | None = None) -> dict[str, str]:
    """Build the default header set, optionally adding a bearer value."""
    h: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": USER_AGENT,
    }
    if auth_value:
        bearer = auth_value if auth_value.startswith("Bearer ") else f"Bearer {auth_value}"
        h["Authorization"] = bearer
    return h


def _http(
    method: str,
    url: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str],
    raw_error: bool = False,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """Send a request and return the parsed JSON envelope ``data`` field.

    For OAuth token endpoints the success body is the token object itself
    (no ``status`` envelope), so we return the whole object in that case
    (``raw_error=True`` path).

    Raises:
        AuthError: On transport failure or non-2xx.
    """
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode(errors="replace")
        finally:
            pass
        # OAuth device-flow error envelopes: {"error": "authorization_pending"}
        if raw_error:
            try:
                err = json.loads(detail)
            except (json.JSONDecodeError, ValueError):
                err = {}
            err_code = str(err.get("error", ""))
            if err_code == "authorization_pending":
                raise _OAuthPendingError from exc
            if err_code == "slow_down":
                raise _OAuthSlowDownError from exc
            if err_code in {"expired_token", "access_denied"}:
                raise AuthError(f"device flow failed: {err_code}") from exc
            raise AuthError(f"OAuth error: {err_code or exc.code}") from exc
        raise AuthError(f"auth request failed ({exc.code}): {detail[:200]}") from exc
    except urllib.error.URLError as exc:
        raise AuthError(f"auth transport error: {exc}") from exc

    if not payload:
        return {}
    try:
        parsed = json.loads(payload.decode())
    except (json.JSONDecodeError, ValueError) as exc:
        raise AuthError("auth response was not JSON") from exc
    # OAuth token endpoint returns the token object directly.
    if raw_error or "data" not in parsed:
        return parsed
    if parsed.get("status") != "success":
        raise AuthError(f"auth response not success: {parsed!r}")
    return parsed["data"]
