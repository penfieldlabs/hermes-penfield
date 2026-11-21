# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Configuration: environment selection, host resolution, advanced knobs.

Resolution order for the API base URL (highest priority first):

1. Explicit ``PENFIELD_URL`` setting (full base URL, e.g. for staging).
2. ``PENFIELD_ENV`` setting (``dev`` | ``prod``) -> host table.
3. ``penfield_url`` saved config value.
4. Default: production.

The same switch also selects the auth/portal/mcp host pairs so OAuth and
CLI commands talk to the right environment. See ADR-0003.

``PENFIELD_*`` settings are read through :func:`read_setting`, which asks
Hermes for the value that belongs to the active profile. One Hermes gateway
can serve several profiles from a single process, each with its own
``.env``; reading ``os.environ`` there could hand one profile another's key.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .constants import API_PREFIX, DEV_HOSTS, PROD_HOSTS
from .exceptions import ConfigError
from .transport import is_safe_address

logger = logging.getLogger("penfield.config")

DEFAULT_ENV = "prod"

#: Behaviour settings a user may put in ``penfield/config.json``, with the
#: type each must have and, for numbers, the range it is kept within.
_KNOBS: dict[str, tuple[type, float | None, float | None]] = {
    "auto_recall": (bool, None, None),
    "recall_limit": (int, 1, 100),
    "recall_threshold": (float, 0.0, 1.0),
    "max_prefetch_chars": (int, 200, 100_000),
    "pre_compress_save": (bool, None, None),
    "mirror_builtin_writes": (bool, None, None),
}
#: Connection settings stored next to them.
_CONNECTION_KEYS = frozenset({"penfield_env", "penfield_url", "api_key", "auth_method"})


def _coerce(value: object, kind: type, low: float | None, high: float | None) -> object:
    """Return ``value`` as ``kind`` within ``[low, high]``, or ``None`` if it is not one."""
    if kind is bool:
        return value if isinstance(value, bool) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if kind is int and int(value) != value:
        return None
    number = kind(value)
    if low is not None:
        number = max(kind(low), number)
    if high is not None:
        number = min(kind(high), number)
    return number


def read_setting(name: str) -> str:
    """Return the ``PENFIELD_*`` setting ``name`` for the active Hermes profile.

    Inside Hermes this resolves through ``agent.secret_scope.get_secret``:
    the profile's own ``.env`` first, then the process environment where
    Hermes allows it. Outside Hermes (unit tests, scripts) it is a plain
    environment read. An unset setting is an empty string.
    """
    try:
        from agent.secret_scope import get_secret  # type: ignore[import-not-found]
    except ImportError:
        return os.environ.get(name) or ""
    return str(get_secret(name) or "")


def default_hermes_home() -> str:
    """Return the active Hermes profile home, as Hermes itself resolves it."""
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]
    except ImportError:
        return os.environ.get("HERMES_HOME") or str(Path.home() / ".hermes")
    return str(get_hermes_home())


class Environment(StrEnum):
    """Deployment environment. A string enum, so values serialize as JSON."""

    PROD = "prod"
    DEV = "dev"

    @property
    def hosts(self) -> dict[str, str]:
        """Return the host table for this environment."""
        return PROD_HOSTS if self is Environment.PROD else DEV_HOSTS


def resolve_environment(env_str: str | None = None) -> Environment:
    """Resolve an :class:`Environment` from explicit value or env var.

    Args:
        env_str: Explicit ``"prod"``/``"dev"``; ``None`` reads ``PENFIELD_ENV``.

    Raises:
        ConfigError: If the value is not a recognized environment.
    """
    raw = (env_str if env_str is not None else read_setting("PENFIELD_ENV")) or DEFAULT_ENV
    try:
        return Environment(raw.lower())
    except ValueError as exc:
        msg = f"unknown PENFIELD_ENV {raw!r}; expected one of {[e.value for e in Environment]}"
        raise ConfigError(msg) from exc


def hosts_for(env: Environment) -> dict[str, str]:
    """Return the ``{api, auth, portal, mcp}`` host table for an environment."""
    return env.hosts


def default_api_base(env: Environment) -> str:
    """Return the API base URL that ``env`` would derive by default.

    Used to decide whether the current ``api_base`` is a custom override
    (worth persisting) or just the env-derived default (reproducible from
    ``penfield_env`` alone). Replaces a fragile ``startswith('https://api')``
    heuristic that wrongly treated custom URLs like
    ``https://api-private.example.com`` as defaults.
    """
    h = hosts_for(env)
    return f"https://{h['api']}{API_PREFIX}"


def _read_saved(hermes_home: str | Path) -> dict[str, object]:
    """Return the settings saved in ``penfield/config.json``.

    A file that is missing is the same as an empty one. So is a file that
    cannot be used (unreadable, not JSON, not a JSON object), with a warning:
    a damaged settings file must not take the memory provider down.
    """
    path = Path(hermes_home) / "penfield" / "config.json"  # reading creates nothing
    if not path.exists():
        return {}
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:  # ValueError: not JSON, or not UTF-8
        logger.warning("ignoring penfield/config.json: it could not be read (%s)", exc)
        return {}
    if not isinstance(saved, dict):
        logger.warning("ignoring penfield/config.json: it does not hold a JSON object")
        return {}
    return saved


def _penfield_dir(hermes_home: str | Path) -> Path:
    """Return ``{hermes_home}/penfield``, creating it if possible."""
    base = Path(hermes_home)
    pd = base / "penfield"
    pd.mkdir(parents=True, exist_ok=True)
    return pd


@dataclass
class PenfieldConfig:
    """Resolved runtime configuration.

    The provider holds one of these; the client reads ``api_base`` and
    ``env`` from it. All fields have safe defaults so a freshly-constructed
    config targets production but never makes a network call until auth.
    """

    env: Environment = Environment.PROD
    api_base: str = ""
    auth_url: str = ""
    portal_url: str = ""
    mcp_url: str = ""
    api_key: str | None = None
    # Behaviour settings; the README describes each one.
    auto_recall: bool = True
    recall_limit: int = 5
    # Lowest search score auto-recall accepts; the API's own relevance floor.
    recall_threshold: float = 0.25
    max_prefetch_chars: int = 3000
    pre_compress_save: bool = False
    mirror_builtin_writes: bool = False
    #: Saved keys this version does not know; kept so a save does not drop them.
    extra: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """Derive default host URLs after dataclass init."""
        if not self.api_base:
            self.reapply_hosts()

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    @classmethod
    def load(
        cls,
        hermes_home: str | Path | None = None,
        *,
        env: str | None = None,
        api_key: str | None = None,
        api_url_override: str | None = None,
    ) -> PenfieldConfig:
        """Build a config from env vars, saved JSON, and explicit overrides.

        Resolution:

        * environment from ``env`` arg -> ``PENFIELD_ENV`` -> saved config -> prod
        * ``PENFIELD_URL`` (or ``api_url_override``) overrides the derived host
        * api key from arg -> ``PENFIELD_API_KEY``

        Args:
            hermes_home: Hermes home dir holding ``penfield/config.json``.
            env: Explicit environment override.
            api_key: Explicit API key override (skips env/file lookup).
            api_url_override: Explicit API base URL override.
        """
        saved = _read_saved(hermes_home) if hermes_home else {}
        env_val = env or read_setting("PENFIELD_ENV") or str(saved.get("penfield_env") or "")
        environment = resolve_environment(env_val or DEFAULT_ENV)
        saved_url = str(saved.get("penfield_url") or "")
        override = api_url_override or read_setting("PENFIELD_URL") or saved_url
        if override and not is_safe_address(override):
            raise ConfigError(
                f"PENFIELD_URL must be an https address, got {override!r}. "
                "Plain http is accepted only for this machine (localhost)."
            )
        instance = cls(env=environment)
        if override:
            instance.api_base = override.rstrip("/")
        else:
            instance.reapply_hosts()

        # API key
        # The key lives in the profile's .env and nowhere else (SECURITY.md).
        # Whitespace around it is never part of it: a key that comes from a
        # file usually ends with a line break.
        instance.api_key = (api_key or read_setting("PENFIELD_API_KEY")).strip() or None
        if instance.api_key is None and saved.get("api_key"):
            logger.warning(
                "penfield/config.json holds an api_key, which is no longer read: put the key "
                "in the profile's .env as PENFIELD_API_KEY, or run `hermes memory setup penfield`"
            )

        # Behaviour settings from the saved config
        for name, (kind, low, high) in _KNOBS.items():
            if name not in saved:
                continue
            value = _coerce(saved[name], kind, low, high)
            if value is None:
                logger.warning(
                    "ignoring %s=%r in penfield/config.json: expected %s",
                    name,
                    saved[name],
                    "true or false" if kind is bool else "a number",
                )
            else:
                setattr(instance, name, value)
        known = _CONNECTION_KEYS | _KNOBS.keys()
        instance.extra = {k: v for k, v in saved.items() if k not in known}
        return instance

    def reapply_hosts(self) -> None:
        """Re-derive api/auth/portal/mcp URLs from the current environment.

        Call this after mutating ``env`` so the derived URLs stay coherent.
        Public so callers (e.g. the provider's save_config) don't need to
        reach into private state.
        """
        h = hosts_for(self.env)
        self.api_base = f"https://{h['api']}{API_PREFIX}"
        self.auth_url = f"https://{h['auth']}"
        self.portal_url = f"https://{h['portal']}"
        self.mcp_url = f"https://{h['mcp']}"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def save(self, hermes_home: str | Path) -> Path:
        """Persist non-secret config to ``{hermes_home}/penfield/config.json``.

        The API key is never written by this method: Hermes keeps it in the
        profile's ``.env``. See SECURITY.md.
        """
        pd = _penfield_dir(hermes_home)
        cfg_path = pd / "config.json"
        data: dict[str, object] = dict(self.extra)
        data["penfield_env"] = self.env.value
        data.update({name: getattr(self, name) for name in _KNOBS})
        # Persist the URL override only when it differs from what the
        # current env would derive. Otherwise we'd either drop a real
        # custom override on restart (the heuristic bug) or redundantly
        # persist the default. Compare against the derived default, not a
        # string-prefix guess.
        if self.api_base and self.api_base != default_api_base(self.env):
            data["penfield_url"] = self.api_base
        cfg_path.write_text(json.dumps(data, indent=2))
        with contextlib.suppress(OSError):
            cfg_path.chmod(0o600)
        return cfg_path

    def token_path(self, hermes_home: str | Path) -> Path:
        """Return the path used for cached JWT tokens."""
        return _penfield_dir(hermes_home) / "tokens.json"
