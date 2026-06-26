# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""CLI subcommands registered under ``hermes penfield ...``.

The provider exposes these via :func:`register_cli`, which Hermes calls
with an ``argparse`` subparser. Each command is also runnable directly via
:meth:`main` for standalone testing.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import TYPE_CHECKING, Any

from hermes_penfield import __version__
from hermes_penfield.auth import PenfieldAuth
from hermes_penfield.client import PenfieldClient
from hermes_penfield.config import PenfieldConfig, resolve_environment
from hermes_penfield.exceptions import AuthError, PenfieldError
from hermes_penfield.tools import dispatch

if TYPE_CHECKING:
    from collections.abc import Sequence


def register_cli(subparser: argparse._SubParsersAction) -> None:
    """Register ``hermes penfield <subcommand>`` handlers.

    Nests commands under a ``penfield`` group, as Hermes expects. The
    command definitions themselves live in :func:`_add_commands` so the
    Hermes path and the standalone :func:`main` path cannot drift.

    Args:
        subparser: The argparse subparsers object Hermes passes in.
    """
    penfield = subparser.add_parser("penfield", help="Penfield memory provider commands")
    penfield.set_defaults(_penfield_cli=True)
    sub = penfield.add_subparsers(dest="penfield_command", required=True)
    _add_commands(sub)


def _build_config_and_client(hermes_home: str) -> tuple[PenfieldConfig, PenfieldClient]:
    cfg = PenfieldConfig.load(hermes_home or None)
    auth = PenfieldAuth(cfg, hermes_home or None)
    client = PenfieldClient(auth, cfg)
    return cfg, client


def cmd_status(args: argparse.Namespace) -> int:
    cfg, client = _build_config_and_client(getattr(args, "hermes_home", ""))
    print(f"hermes-penfield {__version__}")
    print(f"environment: {cfg.env.value}")
    print(f"api_base: {cfg.api_base}")
    print(f"api_key set: {'yes' if cfg.api_key else 'no'}")
    auth = PenfieldAuth(cfg, getattr(args, "hermes_home", "") or None)
    print(f"authenticated: {'yes' if auth.is_authenticated() else 'no'}")
    # The help text promises a memory count, so actually fetch it.
    # Non-fatal: if the tenant is unreachable, report that rather than fail.
    try:
        stats = client.call("search_stats")
        print(f"memory count: {stats.get('total_memories', '?')}")
    except PenfieldError as exc:
        print(f"memory count: unavailable ({exc})")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    hermes_home = getattr(args, "hermes_home", "")
    cfg = PenfieldConfig.load(hermes_home or None)
    auth = PenfieldAuth(cfg, hermes_home or None)
    try:
        auth.device_code_flow(client_id=getattr(args, "client_id", None))
    except AuthError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1
    print("login successful; tokens cached.")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    hermes_home = getattr(args, "hermes_home", "")
    cfg = PenfieldConfig.load(hermes_home or None)
    auth = PenfieldAuth(cfg, hermes_home or None)
    auth.logout()
    print("logged out; cached tokens cleared.")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    _, client = _build_config_and_client(getattr(args, "hermes_home", ""))
    query = " ".join(getattr(args, "query", []) or [])
    if not query:
        print("error: query required", file=sys.stderr)
        return 2
    try:
        result = dispatch(client, "penfield_recall", {"query": query, "limit": args.limit})
    except PenfieldError as exc:
        print(f"search failed: {exc}", file=sys.stderr)
        return 1
    parsed = json.loads(result)
    for it in parsed.get("items", []):
        score = it.get("score")
        # score may be None on some results; guard the format.
        score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "  -  "
        snippet = it.get("snippet", "")
        print(f"[{score_str}] {snippet}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    _, client = _build_config_and_client(getattr(args, "hermes_home", ""))
    try:
        stats = client.call("search_stats")
    except PenfieldError as exc:
        print(f"stats failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, indent=2))
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


_COMMANDS = {
    "status": cmd_status,
    "login": cmd_login,
    "logout": cmd_logout,
    "search": cmd_search,
    "stats": cmd_stats,
    "version": cmd_version,
}


def main(argv: Sequence[str] | None = None) -> int:
    """Standalone entry point (also used by tests).

    Registers commands directly (no ``penfield`` prefix) so the binary works
    standalone: ``hermes-penfield status`` rather than
    ``hermes-penfield penfield status``.
    """
    parser = argparse.ArgumentParser(prog="hermes-penfield")
    sub = parser.add_subparsers(dest="penfield_command", required=True)
    _register_commands(sub)
    args = parser.parse_args(argv)
    cmd = getattr(args, "penfield_command", None)
    handler = _COMMANDS.get(str(cmd) if cmd is not None else "")
    if handler is None:
        parser.print_help(sys.stderr)
        return 2
    return int(handler(args) or 0)


def _register_commands(sub: Any) -> None:
    """Register command subparsers directly (standalone path).

    Thin wrapper over :func:`_add_commands`; kept as a named entry point
    so :func:`main` reads cleanly.
    """
    _add_commands(sub)


def _add_commands(sub: Any) -> None:
    """Define the penfield subcommands on a subparsers action.

    Single source of truth for command definitions — both the Hermes
    path (:func:`register_cli`, nested under ``penfield``) and the
    standalone path (:func:`main`, flat) call this so they can't drift.
    """
    p = sub.add_parser("status", help="Show connection status")
    p.add_argument("--hermes-home", default="")

    p = sub.add_parser("login", help="Run interactive OAuth device-code login")
    p.add_argument("--hermes-home", default="")
    p.add_argument("--client-id", default=None)

    p = sub.add_parser("logout", help="Clear cached tokens")
    p.add_argument("--hermes-home", default="")

    p = sub.add_parser("search", help="Quick semantic search from the CLI")
    p.add_argument("query", nargs=argparse.REMAINDER)
    p.add_argument("--hermes-home", default="")
    p.add_argument("--limit", type=int, default=10)

    p = sub.add_parser("stats", help="Show memory/relationship/storage stats")
    p.add_argument("--hermes-home", default="")

    sub.add_parser("version", help="Print the plugin version")


# resolve_environment is re-exported for CLI-level env validation tests.
__all__ = ["main", "register_cli", "resolve_environment"]
