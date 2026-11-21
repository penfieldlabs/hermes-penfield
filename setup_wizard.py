# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""What ``hermes memory setup`` runs for Penfield.

Hermes asks a provider's schema questions itself when the provider is picked
from its list. ``hermes memory setup penfield``, the command Hermes prints
after an install, only selects the provider unless the provider has a
``post_setup`` hook. This is that hook. It connects the profile to Penfield
and selects the provider:

* **Browser sign-in** is offered first. The hook shows a link and a code and
  waits until the user has approved in a browser.
* **An API key** is the other choice: for a machine nobody sits at, or to
  limit what the agent may do. The hook asks for it, checks it with Penfield
  and saves it where Hermes keeps secrets.

The hook needs a terminal to ask on, and it saves with functions from
Hermes's published plugin API. In the plugin host the standard streams are
Hermes's channel, so there it talks through the controlling terminal, the
way ``ssh`` and ``sudo`` do. Where there is no terminal at all (a script, a
service) or a Hermes without those functions, there is no hook and Hermes
asks its schema questions instead. See ADR-0022 and ADR-0024.

Ctrl-C cancels the setup at any point and changes nothing. In the plugin
host the terminal would deliver it as a signal to Hermes as well, which
would end Hermes with a traceback while it waits for the hook. For the
length of the hook the terminal is therefore told to pass Ctrl-C on as a
key, and the hook reads it as "cancel".
"""

from __future__ import annotations

import contextlib
import functools
import getpass
import os
import select
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .auth import PenfieldAuth, TokenSet, access_level, key_problem, saved_browser_sign_in
from .client import PenfieldClient
from .config import PenfieldConfig, read_setting
from .constants import API_KEYS_URL, SETUP_CHECK_TIMEOUT
from .exceptions import (
    AuthError,
    AuthTransportError,
    PenfieldError,
    PermissionDeniedError,
    TokenExpiredError,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

ACCEPTED = "accepted"
REJECTED = "rejected"
UNCHECKED = "unchecked"
#: Not something that can be an API key at all; Penfield is not asked.
UNUSABLE = "unusable"


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


#: Set by Hermes in the plugin host, the process a plugin runs in under
#: ``plugins.isolation: host``.
_PLUGIN_HOST_ENV = "HERMES_PLUGIN_HOST_PROCESS"
_TTY = "/dev/tty"


@dataclass(frozen=True)
class Terminal:
    """How the hook talks to the person at the keyboard."""

    #: Show one line.
    say: Callable[[str], None]
    #: Ask a question and return the answer, shown as it is typed.
    ask: Callable[[str], str]
    #: Ask a question and return the answer, not shown as it is typed.
    ask_secret: Callable[[str], str]
    #: Pause for a number of seconds; Ctrl-C ends the pause with KeyboardInterrupt.
    wait: Callable[[float], None] = time.sleep
    #: Entered for the length of the hook; see :func:`_ctrl_c_as_a_key`.
    session: Callable[[], contextlib.AbstractContextManager[None]] = contextlib.nullcontext


def _has_terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):  # a stream was closed or replaced
        return False


def _ask_secret(question: str) -> str:
    """Ask on the terminal without showing what is typed.

    ``getpass`` reads from the controlling terminal when there is one, so
    this works from the plugin host as well.
    """
    return getpass.getpass(f"{question}: ")


_CTRL_C = "\x03"


def _say_on_tty(line: str) -> None:
    with open(_TTY, "w", encoding="utf-8") as tty:
        tty.write(f"{line}\n")


def _read_line(fd: int) -> str:
    """Read what was typed up to Enter, or up to Ctrl-C (see ``_ctrl_c_as_a_key``).

    One ``read`` returns one line from a terminal. Python's ``readline``
    would go on reading until a newline, which Ctrl-C does not send.
    """
    answer = os.read(fd, 4096).decode("utf-8", "replace")
    if _CTRL_C in answer:
        raise KeyboardInterrupt
    if not answer:
        raise EOFError
    return answer.rstrip("\n")


def _ask_on_tty(question: str, *, echo: bool = True) -> str:
    import termios

    fd = os.open(_TTY, os.O_RDWR | os.O_NOCTTY)
    try:
        os.write(fd, f"{question}: ".encode())
        if echo:
            return _read_line(fd)
        # As getpass does it: echo off for the one line, then a newline of our own.
        shown = termios.tcgetattr(fd)
        hidden = termios.tcgetattr(fd)
        hidden[3] &= ~termios.ECHO
        termios.tcsetattr(fd, termios.TCSAFLUSH, hidden)
        try:
            return _read_line(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, shown)
            os.write(fd, b"\n")
    finally:
        os.close(fd)


def _ask_secret_on_tty(question: str) -> str:
    return _ask_on_tty(question, echo=False)


def _wait_on_tty(seconds: float) -> None:
    """Pause, and cancel at once if Ctrl-C is typed meanwhile."""
    end = time.monotonic() + seconds
    fd = os.open(_TTY, os.O_RDONLY | os.O_NOCTTY)
    try:
        while (left := end - time.monotonic()) > 0:
            ready, _, _ = select.select([fd], [], [], left)
            if ready:
                with contextlib.suppress(EOFError):
                    _read_line(fd)
    finally:
        os.close(fd)


#: A process that puts the terminal back if this one ends without doing so.
#: It notes the settings it finds, says so, and waits for its input to close.
_GUARD = """
import os, sys, termios
fd = os.open(sys.argv[1], os.O_RDWR | os.O_NOCTTY)
found = termios.tcgetattr(fd)
sys.stdout.write("ready\\n")
sys.stdout.flush()
if sys.stdin.read() != "done":
    termios.tcsetattr(fd, termios.TCSANOW, found)
"""


@contextlib.contextmanager
def _guarded_terminal() -> Iterator[None]:
    """Make sure the terminal is put back even if this process is ended abruptly.

    Hermes ends the plugin host without unwinding it, so a ``finally`` in
    here is not enough. A small process of its own watches instead: if this
    one goes away without saying "done", it restores what it found.
    """
    guard = None
    with contextlib.suppress(OSError, ValueError):
        guard = subprocess.Popen(
            [sys.executable, "-I", "-c", _GUARD, _TTY],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        if guard.stdout is not None:
            guard.stdout.readline()  # it has noted the settings
    try:
        yield
    finally:
        if guard is not None:
            with contextlib.suppress(OSError, ValueError, subprocess.TimeoutExpired):
                guard.communicate("done", timeout=5)


@contextlib.contextmanager
def _ctrl_c_as_a_key() -> Iterator[None]:
    """Have the terminal pass Ctrl-C on as a key, and put it back afterwards.

    A terminal turns Ctrl-C into a signal for every process in the
    foreground: this one, and the Hermes that is waiting for it. Hermes does
    not expect one there. With the signal switched off and Ctrl-C made an
    end-of-line key, a read returns at once with the key in it and the hook
    cancels on its own terms.
    """
    import termios

    fd = os.open(_TTY, os.O_RDWR | os.O_NOCTTY)
    try:
        with _guarded_terminal():
            found = termios.tcgetattr(fd)
            ours = termios.tcgetattr(fd)
            ours[3] &= ~termios.ISIG
            ours[6][termios.VEOL] = _CTRL_C.encode()
            termios.tcsetattr(fd, termios.TCSADRAIN, ours)
            try:
                yield
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, found)
    finally:
        os.close(fd)


def _controlling_terminal() -> Terminal | None:
    """Return the terminal this process was started from, if it has one.

    The plugin host's standard streams belong to Hermes: input and output
    are its channel, and anything printed lands in the log. The terminal
    the user typed the command in is still reachable as the controlling
    terminal. ``None`` where there is none: a service, a desktop app,
    Windows.
    """
    try:
        import termios
    except ImportError:
        return None
    try:
        fd = os.open(_TTY, os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return None
    try:
        termios.tcgetattr(fd)
    except termios.error:  # it opens, but it is not a terminal
        return None
    finally:
        os.close(fd)
    return Terminal(
        say=_say_on_tty,
        ask=_ask_on_tty,
        ask_secret=_ask_secret_on_tty,
        wait=_wait_on_tty,
        session=_ctrl_c_as_a_key,
    )


def _terminal() -> Terminal | None:
    """Return the way to reach the user from this process, or ``None``."""
    if os.environ.get(_PLUGIN_HOST_ENV) == "1":
        return _controlling_terminal()
    if not _has_terminal():
        return None
    return Terminal(say=print, ask=lambda question: input(f"{question}: "), ask_secret=_ask_secret)


def hook(provider_name: str) -> Callable[[str, dict[str, Any]], None] | None:
    """Return the setup hook for this process, or ``None`` where it cannot run."""
    hermes = _hermes_helpers()
    if hermes is None:
        return None
    terminal = _terminal()
    if terminal is None:
        return None
    return functools.partial(run, hermes, provider_name, terminal=terminal)


def check_key(api_key: str, hermes_home: str) -> tuple[str, str]:
    """Ask Penfield whether it accepts ``api_key``.

    Returns:
        ``(ACCEPTED, "")``, ``(REJECTED, reason)`` when Penfield refused the
        key, ``(UNUSABLE, reason)`` when it cannot be a key at all, or
        ``(UNCHECKED, reason)`` when no answer could be had. Nothing is
        cached; the first session makes its own exchange.
    """
    problem = key_problem(api_key)
    if problem:
        return UNUSABLE, problem
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


def check_sign_in(hermes_home: str) -> tuple[str, str]:
    """Ask Penfield whether it still accepts the profile's browser sign-in.

    The saved tokens alone do not tell: a sign-in revoked at Penfield looks
    the same on disk. One request is made, the one ``hermes penfield
    status`` makes, and a sign-in whose access token has run out is renewed
    on the way.

    Returns:
        ``(ACCEPTED, "")``, ``(REJECTED, reason)`` when the sign-in is over
        (it ran out or was revoked), or ``(UNCHECKED, reason)`` when no
        answer could be had.
    """
    try:
        cfg = PenfieldConfig.load(hermes_home)
        auth = PenfieldAuth(cfg, hermes_home, timeout=SETUP_CHECK_TIMEOUT)
        PenfieldClient(auth, cfg).call("search_stats", timeout=SETUP_CHECK_TIMEOUT, retries=0)
    except AuthTransportError as exc:
        return UNCHECKED, str(exc)
    except TokenExpiredError as exc:
        return REJECTED, str(exc)
    except PermissionDeniedError:
        # Penfield knows the login; what it may do is another matter.
        return ACCEPTED, ""
    except PenfieldError as exc:
        return UNCHECKED, str(exc)
    return ACCEPTED, ""


_SIGN_IN = "sign in"
_KEEP = "keep"
_API_KEY = "api key"

_API_KEY_CHOICE = (
    "Use an API key: for a machine nobody sits at, or to limit what the\n"
    "       agent may do (read only, read and write, or read, write and delete)"
)


def run(
    hermes: _Hermes,
    provider_name: str,
    hermes_home: str,
    config: dict[str, Any],
    *,
    terminal: Terminal | None = None,
) -> None:
    """Connect the profile to Penfield and select the provider.

    Nothing is changed unless the profile ends up connected or was
    connected already: a sign-in that is not approved, a key Penfield
    refuses and a cancelled question all leave the profile as it was. A key
    that cannot be checked, because Penfield could not be reached, is saved
    and reported as such; a browser sign-in it would replace is then left
    in place.

    Args:
        hermes: Hermes's ``.env`` and ``config.yaml`` writers.
        provider_name: The name to store as ``memory.provider``.
        hermes_home: The profile being set up.
        config: Hermes's loaded ``config.yaml``, saved back with the choice.
        terminal: How to reach the user; this process's terminal by default.
    """
    term = terminal or Terminal(
        say=print, ask=lambda question: input(f"{question}: "), ask_secret=_ask_secret
    )
    with term.session():
        term.say(f"\n  Configuring {provider_name}:\n")
        try:
            _connect(hermes, provider_name, hermes_home, config, term)
        except (KeyboardInterrupt, EOFError):
            term.say("\n\n  Setup cancelled. Nothing was changed.\n")
        except PenfieldError as exc:
            term.say(f"\n  Setup stopped: {exc}\n  Nothing was changed.\n")


def _connect(
    hermes: _Hermes, provider_name: str, hermes_home: str, config: dict[str, Any], term: Terminal
) -> None:
    existing = read_setting("PENFIELD_API_KEY").strip()
    if existing:
        term.say("  This profile connects with an API key, which is used in preference to a")
        term.say("  browser sign-in. To sign in with your browser instead, remove")
        term.say("  PENFIELD_API_KEY from the profile's .env and run this command again.\n")
        # The key is what counts here. A setting that cannot be used shows
        # when the key is checked, so it must not stop the hook before that.
        signed_in = False
        with contextlib.suppress(PenfieldError):
            signed_in = _usable_sign_in(hermes_home) is not None
        _use_api_key(
            hermes, provider_name, hermes_home, config, term, existing, signed_in=signed_in
        )
        return

    # A sign-in saved in the profile counts only if Penfield still accepts it.
    sign_in = _usable_sign_in(hermes_home)
    state, reason = UNCHECKED, ""
    if sign_in is not None:
        # Said first: the answer can take a while when Penfield is slow.
        term.say("  Checking the saved browser sign-in with Penfield...")
        state, reason = check_sign_in(hermes_home)
    if state == REJECTED:
        term.say("  This profile's browser sign-in is no longer valid: it ran out or was")
        term.say("  revoked.\n")
        sign_in = None
    choice = _choose(term, sign_in, reason, again=state == REJECTED)
    if choice == _API_KEY:
        _use_api_key(
            hermes,
            provider_name,
            hermes_home,
            config,
            term,
            existing,
            signed_in=sign_in is not None,
        )
        return
    if choice == _SIGN_IN:
        term.say("")
        auth = PenfieldAuth(
            PenfieldConfig.load(hermes_home), hermes_home, timeout=SETUP_CHECK_TIMEOUT
        )
        try:
            auth.device_code_flow(say=lambda line: term.say(f"  {line}"), sleep=term.wait)
        except AuthError as exc:
            term.say(f"\n  The sign-in did not finish: {exc}.")
            term.say("  Nothing was changed. Run the command again to retry.\n")
            return
        level = auth.access_level()
    else:
        # Read again: checking the sign-in may have renewed it.
        level = access_level(_usable_sign_in(hermes_home) or sign_in)
    if not _select_provider(hermes, provider_name, hermes_home, config, term):
        return
    term.say("")
    signed = "Signed in" if choice == _SIGN_IN else "Keeping the browser sign-in"
    term.say(f"  {signed}." if level == "unknown" else f"  {signed}. This login may {level}.")
    term.say(f"  Memory provider: {provider_name}")
    term.say("  Activation saved to config.yaml")
    term.say("\n  Start a new session to activate.\n")


def _usable_sign_in(hermes_home: str) -> TokenSet | None:
    """Return the profile's saved browser sign-in, if it is one for the address in use."""
    return saved_browser_sign_in(hermes_home, PenfieldConfig.load(hermes_home).api_base)


def _choose(term: Terminal, sign_in: TokenSet | None, unchecked: str, *, again: bool) -> str:
    """Ask how to connect and return ``_SIGN_IN``, ``_KEEP`` or ``_API_KEY``.

    Args:
        term: Where to ask.
        sign_in: The profile's browser sign-in, unless Penfield has ended it.
        unchecked: Why that sign-in could not be checked, if it could not.
        again: Whether a sign-in here would replace one that has ended.
    """
    if sign_in is None:
        if not again:
            term.say("  How should this agent connect to Penfield?\n")
        choices = [_SIGN_IN, _API_KEY]
        first = "Sign in again with your browser" if again else "Sign in with your browser"
        term.say(f"    1. {first} (recommended)")
        term.say(f"    2. {_API_KEY_CHOICE}")
    else:
        level = access_level(sign_in)
        may = "" if level == "unknown" else f" That login may {level}."
        term.say(f"  This profile is signed in with a browser.{may}")
        if unchecked:
            term.say(f"  The sign-in could not be checked ({unchecked}).")
        term.say("")
        choices = [_KEEP, _API_KEY, _SIGN_IN]
        term.say("    1. Keep this sign-in")
        term.say(f"    2. {_API_KEY_CHOICE}")
        term.say("    3. Sign in again")
    term.say("")
    while True:
        answer = term.ask("  Choice [1]").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer) - 1]
        term.say(f"  Please answer with a number from 1 to {len(choices)}.")


def _select_provider(
    hermes: _Hermes,
    provider_name: str,
    hermes_home: str,
    config: dict[str, Any],
    term: Terminal,
    *,
    api_key: str = "",
) -> bool:
    """Save the provider choice, and ``api_key`` if given. False if saving failed."""
    memory = config.get("memory")
    if not isinstance(memory, dict):
        memory = config["memory"] = {}
    memory["provider"] = provider_name
    # Hermes's writers act on the active profile; make that the one being set up.
    override = hermes.set_home(hermes_home)
    try:
        if api_key:
            hermes.save_env_value("PENFIELD_API_KEY", api_key)
        hermes.save_config(config)
    except (OSError, ValueError) as exc:
        term.say(f"\n  Setup could not be saved: {exc}\n")
        return False
    finally:
        hermes.reset_home(override)
    return True


def _use_api_key(
    hermes: _Hermes,
    provider_name: str,
    hermes_home: str,
    config: dict[str, Any],
    term: Terminal,
    existing: str,
    *,
    signed_in: bool = False,
) -> None:
    """Ask for an API key, check it, save it and select the provider.

    ``signed_in`` says the profile has a browser sign-in Penfield has not
    ended, which is what it falls back on when no key is entered.
    """
    term.say(f"  Get an API key at {API_KEYS_URL}")
    question = "  Penfield API key"
    if existing:
        shown = f"...{existing[-4:]}" if len(existing) > 4 else "set"
        question += f" (current: {shown}, blank to keep)"
    entered = term.ask_secret(question).strip()

    key = entered or existing
    verdict, reason = check_key(key, hermes_home) if key else (UNCHECKED, "")
    if entered and verdict == UNUSABLE:
        term.say(f"\n  That cannot be an API key: {reason}.")
        term.say(f"  Nothing was changed. Copy the key again at {API_KEYS_URL}")
        term.say("  and run the command again.\n")
        return
    if entered and verdict == REJECTED:
        term.say("\n  Penfield did not accept this API key. Nothing was changed.")
        term.say(f"  Check the key at {API_KEYS_URL} and run the command again.\n")
        return

    if not _select_provider(hermes, provider_name, hermes_home, config, term, api_key=entered):
        return
    term.say("")
    for line in _summary(entered, existing, verdict, reason, signed_in=signed_in):
        term.say(f"  {line}")
    if entered and verdict == ACCEPTED:
        if _end_browser_sign_in(hermes_home):
            term.say("  The saved browser sign-in was ended; the API key is used from now on.")
    elif entered and signed_in:
        # The key may turn out not to work; the sign-in is then still there.
        # Ending it needs Penfield, which did not answer just now, so the way
        # to end it is this command again, in-process and in the plugin host.
        term.say("  The saved browser sign-in was kept, because the key could not be checked.")
        term.say("  Enter the key again when you do: once Penfield has accepted it, the")
        term.say("  sign-in is ended.")
    term.say(f"  Memory provider: {provider_name}")
    term.say("  Activation saved to config.yaml")
    if entered:
        term.say("  API key saved to .env")
    term.say("\n  Start a new session to activate.\n")


def _end_browser_sign_in(hermes_home: str) -> bool:
    """Revoke and forget a browser sign-in that a newly saved key replaces.

    The key takes precedence from now on, so the sign-in would only linger:
    unused here, still valid at Penfield. Called for a key Penfield has
    accepted; a key that could not be checked is no reason to give up a
    login that works.
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
    entered: str, existing: str, verdict: str, reason: str, *, signed_in: bool
) -> list[str]:
    """Say what is known about the credentials the profile now has."""
    if not entered and not existing:
        if signed_in:
            return ["No API key entered. The saved browser sign-in will be used."]
        return [
            "No API key entered. Penfield stays off until the profile is connected:",
            "run this command again to sign in with your browser or to enter a key.",
        ]
    which = "API key" if entered else "Current API key"
    if verdict == ACCEPTED:
        return [f"{which} accepted by Penfield."]
    if verdict == REJECTED:
        return [
            "Penfield does not accept the current API key.",
            "Run this command again and enter a new one.",
        ]
    if verdict == UNUSABLE:
        return [
            f"The current API key cannot be used: {reason}.",
            "Run this command again and enter a new one.",
        ]
    # This command is named, not `hermes penfield status`: it also runs in
    # the plugin host, where that one does not exist.
    kept = "It was saved" if entered else "It was kept"
    return [
        f"{which} could not be checked ({reason}).",
        f"{kept}; run this command again later to check it.",
    ]
