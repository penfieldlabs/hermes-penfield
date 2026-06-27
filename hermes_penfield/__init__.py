# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""hermes-penfield: Penfield persistent memory provider for Hermes Agent.

Connects a Hermes Agent instance to the Penfield memory API, exposing
store / search / relationship / analysis / artifact operations as agent
tools. Stdlib-only HTTP, OAuth device-code + API-key authentication.

See :doc:`../docs/adr/README` for the architectural decisions behind this
package, in particular ADR-0003 (spec vs. real API divergence).
"""

from __future__ import annotations

from typing import Any

from hermes_penfield.auth import PenfieldAuth
from hermes_penfield.client import PenfieldClient
from hermes_penfield.config import Environment, PenfieldConfig
from hermes_penfield.exceptions import PenfieldError

__all__ = [
    "Environment",
    "PenfieldAuth",
    "PenfieldClient",
    "PenfieldConfig",
    "PenfieldError",
    "__version__",
]

__version__ = "0.1.0"


def register(ctx: Any) -> None:
    """Register Penfield as a memory provider plugin.

    This is the programmatic registration entry point, matching Hermes'
    ``register(ctx) -> None`` contract (it calls
    ``ctx.register_memory_provider(instance)``). The same function is
    re-exported by the bundled ``plugin_dir/__init__.py`` shim, which is
    the path Hermes actually discovers via directory scan.

    NOTE: ``hermes-penfield`` is NOT wired via a pip entry point.
    Hermes' memory subsystem discovers providers by scanning
    ``$HERMES_HOME/plugins/<name>/`` directories only; the general plugin
    entry-point path has no ``register_memory_provider`` on its context.
    Install via ``hermes-penfield install``. See ADR-0014.

    ``ctx`` is typed ``Any`` (not the real ``PluginContext``) so this module
    stays importable without Hermes installed; the low-level stack never
    imports Hermes.
    """
    from hermes_penfield.provider import PenfieldMemoryProvider

    ctx.register_memory_provider(PenfieldMemoryProvider())
