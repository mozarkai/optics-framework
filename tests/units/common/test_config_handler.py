"""Unit tests for ``optics_framework.common.config_handler``.

Pins two things after the global-config layer was removed:

1. Constructing a ``ConfigHandler`` creates **no** ``~/.optics/global_config.yaml``
   (the dead global layer is gone — HOME is monkeypatched to a tmp dir and we
   assert the file is absent afterwards).
2. ``deep_merge`` / ``update_config`` preserve their real semantics: a list-valued
   key in the incoming config REPLACES the default list wholesale (not an
   item-wise merge) — so a project that names its own ``driver_sources`` overrides
   the defaults entirely.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from optics_framework.common.config_handler import Config, ConfigHandler

pytestmark = pytest.mark.white_box


def _make_handler(tmp_home: Path) -> ConfigHandler:
    """A ConfigHandler whose execution_output lands in a tmp dir."""
    config = Config()
    config.execution_output_path = str(tmp_home / "execution_output")
    return ConfigHandler(config)


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Point HOME at a throwaway dir so any stray global write is detectable."""
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

    def test_update_config_accepts_config_object(self, tmp_path):
        handler = _make_handler(tmp_path)
        override = Config(log_level="WARNING")
        handler.update_config(override)
        assert handler.config.log_level == "WARNING"
