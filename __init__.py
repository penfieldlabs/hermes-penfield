# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Penfield memory provider for Hermes Agent.

This repo root IS the plugin. ``hermes plugins install penfieldlabs/hermes-penfield --enable``
copies the whole repo into ``~/.hermes/plugins/penfield/``. Hermes loads
``__init__.py``, finds ``register(ctx)``, and calls
``ctx.register_memory_provider(PenfieldMemoryProvider())``.
"""

from __future__ import annotations

from typing import Any


def __getattr__(name: str) -> Any:
    """Resolve ``__version__`` on demand.

    This file has no module-level relative imports, so tooling that imports
    it outside the package (pytest's collection, for one) still can.
    """
    if name == "__version__":
        from .constants import VERSION

        return VERSION
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def register(ctx: Any) -> None:
    """Register Penfield as a memory provider plugin."""
    from .provider import PenfieldMemoryProvider

    ctx.register_memory_provider(PenfieldMemoryProvider())
