# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Facts about the repository as Hermes installs it."""

from __future__ import annotations

import re
from pathlib import Path

import penfield
import pytest
from penfield.constants import USER_AGENT, VERSION

ROOT = Path(__file__).resolve().parent.parent


def _manifest() -> dict[str, str]:
    lines = (ROOT / "plugin.yaml").read_text(encoding="utf-8").splitlines()
    pairs = (line.split(":", 1) for line in lines if re.match(r"^[a-z_]+:", line))
    return {key: value.strip().strip('"') for key, value in pairs}


def test_manifest_and_code_agree_on_the_version() -> None:
    assert _manifest()["version"] == VERSION
    assert penfield.__version__ == VERSION
    assert f"hermes-penfield/{VERSION}" == USER_AGENT


#: What Hermes reports about itself when it cannot read its own version in time.
HERMES_FALLBACK_VERSION = (0, 21, 5)


def test_manifest_declares_a_hermes_floor() -> None:
    assert re.fullmatch(r">=\d+\.\d+\.\d+", _manifest()["requires_hermes"])


def test_the_floor_cannot_refuse_a_hermes_that_misread_its_own_version() -> None:
    """A higher floor makes ``hermes plugins install`` fail on slow machines (ADR-0021)."""
    floor = tuple(
        int(part) for part in _manifest()["requires_hermes"].removeprefix(">=").split(".")
    )
    assert floor <= HERMES_FALLBACK_VERSION


def test_manifest_declares_no_registered_tools_or_hooks() -> None:
    """Memory provider tools reach Hermes through get_tool_schemas, not the manifest."""
    manifest = _manifest()
    assert "provides_tools" not in manifest
    assert "hooks" not in manifest


@pytest.mark.parametrize("name", ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"])
def test_nothing_in_the_root_declares_python_dependencies(name: str) -> None:
    """Hermes takes such a file as a request to install packages (ADR-0019).

    Hermes before 0.23 does so for any ``pyproject.toml``, even one holding
    tool configuration only, and then cannot enable the plugin unattended.
    """
    assert not (ROOT / name).exists()
