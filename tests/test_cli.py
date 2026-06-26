# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for the CLI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from hermes_penfield import __version__
from hermes_penfield.cli import main, register_cli

if TYPE_CHECKING:
    import pytest


class TestVersion:
    def test_version_prints(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["version"])
        assert rc == 0
        out = capsys.readouterr().out.strip()
        assert out == __version__


class TestStatus:
    def test_status_runs(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Status should not raise even without creds.
        import hermes_penfield.cli as cli
        from hermes_penfield.config import Environment, PenfieldConfig

        class StubClient:
            def call(self, *a: Any, **k: Any) -> Any:
                return {"total_memories": 42}

        monkeypatch.delenv("PENFIELD_API_KEY", raising=False)
        cfg = PenfieldConfig(env=Environment.PROD)
        monkeypatch.setattr(cli, "_build_config_and_client", lambda h: (cfg, StubClient()))  # type: ignore[arg-type]
        rc = main(["status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "hermes-penfield" in out
        assert "environment" in out
        assert "memory count: 42" in out

    def test_status_survives_stats_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # The memory-count fetch is non-fatal; a broken tenant must not
        # fail the whole status command.
        import hermes_penfield.cli as cli
        from hermes_penfield.config import Environment, PenfieldConfig
        from hermes_penfield.exceptions import APIError

        class StubClient:
            def call(self, *a: Any, **k: Any) -> Any:
                raise APIError("nope", status=500)

        monkeypatch.setattr(
            cli,
            "_build_config_and_client",
            lambda h: (PenfieldConfig(env=Environment.PROD), StubClient()),
        )  # type: ignore[arg-type]
        rc = main(["status"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "unavailable" in out


class TestSearch:
    def test_no_query_errors(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["search"])
        assert rc != 0

    def test_search_dispatches(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Stub the dispatch to avoid network.
        import hermes_penfield.cli as cli

        def fake_dispatch(_client: Any, _name: str, _args: dict[str, Any]) -> str:
            return json.dumps({"items": [{"id": "x", "score": 0.5, "snippet": "hi"}]})

        monkeypatch.setattr(cli, "dispatch", fake_dispatch)
        # Bypass client construction by stubbing _build_config_and_client.
        monkeypatch.setattr(cli, "_build_config_and_client", lambda h: (None, None))  # type: ignore[arg-type]
        rc = main(["search", "my", "query"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "hi" in out

    def test_search_handles_null_score(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Regression: previously `f"[{score:.3f}]"` crashed with TypeError
        # when score was None (some API results omit it). Must not raise.
        import hermes_penfield.cli as cli

        def fake_dispatch(_client: Any, _name: str, _args: dict[str, Any]) -> str:
            return json.dumps({"items": [{"id": "a", "score": None, "snippet": "no score"}]})

        monkeypatch.setattr(cli, "dispatch", fake_dispatch)
        monkeypatch.setattr(cli, "_build_config_and_client", lambda h: (None, None))  # type: ignore[arg-type]
        rc = main(["search", "x"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "no score" in out


class TestStats:
    def test_stats_dispatches(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        import hermes_penfield.cli as cli

        class FakeClient:
            def call(self, *a: Any, **k: Any) -> Any:
                return {"total_memories": 42}

        monkeypatch.setattr(cli, "_build_config_and_client", lambda h: (None, FakeClient()))  # type: ignore[arg-type]
        rc = main(["stats"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "42" in out


class TestRegisterCli:
    def test_register_cli_adds_subcommands(self) -> None:
        import argparse

        from hermes_penfield.cli import _register_commands

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="penfield_command")
        _register_commands(sub)
        # Parsing "status" should succeed and set the command.
        args = parser.parse_args(["status"])
        assert args.penfield_command == "status"

    def test_register_cli_wraps_under_penfield(self) -> None:
        """Hermes integration: register_cli nests under a ``penfield`` group."""
        import argparse

        parser = argparse.ArgumentParser()
        sub = parser.add_subparsers(dest="command")
        register_cli(sub)
        args = parser.parse_args(["penfield", "version"])
        assert args.penfield_command == "version"
