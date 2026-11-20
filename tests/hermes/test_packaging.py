# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Hermes's own tooling accepts the plugin: validator clean, installer enables it."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .conftest import _IGNORED, REPO_ROOT, child_env, hermes_cli


def _hermes(home: Path, *args: str, timeout: float = 300.0) -> subprocess.CompletedProcess[str]:
    home.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [*hermes_cli(), *args],
        env=child_env(home),
        cwd=str(home),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _git_snapshot(dest: Path) -> Path:
    """Commit a copy of the working tree so the installer can clone it."""
    shutil.copytree(REPO_ROOT, dest, ignore=_IGNORED)
    identity = ["-c", "user.name=test", "-c", "user.email=test@example.invalid"]
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", *identity, "-c", "commit.gpgsign=false", "commit", "-q", "-m", "snapshot"],
    ):
        subprocess.run(cmd, cwd=dest, check=True, capture_output=True)
    return dest


def test_validator_reports_no_warnings(tmp_path: Path) -> None:
    result = _hermes(tmp_path / "home", "plugins", "validate", "--json", str(REPO_ROOT))

    report = json.loads(result.stdout)
    assert report["ok"], [c for c in report["checks"] if not c["ok"]]
    assert report["warnings"] == []
    assert report["isolation"]["host_ready"]


def test_install_command_enables_the_provider_unattended(tmp_path: Path) -> None:
    source = _git_snapshot(tmp_path / "source")
    home = tmp_path / "home"

    result = _hermes(home, "plugins", "install", f"file://{source}", "--enable")

    output = result.stdout + result.stderr
    assert result.returncode == 0, output[-3000:]
    assert "Cannot enable" not in output, output[-3000:]
    assert (home / "plugins" / "penfield" / "plugin.yaml").is_file()
    config = (home / "config.yaml").read_text(encoding="utf-8")
    assert "provider: penfield" in config
