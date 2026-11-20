# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Run a command on a pseudo-terminal and type answers at its prompts.

Some of Hermes's commands only ask questions when they have a terminal. The
integration tests start this file in a subprocess, which gives the command a
terminal of its own and keeps the fork out of the multi-threaded test
process.

Usage: ``python terminal.py <spec.json> <result.json>``. The spec holds
``argv``, ``cwd``, ``timeout`` and ``answers``, a list of
``[text to wait for, text to type]`` pairs used in order. The result holds
the exit code, everything the command showed, and how many answers were
typed.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import signal
import sys
import time
from pathlib import Path

_CONTROL = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]|\r")
#: Type only once the screen has been quiet this long, so a prompt is complete.
_SETTLE_SECONDS = 0.3
_EXIT_GRACE_SECONDS = 10.0


def _shown(raw: bytes) -> str:
    return _CONTROL.sub("", raw.decode("utf-8", "replace"))


def main(argv: list[str]) -> int:
    spec = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    answers: list[list[str]] = spec["answers"]
    pid, fd = pty.fork()
    if pid == 0:  # the command, with the pseudo-terminal as its terminal
        os.chdir(spec["cwd"])
        os.execvp(spec["argv"][0], spec["argv"])
    raw, typed, answered_at = b"", 0, 0
    deadline = time.monotonic() + float(spec["timeout"])
    quiet_since = time.monotonic()
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # the command closed its terminal
                chunk = b""
            if not chunk:
                break
            raw += chunk
            quiet_since = time.monotonic()
            continue
        waiting = typed < len(answers) and answers[typed][0] in _shown(raw)[answered_at:]
        if waiting and time.monotonic() - quiet_since >= _SETTLE_SECONDS:
            answered_at = len(_shown(raw))
            os.write(fd, answers[typed][1].encode())
            typed += 1
    # The terminal closes a moment before the command has finished exiting.
    finished, status = os.waitpid(pid, os.WNOHANG)
    grace = time.monotonic() + _EXIT_GRACE_SECONDS
    while not finished and time.monotonic() < min(grace, deadline):
        time.sleep(0.05)
        finished, status = os.waitpid(pid, os.WNOHANG)
    if not finished:
        os.kill(pid, signal.SIGKILL)
        _, status = os.waitpid(pid, 0)
    result = {
        "exit": os.waitstatus_to_exitcode(status),
        "timed_out": not finished,
        "shown": _shown(raw),
        "typed": typed,
    }
    Path(argv[2]).write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
