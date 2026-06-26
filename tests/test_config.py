# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for config / environment resolution."""

from __future__ import annotations

import json
import pathlib  # noqa: TC003

import pytest

from hermes_penfield.config import Environment, PenfieldConfig, resolve_environment
from hermes_penfield.exceptions import ConfigError


class TestEnvironmentResolution:
    def test_default_is_prod(self) -> None:
        assert resolve_environment(None) is Environment.PROD

    def test_dev_explicit(self) -> None:
        assert resolve_environment("dev") is Environment.DEV

    def test_case_insensitive(self) -> None:
        assert resolve_environment("PROD") is Environment.PROD

    def test_invalid_raises(self) -> None:
        with pytest.raises(ConfigError):
            resolve_environment("staging")

    def test_env_var_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_ENV", "dev")
        assert resolve_environment(None) is Environment.DEV


class TestHostApplication:
    def test_prod_hosts(self) -> None:
        cfg = PenfieldConfig(env=Environment.PROD)
        assert cfg.api_base == "https://api.penfield.app/api/v2"
        assert cfg.auth_url == "https://auth.penfield.app"
        assert cfg.mcp_url == "https://mcp.penfield.app"

    def test_dev_hosts(self) -> None:
        cfg = PenfieldConfig(env=Environment.DEV)
        assert cfg.api_base == "https://api-dev.penfield.app/api/v2"
        assert cfg.auth_url == "https://auth-dev.penfield.app"
        assert cfg.portal_url == "https://portal-dev.penfield.app"


class TestUrlOverride:
    def test_env_var_overrides_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_URL", "https://staging.example.com/api/v9")
        cfg = PenfieldConfig.load()
        assert cfg.api_base == "https://staging.example.com/api/v9"

    def test_explicit_override_wins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_URL", "https://env.example.com")
        cfg = PenfieldConfig.load(api_url_override="https://arg.example.com")
        assert cfg.api_base == "https://arg.example.com"


class TestLoadSave:
    def test_save_then_load_roundtrip(self, tmp_path: pathlib.Path) -> None:
        cfg = PenfieldConfig(env=Environment.DEV)
        cfg.recall_limit = 7
        cfg.auto_recall = False
        cfg.save(tmp_path)

        # api_key must NEVER be persisted.
        saved = json.loads((tmp_path / "penfield" / "config.json").read_text())
        assert "api_key" not in saved

        loaded = PenfieldConfig.load(tmp_path)
        assert loaded.env is Environment.DEV
        assert loaded.recall_limit == 7
        assert loaded.auto_recall is False

    def test_load_reads_api_key_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PENFIELD_API_KEY", "tm_test_ak_xyz")
        cfg = PenfieldConfig.load()
        assert cfg.api_key == "tm_test_ak_xyz"

    def test_token_path(self, tmp_path: pathlib.Path) -> None:
        cfg = PenfieldConfig(env=Environment.PROD)
        assert cfg.token_path(tmp_path) == tmp_path / "penfield" / "tokens.json"
