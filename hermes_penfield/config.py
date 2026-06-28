# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Configuration: environment selection, host resolution, advanced knobs.

Resolution order for the API base URL (highest priority first):

1. Explicit ``PENFIELD_URL`` env var (full base URL, e.g. for staging).
2. ``PENFIELD_ENV`` env var (``dev`` | ``prod``) -> host table.
3. ``penfield_url`` saved config value.
4. Default: production.

The same switch also selects the auth/portal/mcp host pairs so OAuth and
CLI commands talk to the right environment. See ADR-0004.
"""

from __future__ import annotations

import contextlib
import json
import os
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from hermes_penfield.constants import API_PREFIX, DEV_HOSTS, PROD_HOSTS
from hermes_penfield.exceptions import ConfigError

DEFAULT_ENV = "prod"


class Environment(str, Enum):
    """Deployment environment. ``str`` mixin so values serialize as JSON."""

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
    raw = (env_str if env_str is not None else os.environ.get("PENFIELD_ENV")) or DEFAULT_ENV
    try:
        return Environment(raw.lower())
    except ValueError as exc:
        msg = f"unknown PENFIELD_ENV {raw!r}; expected one of {[e.value for e in Environment]}"
        raise ConfigError(msg) from exc


def hosts_for(env: Environment) -> dict[str, str]:
    """Return the ``{api, auth, portal, mcp}`` host table for an environment."""
    return env.hosts


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
    # Advanced knobs (see spec section 8.2). Defaults are conservative.
    auto_recall: bool = True
    recall_limit: int = 5
    recall_threshold: float = 0.7
    mirror_builtin_writes: bool = False
    sync_turn_enabled: bool = True
    pre_compress_save: bool = True
    max_prefetch_chars: int = 2000
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
        * api key from arg -> ``PENFIELD_API_KEY`` -> saved config

        Args:
            hermes_home: Hermes home dir holding ``penfield/config.json``.
            env: Explicit environment override.
            api_key: Explicit API key override (skips env/file lookup).
            api_url_override: Explicit API base URL override.
        """
        saved: dict[str, object] = {}
        if hermes_home:
            cfg_path = _penfield_dir(hermes_home) / "config.json"
            if cfg_path.exists():
                try:
                    saved = json.loads(cfg_path.read_text())
                except (json.JSONDecodeError, OSError):
                    saved = {}

        # Environment
        env_val = (
            env or os.environ.get("PENFIELD_ENV") or str(saved.get("penfield_env", DEFAULT_ENV))
        )
        environment = resolve_environment(env_val)

        # URL override: explicit arg > env var > saved penfield_url > derived
        override = (
            api_url_override
            or os.environ.get("PENFIELD_URL")
            or str(saved.get("penfield_url", ""))
        )

        instance = cls(env=environment)
        if override:
            instance.api_base = override.rstrip("/")
        else:
            instance.reapply_hosts()

        # API key
        key = (
            api_key
            or os.environ.get("PENFIELD_API_KEY")
            or (saved.get("api_key") if isinstance(saved.get("api_key"), str) else None)
        )
        instance.api_key = key if isinstance(key, str) else None

        # Advanced knobs from saved config
        for fld in (
            "auto_recall",
            "recall_limit",
            "recall_threshold",
            "mirror_builtin_writes",
            "sync_turn_enabled",
            "pre_compress_save",
            "max_prefetch_chars",
        ):
            if fld in saved and hasattr(instance, fld):
                setattr(instance, fld, saved[fld])
        # Anything else survives in extra for forward-compat.
        known = {
            "penfield_env",
            "penfield_url",
            "api_key",
            "auth_method",
            *{
                f
                for f in (
                    "auto_recall",
                    "recall_limit",
                    "recall_threshold",
                    "mirror_builtin_writes",
                    "sync_turn_enabled",
                    "pre_compress_save",
                    "max_prefetch_chars",
                )
            },
        }
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

        The API key is *never* written by this method; secrets stay in env
        vars or the keychain. See ADR-0006 and SECURITY.md.
        """
        pd = _penfield_dir(hermes_home)
        cfg_path = pd / "config.json"
        data: dict[str, object] = {
            "penfield_env": self.env.value,
            "auto_recall": self.auto_recall,
            "recall_limit": self.recall_limit,
            "recall_threshold": self.recall_threshold,
            "mirror_builtin_writes": self.mirror_builtin_writes,
            "sync_turn_enabled": self.sync_turn_enabled,
            "pre_compress_save": self.pre_compress_save,
            "max_prefetch_chars": self.max_prefetch_chars,
        }
        # Persist a URL override if one is set (issue #3: was being dropped
        # on restart, so users who configured a custom endpoint lost it).
        if self.api_base and not self.api_base.startswith("https://api"):
            data["penfield_url"] = self.api_base
        cfg_path.write_text(json.dumps(data, indent=2))
        with contextlib.suppress(OSError):
            cfg_path.chmod(0o600)
        return cfg_path

    def token_path(self, hermes_home: str | Path) -> Path:
        """Return the path used for cached JWT tokens."""
        return _penfield_dir(hermes_home) / "tokens.json"
