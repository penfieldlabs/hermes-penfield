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

    Hermes resolves the ``hermes_agent.plugins`` entry point to this
    callable and invokes it with a :class:`~hermes_cli.plugins.PluginContext`.
    The contract is ``register(ctx) -> None``; the plugin must call
    ``ctx.register_memory_provider(instance)``. Returning the class (the
    v0.1.0 guess) is wrong — Hermes never reads the return value, and the
    missing ``ctx`` parameter raised ``TypeError`` at load time.

    ``ctx`` is typed ``Any`` (not the real ``PluginContext``) so this module
    stays importable without Hermes installed; the low-level stack never
    imports Hermes. See ADR-0007.
    """
    from hermes_penfield.provider import PenfieldMemoryProvider

    ctx.register_memory_provider(PenfieldMemoryProvider())
