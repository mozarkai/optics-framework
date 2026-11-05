import os
import re

import pytest

from optics_framework.common.config_handler import resolve_config_file
from optics_framework.common.error import OpticsError


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("HUB", "hub.example.com")
    monkeypatch.setenv("EMPTY", "")
    monkeypatch.delenv("UNSET", raising=False)


@pytest.mark.parametrize("value, expected", [
    ("https://$HUB/wd/hub", "https://hub.example.com/wd/hub"),
    ("https://${HUB}/wd/hub", "https://hub.example.com/wd/hub"),
    ("${UNSET:-emulator-5554}", "emulator-5554"),
    ("${EMPTY:-fallback}", "fallback"),
    ("${UNSET:-}", ""),
    ("pa$$word", "pa$word"),
    ("$${HUB}", "${HUB}"),
    ("costs $5", "costs $5"),
])
def test_expands_environment_references(env, tmp_path, value, expected):
    assert resolve_config_file({"key": value}, str(tmp_path)) == {"key": expected}


@pytest.mark.parametrize("value", ["$UNSET", "${UNSET}", "${EMPTY}"])
def test_unset_or_empty_variable_stops_with_its_name(env, tmp_path, value):
    with pytest.raises(OpticsError, match=re.escape(value)):
        resolve_config_file({"key": value}, str(tmp_path))


def test_expands_nested_values_and_keeps_other_types(env, tmp_path):
    data = {"driver_sources": [{"appium": {"enabled": True, "url": "$HUB", "capabilities": {"ids": ["$HUB", 3]}}}]}
    resolved = resolve_config_file(data, str(tmp_path))
    assert resolved == {"driver_sources": [{"appium": {"enabled": True, "url": "hub.example.com",
                                                       "capabilities": {"ids": ["hub.example.com", 3]}}}]}


def test_relative_paths_resolve_against_the_project_folder(tmp_path, monkeypatch):
    (tmp_path / "creds").mkdir()
    (tmp_path / "creds" / "sa.json").write_text("{}")
    monkeypatch.setenv("CREDS", "./creds/sa.json")
    monkeypatch.chdir("/")
    resolved = resolve_config_file({"a": "./creds/sa.json", "b": "$CREDS", "c": "creds/sa.json"}, str(tmp_path))
    expected = os.path.join(str(tmp_path), "creds", "sa.json")
    assert resolved == {"a": expected, "b": expected, "c": "creds/sa.json"}


def test_missing_relative_file_stops_with_the_full_path(tmp_path):
    with pytest.raises(OpticsError, match="does not exist"):
        resolve_config_file({"event_attributes_json": "./missing.json"}, str(tmp_path))


def test_output_paths_need_not_exist_yet(tmp_path):
    resolved = resolve_config_file({"execution_output_path": "./out", "log_path": "../logs"}, str(tmp_path))
    assert resolved == {"execution_output_path": str(tmp_path / "out"),
                        "log_path": os.path.abspath(os.path.join(str(tmp_path), "..", "logs"))}


def test_playwright_url_and_headers_are_left_for_the_driver(env, tmp_path):
    entry = {"enabled": True, "url": "wss://$HUB?token=$${X}",
             "capabilities": {"headers": {"Authorization": "Bearer ${HUB}"}, "browser": "$HUB"}}
    resolved = resolve_config_file({"driver_sources": [{"playwright": entry}]}, str(tmp_path))
    assert resolved["driver_sources"][0]["playwright"] == {
        "enabled": True, "url": "wss://$HUB?token=$${X}",
        "capabilities": {"headers": {"Authorization": "Bearer ${HUB}"}, "browser": "hub.example.com"},
    }
