# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Penfield
"""Unit tests for config / environment resolution."""

from __future__ import annotations

import json
import pathlib

import pytest
from penfield.config import Environment, PenfieldConfig, resolve_environment
from penfield.exceptions import ConfigError


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


class TestSettings:
    def test_unset_is_an_empty_string(self) -> None:
        from penfield.config import read_setting

        assert read_setting("PENFIELD_API_KEY") == ""

    def test_reads_the_environment_outside_hermes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from penfield.config import read_setting

        monkeypatch.setenv("PENFIELD_ENV", "dev")
        assert read_setting("PENFIELD_ENV") == "dev"

    def test_default_home_follows_hermes_home(self, tmp_path: pathlib.Path) -> None:
        from penfield.config import default_hermes_home

        assert default_hermes_home() == str(tmp_path / "hermes-home")


class TestBehaviourSettings:
    def _load(self, home: pathlib.Path, **saved: object) -> PenfieldConfig:
        (home / "penfield").mkdir(parents=True, exist_ok=True)
        (home / "penfield" / "config.json").write_text(json.dumps(saved), encoding="utf-8")
        return PenfieldConfig.load(home)

    def test_saved_values_are_applied(self, tmp_path: pathlib.Path) -> None:
        cfg = self._load(
            tmp_path,
            auto_recall=False,
            recall_limit=8,
            recall_threshold=0.4,
            max_prefetch_chars=900,
        )
        assert (cfg.auto_recall, cfg.recall_limit) == (False, 8)
        assert (cfg.recall_threshold, cfg.max_prefetch_chars) == (0.4, 900)

    def test_numbers_are_kept_within_range(self, tmp_path: pathlib.Path) -> None:
        cfg = self._load(tmp_path, recall_limit=5000, recall_threshold=7, max_prefetch_chars=1)
        assert (cfg.recall_limit, cfg.recall_threshold, cfg.max_prefetch_chars) == (100, 1.0, 200)

    def test_a_value_of_the_wrong_type_is_ignored_with_a_warning(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        cfg = self._load(tmp_path, auto_recall="false", recall_limit="many")

        assert (cfg.auto_recall, cfg.recall_limit) == (True, 5)
        assert "ignoring auto_recall='false'" in caplog.text
        assert "ignoring recall_limit='many'" in caplog.text

    def test_unknown_keys_survive_a_save(self, tmp_path: pathlib.Path) -> None:
        cfg = self._load(tmp_path, added_by_a_newer_version=True)
        cfg.save(tmp_path)
        saved = json.loads((tmp_path / "penfield" / "config.json").read_text())
        assert saved["added_by_a_newer_version"] is True

    def test_a_broken_file_falls_back_to_defaults(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "penfield").mkdir()
        (tmp_path / "penfield" / "config.json").write_text("{not json", encoding="utf-8")
        assert PenfieldConfig.load(tmp_path).recall_limit == 5
