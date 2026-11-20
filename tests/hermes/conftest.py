# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Fixtures for running the plugin inside a real Hermes Agent install.

These tests need Hermes importable in the interpreter running pytest. They
skip when it is not, unless ``HERMES_PENFIELD_REQUIRE_HERMES=1`` is set (CI
sets it so a broken install fails loudly).
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.support.fake_penfield import FakePenfield, Recorded

REPO_ROOT = Path(__file__).resolve().parents[2]
DRIVER = Path(__file__).with_name("driver.py")
TERMINAL = Path(__file__).with_name("terminal.py")
ISOLATION_MODES = ("in_process", "host")
TEST_API_KEY = "tm_pf_test_ak_integration"

_IGNORED = shutil.ignore_patterns(
    ".git",
    ".venv*",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".coverage*",
    "htmlcov",
    "*.egg-info",
)


def _hermes_importable() -> bool:
    try:
        return importlib.util.find_spec("agent.memory_provider") is not None
    except (ImportError, ValueError):
        return False


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    mine = [item for item in items if here in Path(str(item.fspath)).parents]
    for item in mine:
        item.add_marker(pytest.mark.hermes)
    if mine and not _hermes_importable():
        if os.environ.get("HERMES_PENFIELD_REQUIRE_HERMES") == "1":
            pytest.exit("Hermes Agent is required for tests/hermes but is not importable", 1)
        skip = pytest.mark.skip(reason="Hermes Agent is not installed in this interpreter")
        for item in mine:
            item.add_marker(skip)


def calls_during(fake: FakePenfield, report: dict[str, Any], step: str) -> list[Recorded]:
    """Requests the fake API received while one driver step was running."""
    entry = report["steps"][step]
    return [r for r in fake.calls() if entry["started"] <= r.at <= entry["finished"]]


def hermes_cli() -> list[str]:
    """Command prefix for the ``hermes`` CLI of the interpreter running pytest."""
    script = Path(sys.executable).with_name("hermes")
    return [str(script)] if script.exists() else [sys.executable, "-m", "hermes_cli.main"]


def child_env(home: Path) -> dict[str, str]:
    """Environment for a Hermes child process bound to ``home``.

    Plugin settings must come from the home's ``.env``, as they do for a real
    user, so anything ``PENFIELD_*`` in the caller's environment is dropped.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("PENFIELD_")}
    env["HERMES_HOME"] = str(home)
    for name in ("NO_PROXY", "no_proxy"):
        hosts = [h for h in env.get(name, "").split(",") if h]
        env[name] = ",".join(dict.fromkeys([*hosts, "127.0.0.1", "localhost"]))
    return env


@pytest.fixture
def fake_api() -> Iterator[FakePenfield]:
    with FakePenfield() as fake:
        yield fake


def build_home(
    base: Path,
    *,
    api_url: str,
    isolation: str = "in_process",
    api_key: str = TEST_API_KEY,
    settings: dict[str, Any] | None = None,
) -> Path:
    """Build a Hermes home under ``base`` with this plugin as the memory provider.

    ``api_key=""`` leaves the home without credentials. ``settings`` is written
    to the plugin's own ``penfield/config.json``.
    """
    home = base / f"hermes-home-{isolation}"
    shutil.copytree(REPO_ROOT, home / "plugins" / "penfield", ignore=_IGNORED)
    lines = ["memory:", "  provider: penfield"]
    if isolation != "in_process":
        lines += ["plugins:", f"  isolation: {isolation}"]
    (home / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    env_lines = [f"PENFIELD_URL={api_url}"]
    if api_key:
        env_lines.insert(0, f"PENFIELD_API_KEY={api_key}")
    (home / ".env").write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    if settings:
        (home / "penfield").mkdir()
        (home / "penfield" / "config.json").write_text(json.dumps(settings), encoding="utf-8")
    return home


def run_driver(home: Path, scenario: str, *, timeout: float = 120.0) -> dict[str, Any]:
    """Run a driver scenario in a fresh Hermes process and return its report."""
    out = home.parent / f"report-{scenario}-{home.name}.json"
    proc = subprocess.run(
        [sys.executable, str(DRIVER), scenario, str(out)],
        env=child_env(home),
        cwd=str(home),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    assert out.exists(), (
        f"driver wrote no report\nstdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-2000:]}"
    )
    report: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    assert "crashed" not in report, report["crashed"]
    return report


def run_in_terminal(
    home: Path, args: list[str], answers: list[tuple[str, str]], *, timeout: float = 120.0
) -> dict[str, Any]:
    """Run a ``hermes`` command on a terminal of its own, typing ``answers``.

    Each answer is ``(text to wait for, text to type)``. Returns what the
    command showed, its exit code and how many answers were typed.
    """
    spec = home.parent / f"terminal-{home.name}.json"
    out = home.parent / f"terminal-{home.name}-result.json"
    spec.write_text(
        json.dumps(
            {
                "argv": [*hermes_cli(), *args],
                "cwd": str(home),
                "answers": [list(pair) for pair in answers],
                "timeout": timeout,
            }
        ),
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, str(TERMINAL), str(spec), str(out)],
        env={**child_env(home), "TERM": "xterm-256color", "LINES": "40", "COLUMNS": "120"},
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout + 30,
        check=False,
    )
    assert out.exists(), f"no terminal result\nstderr: {proc.stderr[-2000:]}"
    result: dict[str, Any] = json.loads(out.read_text(encoding="utf-8"))
    assert not result["timed_out"], result["shown"][-2000:]
    return result


@pytest.fixture
def make_home(tmp_path: Path) -> Callable[..., Path]:
    def build(**kwargs: Any) -> Path:
        return build_home(tmp_path, **kwargs)

    return build
