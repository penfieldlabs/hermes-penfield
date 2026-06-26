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


def register() -> type:
    """Return the provider class for Hermes plugin discovery.

    Hermes loads providers via the ``hermes_agent.plugins`` entry point,
    which must resolve to a callable that returns the provider class.
    Importing :class:`~hermes_penfield.provider.PenfieldMemoryProvider` at
    module top-level would pull the Hermes ``agent`` package into the
    import graph even when it is absent (e.g. during unit tests of the
    client). Keeping this lazy lets the low-level modules stay importable
    without Hermes installed.
    """
    from hermes_penfield.provider import PenfieldMemoryProvider

    return PenfieldMemoryProvider
