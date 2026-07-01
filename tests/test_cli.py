# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for the CLI."""

from __future__ import annotations

from typing import Any

import pytest


class TestResolveHermesHome:
    def test_explicit_arg_wins(self) -> None:
        from penfield.cli import _resolve_hermes_home

        assert _resolve_hermes_home("/custom/path") == "/custom/path"

    def test_env_var_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from penfield.cli import _resolve_hermes_home

        monkeypatch.setenv("HERMES_HOME", "/env/path")
        assert _resolve_hermes_home("") == "/env/path"

    def test_defaults_to_home_hermes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from penfield.cli import _resolve_hermes_home

        monkeypatch.delenv("HERMES_HOME", raising=False)
        result = _resolve_hermes_home("")
        assert result.endswith("/.hermes")


class TestRegisterCli:
    def test_register_cli_adds_subcommands(self) -> None:
        import argparse

        from penfield.cli import register_cli

        parser = argparse.ArgumentParser()
        register_cli(parser)
        args = parser.parse_args(["status"])
        assert args.penfield_command == "status"


class TestStatusSmoke:
    """Smoke tests that mock the network layer."""

    def test_status_prints_version_and_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Any,
    ) -> None:
        from penfield.cli import cmd_status

        class StubClient:
            def is_authenticated(self) -> bool:
                return True

            def call(self, *_a: Any, **_k: Any) -> Any:
                return {"total_memories": 42}

        from penfield.config import Environment, PenfieldConfig

        cfg = PenfieldConfig(env=Environment.PROD)

        class StubArgs:
            hermes_home = str(tmp_path)

        monkeypatch.setattr("penfield.cli._build_config_and_client", lambda h: (cfg, StubClient()))
        rc = cmd_status(StubArgs())
        assert rc == 0
        out = capsys.readouterr().out
        assert "penfield" in out
        assert "memory count: 42" in out


class TestVersionSmoke:
    def test_version_prints(self, capsys: pytest.CaptureFixture[str]) -> None:
        from penfield.cli import cmd_version

        class StubArgs:
            pass

        rc = cmd_version(StubArgs())
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out  # non-empty version string
