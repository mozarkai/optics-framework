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

from optics_framework.common.config_handler import Config, ConfigHandler, deep_merge

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


class TestNoGlobalConfigLayer:
    def test_constructing_handler_writes_no_global_file(self, isolated_home, tmp_path):
        handler = _make_handler(tmp_path)
        # The old global file must not be created by construction.
        assert not (isolated_home / ".optics" / "global_config.yaml").exists()
        # And the handler exposes no global-config attributes/methods anymore.
        assert not hasattr(handler, "global_config_path")
        assert not hasattr(ConfigHandler, "DEFAULT_GLOBAL_CONFIG_PATH")
        assert not hasattr(handler, "load")
        assert not hasattr(handler, "save_config")
        assert not hasattr(handler, "_ensure_global_config")
        assert not hasattr(handler, "_load_yaml")

    def test_no_global_dir_created_at_all(self, isolated_home, tmp_path):
        _make_handler(tmp_path)
        # Not even the ~/.optics directory should be touched now.
        assert not (isolated_home / ".optics").exists()


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


class TestDeepMergeListSemantics:
    """A list in c2 REPLACES the list in c1 — not an item-wise merge. This is the
    load-bearing rule for project configs: a project listing its own
    ``driver_sources`` overrides the defaults' driver list entirely."""

    def test_project_driver_sources_replaces_default_list(self):
        default = Config()
        # Sanity: defaults ship three disabled drivers.
        default_names = [next(iter(d)) for d in default.driver_sources]
        assert "appium" in default_names

        project = Config(
            driver_sources=[
                {"playwright": {"enabled": True, "url": None, "capabilities": {}}}
            ]
        )
        merged = deep_merge(default, project)
        names = [next(iter(d)) for d in merged.driver_sources]
        # The default appium/selenium/ble are GONE — replaced, not appended.
        assert names == ["playwright"]
        assert "appium" not in names

    def test_empty_project_list_falls_back_to_defaults(self):
        # An empty list means "I specified nothing" — Config.__init__ refills
        # the defaults, consistent with how a project omitting the key behaves.
        default = Config()
        project = Config(driver_sources=[])
        merged = deep_merge(default, project)
        assert len(merged.driver_sources) > 0
        # But the OTHER defaults (untouched by project) survive.
        assert len(merged.elements_sources) > 0

    def test_scalar_override_wins(self):
        default = Config()
        project = Config(log_level="ERROR")
        merged = deep_merge(default, project)
        assert merged.log_level == "ERROR"

    def test_list_replacement_is_wholesale_not_itemwise(self):
        # The load-bearing rule: a project that lists its own driver_sources
        # OVERRIDES the defaults' list entirely. The default appium/selenium/ble
        # are gone — there is no positional merge of list items.
        default = Config()
        default_names = [next(iter(d)) for d in default.driver_sources]
        assert "appium" in default_names

        project = Config(
            driver_sources=[
                {"playwright": {"enabled": True, "url": "http://proj", "capabilities": {"browser": "chromium"}}}
            ]
        )
        merged = deep_merge(default, project)
        names = [next(iter(d)) for d in merged.driver_sources]
        assert names == ["playwright"]
        assert "appium" not in names
        entry = merged.driver_sources[0]["playwright"]
        assert entry.enabled is True
        assert entry.url == "http://proj"
        assert entry.capabilities == {"browser": "chromium"}


def test_config_handler_precomputes_enabled(tmp_path):
    config = Config(
        driver_sources=[
            {"appium": {"enabled": True, "url": "http://x", "capabilities": {}}},
            {"selenium": {"enabled": False, "url": None, "capabilities": {}}},
        ]
    )
    config.execution_output_path = str(tmp_path / "out")
    handler = ConfigHandler(config)
    assert handler.get("driver_sources") == ["appium"]
    assert handler.get("elements_sources") == []


def test_strict_element_match_defaults_to_false():
    assert Config().strict_element_match is False
