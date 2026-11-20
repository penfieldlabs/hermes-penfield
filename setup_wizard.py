# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""What ``hermes memory setup`` runs for Penfield.

Hermes asks a provider's schema questions itself when the provider is picked
from its list. ``hermes memory setup penfield``, the command Hermes prints
after an install, only selects the provider unless the provider has a
``post_setup`` hook. This is that hook: it asks for the API key, checks it
with Penfield, saves it where Hermes keeps secrets and selects the provider.

The hook needs a terminal to ask on, and it saves with functions from
Hermes's published plugin API. Where either is missing (the plugin host, a
script, a Hermes without those functions) there is no hook and Hermes asks
its schema questions instead. See ADR-0022.
"""

from __future__ import annotations

import functools
import getpass
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .auth import PenfieldAuth, browser_sign_in_saved
from .config import PenfieldConfig, read_setting
from .constants import API_KEYS_URL, SETUP_CHECK_TIMEOUT
from .exceptions import AuthError, AuthTransportError, PenfieldError

if TYPE_CHECKING:
    from collections.abc import Callable

ACCEPTED = "accepted"
REJECTED = "rejected"
UNCHECKED = "unchecked"


@dataclass(frozen=True)
class _Hermes:
    """The functions of Hermes's plugin API the hook saves with."""

    #: ``save_env_value(name, value)``: write one line of the profile's ``.env``.
    save_env_value: Callable[[str, str], Any]
    #: ``save_config(config)``: write the profile's ``config.yaml``.
    save_config: Callable[..., Any]
    #: Point the two writers above at a profile directory, and undo that.
    set_home: Callable[[str], Any]
    reset_home: Callable[[Any], None]


def _hermes_helpers() -> _Hermes | None:
    try:
        from hermes_cli.config import (  # type: ignore[import-not-found]
            save_config,
            save_env_value,
        )
        from hermes_constants import (  # type: ignore[import-not-found]
            reset_hermes_home_override,
            set_hermes_home_override,
        )
    except ImportError:
        return None
    return _Hermes(
        save_env_value=save_env_value,
        save_config=save_config,
        set_home=set_hermes_home_override,
        reset_home=reset_hermes_home_override,
    )


def _has_terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):  # a stream was closed or replaced
        return False


def _ask_secret(question: str) -> str:
    """Ask on the terminal without showing what is typed."""
    return getpass.getpass(f"{question}: ")


def hook(provider_name: str) -> Callable[[str, dict[str, Any]], None] | None:
    """Return the setup hook for this process, or ``None`` where it cannot run."""
    if not _has_terminal():
        return None
    hermes = _hermes_helpers()
    if hermes is None:
        return None
    return functools.partial(run, hermes, provider_name)


def check_key(api_key: str, hermes_home: str) -> tuple[str, str]:
    """Ask Penfield whether it accepts ``api_key``.

    Returns:
        ``(ACCEPTED, "")``, ``(REJECTED, reason)`` when Penfield refused the
        key, or ``(UNCHECKED, reason)`` when no answer could be had. Nothing
        is cached; the first session makes its own exchange.
    """
    try:
        cfg = PenfieldConfig.load(hermes_home, api_key=api_key)
        PenfieldAuth(cfg, None, timeout=SETUP_CHECK_TIMEOUT).authenticate()
    except AuthTransportError as exc:
        return UNCHECKED, str(exc)
    except AuthError as exc:
        return (REJECTED if exc.status in (401, 403) else UNCHECKED), str(exc)
    except PenfieldError as exc:
        return UNCHECKED, str(exc)
    return ACCEPTED, ""


def run(
    hermes: _Hermes,
    provider_name: str,
    hermes_home: str,
    config: dict[str, Any],
    *,
    ask: Callable[[str], str] | None = None,
) -> None:
    """Ask for the API key, check it, save it and select the provider.

    A key Penfield refuses changes nothing. A key that cannot be checked,
    because Penfield could not be reached, is saved and reported as such.

    Args:
        hermes: Hermes's ``.env`` and ``config.yaml`` writers.
        provider_name: The name to store as ``memory.provider``.
        hermes_home: The profile being set up.
        config: Hermes's loaded ``config.yaml``, saved back with the choice.
        ask: Asks one question and returns the answer; the terminal by default.
    """
    existing = read_setting("PENFIELD_API_KEY")
    print(f"\n  Configuring {provider_name}:\n")
    print(f"  Get an API key at {API_KEYS_URL}")
    question = "  Penfield API key"
    if existing:
        shown = f"...{existing[-4:]}" if len(existing) > 4 else "set"
        question += f" (current: {shown}, blank to keep)"
    try:
        entered = (ask or _ask_secret)(question).strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\n  Setup cancelled. Nothing was changed.\n")
        return

    key = entered or existing
    verdict, reason = check_key(key, hermes_home) if key else (UNCHECKED, "")
    if entered and verdict == REJECTED:
        print("\n  Penfield did not accept this API key. Nothing was changed.")
        print(f"  Check the key at {API_KEYS_URL} and run the command again.\n")
        return

    memory = config.get("memory")
    if not isinstance(memory, dict):
        memory = config["memory"] = {}
    memory["provider"] = provider_name
    # Hermes's writers act on the active profile; make that the one being set up.
    override = hermes.set_home(hermes_home)
    try:
        if entered:
            hermes.save_env_value("PENFIELD_API_KEY", entered)
        hermes.save_config(config)
    except (OSError, ValueError) as exc:
        print(f"\n  Setup could not be saved: {exc}\n")
        return
    finally:
        hermes.reset_home(override)

    print()
    for line in _summary(entered, existing, verdict, reason, hermes_home):
        print(f"  {line}")
    if entered and _end_browser_sign_in(hermes_home):
        print("  The saved browser sign-in was ended; the API key is used from now on.")
    print(f"  Memory provider: {provider_name}")
    print("  Activation saved to config.yaml")
    if entered:
        print("  API key saved to .env")
    print("\n  Start a new session to activate.\n")


def _end_browser_sign_in(hermes_home: str) -> bool:
    """Revoke and forget a browser sign-in that a newly saved key replaces.

    The key takes precedence from now on, so the sign-in would only linger:
    unused here, still valid at Penfield.
    """
    try:
        auth = PenfieldAuth(
            PenfieldConfig.load(hermes_home), hermes_home, timeout=SETUP_CHECK_TIMEOUT
        )
        if not auth.holds_browser_sign_in():
            return False
        auth.logout()
    except PenfieldError:
        return False
    return True


def _summary(
    entered: str, existing: str, verdict: str, reason: str, hermes_home: str
) -> list[str]:
    """Say what is known about the credentials the profile now has."""
    if not entered and not existing:
        if browser_sign_in_saved(hermes_home):
            return ["No API key entered. The saved browser sign-in will be used."]
        return [
            "No API key entered. Penfield stays off until there is one:",
            "run this command again, or sign in with `hermes penfield login`.",
        ]
    which = "API key" if entered else "Current API key"
    if verdict == ACCEPTED:
        return [f"{which} accepted by Penfield."]
    if verdict == REJECTED:
        return [
            "Penfield does not accept the current API key.",
            "Run this command again and enter a new one.",
        ]
    kept = "It was saved" if entered else "It was kept"
    return [
        f"{which} could not be checked ({reason}).",
        f"{kept}; `hermes penfield status` checks it again.",
    ]
