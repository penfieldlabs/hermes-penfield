# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Memory-provider plugin shim for Hermes Agent directory discovery.

Hermes discovers memory providers by scanning two **directories** —
``plugins/memory/<name>/`` (bundled) and ``$HERMES_HOME/plugins/<name>/``
(user-installed) — NOT via pip entry points. See ADR-0014.

This file is the directory-layout shim. The full implementation lives in
the installed ``hermes_penfield`` package (``pip install hermes-penfield``);
this file only:

1. exposes a ``register(ctx)`` callable matching Hermes' contract, and
2. contains the literal string ``register_memory_provider`` so the loader's
   text-scan heuristic (``_is_memory_provider_dir``) recognizes it.

Install it (after ``pip install hermes-penfield``) via:

    hermes-penfield install         # drops this dir into $HERMES_HOME/plugins/penfield

or by copying this whole directory to ``$HERMES_HOME/plugins/penfield/``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hermes_penfield.provider import PenfieldMemoryProvider

if TYPE_CHECKING:
    import hermes_cli.plugins  # noqa: F401  (type-only; not imported at runtime)


def register(ctx: object) -> None:
    """Register Penfield as a memory provider.

    Hermes calls this with a context whose ``register_memory_provider``
    method accepts a :class:`~hermes_penfield.provider.PenfieldMemoryProvider`
    instance. Matches the contract in
    ``plugins/memory/hindsight/__init__.py`` verbatim.
    """
    ctx.register_memory_provider(PenfieldMemoryProvider())  # type: ignore[attr-defined]
