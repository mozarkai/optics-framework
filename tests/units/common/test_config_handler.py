from __future__ import annotations

from pathlib import Path

import pytest

from optics_framework.common.config_handler import Config, ConfigHandler


def _make_handler(tmp_home: Path) -> ConfigHandler:
    """A ConfigHandler whose execution_output lands in a tmp dir."""
    config = Config()
    config.execution_output_path = str(tmp_home / "execution_output")
    return ConfigHandler(config)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    return home


class TestUpdateConfigMerges:
    def test_update_config_merges_scalar_overrides(self, tmp_path):
        handler = _make_handler(tmp_path)
        handler.update_config({"log_level": "DEBUG"})
        assert handler.config.log_level == "DEBUG"
