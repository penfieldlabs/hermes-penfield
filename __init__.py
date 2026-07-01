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

__version__ = "0.2.0"


def register(ctx: Any) -> None:
    """Register Penfield as a memory provider plugin."""
    from .provider import PenfieldMemoryProvider

    ctx.register_memory_provider(PenfieldMemoryProvider())
