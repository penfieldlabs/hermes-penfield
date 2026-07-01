# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Penfield memory-provider CLI commands for Hermes.

Hermes calls ``register_cli(plugin_parser)`` with the already-created
``penfield`` subparser; we add subcommands TO it. A top-level
``penfield_command`` function dispatches based on the parsed subcommand.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def register_cli(parser: argparse.ArgumentParser) -> None:
    """Register ``hermes penfield <subcommand>`` handlers.

    Hermes creates the ``penfield`` subparser and passes it here. We add
    subcommands to it and wire a default handler.
    """
    subs = parser.add_subparsers(dest="penfield_command", required=True)

    p = subs.add_parser("status", help="Show connection status")
    p.add_argument("--hermes-home", default="")

    p = subs.add_parser("login", help="Run interactive OAuth device-code login")
    p.add_argument("--hermes-home", default="")
    p.add_argument("--client-id", default=None)

    p = subs.add_parser("logout", help="Clear cached tokens")
    p.add_argument("--hermes-home", default="")

    p = subs.add_parser("search", help="Quick semantic search from the CLI")
    p.add_argument("query", nargs=argparse.REMAINDER)
    p.add_argument("--hermes-home", default="")
    p.add_argument("--limit", type=int, default=10)

    p = subs.add_parser("stats", help="Show memory/relationship/storage stats")
    p.add_argument("--hermes-home", default="")

    subs.add_parser("version", help="Print the plugin version")

    parser.set_defaults(func=penfield_command)


def penfield_command(args: argparse.Namespace) -> None:
    """Dispatch handler for ``hermes penfield <subcommand>``."""
    cmd = getattr(args, "penfield_command", None)
    if cmd == "status":
        sys.exit(cmd_status(args))
    elif cmd == "login":
        sys.exit(cmd_login(args))
    elif cmd == "logout":
        sys.exit(cmd_logout(args))
    elif cmd == "search":
        sys.exit(cmd_search(args))
    elif cmd == "stats":
        sys.exit(cmd_stats(args))
    elif cmd == "version":
        sys.exit(cmd_version(args))
    else:
        print(f"unknown penfield command: {cmd}", file=sys.stderr)
        sys.exit(2)


# ---------------------------------------------------------------------------
# Command implementations
# ---------------------------------------------------------------------------

__version__ = "0.2.0"


def _resolve_hermes_home(hermes_home: str = "") -> str:
    """Resolve HERMES_HOME the way Hermes itself does.

    Priority: explicit arg > $HERMES_HOME > ~/.hermes.
    Without this, commands like `hermes penfield status` fail to find
    tokens at ~/.hermes/penfield/tokens.json because the default empty
    string doesn't resolve to a real path.
    """
    import os
    from pathlib import Path

    if hermes_home:
        return hermes_home
    env_home = os.environ.get("HERMES_HOME", "")
    if env_home:
        return env_home
    return str(Path.home() / ".hermes")


def _build_config_and_client(hermes_home: str) -> tuple[Any, Any]:
    from .auth import PenfieldAuth
    from .client import PenfieldClient
    from .config import PenfieldConfig

    resolved = _resolve_hermes_home(hermes_home)
    cfg = PenfieldConfig.load(resolved)
    auth = PenfieldAuth(cfg, resolved)
    client = PenfieldClient(auth, cfg)
    return cfg, client


def cmd_status(args: argparse.Namespace) -> int:
    cfg, client = _build_config_and_client(getattr(args, "hermes_home", ""))
    print(f"penfield {__version__}")
    print(f"environment: {cfg.env.value}")
    print(f"api_base: {cfg.api_base}")
    print(f"api_key set: {'yes' if cfg.api_key else 'no'}")
    print(f"authenticated: {'yes' if client.is_authenticated() else 'no'}")
    try:
        stats = client.call("search_stats")
        print(f"memory count: {stats.get('total_memories', '?')}")
    except Exception as exc:
        print(f"memory count: unavailable ({exc})")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    import os
    from pathlib import Path

    from .auth import PenfieldAuth
    from .config import PenfieldConfig
    from .exceptions import AuthError

    hermes_home = getattr(args, "hermes_home", "") or os.environ.get("HERMES_HOME", "")
    if not hermes_home:
        hermes_home = str(Path.home() / ".hermes")
    cfg = PenfieldConfig.load(hermes_home)
    auth = PenfieldAuth(cfg, hermes_home)
    try:
        auth.device_code_flow(client_id=getattr(args, "client_id", None))
    except AuthError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1
    print("login successful; tokens cached.")
    return 0


def cmd_logout(args: argparse.Namespace) -> int:
    from .auth import PenfieldAuth
    from .config import PenfieldConfig

    hermes_home = _resolve_hermes_home(getattr(args, "hermes_home", ""))
    cfg = PenfieldConfig.load(hermes_home)
    auth = PenfieldAuth(cfg, hermes_home)
    auth.logout()
    print("logged out; cached tokens cleared.")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    from .exceptions import PenfieldError
    from .tools import dispatch

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
        score_str = f"{score:.3f}" if isinstance(score, (int, float)) else "  -  "
        snippet = it.get("snippet", "")
        print(f"[{score_str}] {snippet}")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    from .exceptions import PenfieldError

    _, client = _build_config_and_client(getattr(args, "hermes_home", ""))
    try:
        stats = client.call("search_stats")
    except PenfieldError as exc:
        print(f"stats failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(stats, indent=2, default=str))
    return 0


def cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0
