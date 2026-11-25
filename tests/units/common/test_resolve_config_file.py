import re

import pytest

from optics_framework.common.config_handler import resolve_config_file
from optics_framework.common.error import OpticsError


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("HUB", "hub.example.com")
    monkeypatch.setenv("EMPTY", "")
    monkeypatch.delenv("UNSET", raising=False)


@pytest.mark.parametrize("value", ["$UNSET", "${UNSET}", "${EMPTY}"])
def test_unset_or_empty_variable_stops_with_its_name(env, tmp_path, value):
    folder = str(tmp_path)
    with pytest.raises(OpticsError, match=re.escape(value)):
        resolve_config_file({"key": value}, folder)


def test_missing_relative_file_stops_with_the_full_path(tmp_path):
    folder = str(tmp_path)
    with pytest.raises(OpticsError, match="does not exist"):
        resolve_config_file({"event_attributes_json": "./missing.json"}, folder)


def test_playwright_url_and_headers_are_left_for_the_driver(env, tmp_path):
    entry = {"enabled": True, "url": "wss://$HUB?token=$${X}",
             "capabilities": {"headers": {"Authorization": "Bearer ${HUB}"}, "browser": "$HUB"}}
    resolved = resolve_config_file({"driver_sources": [{"playwright": entry}]}, str(tmp_path))
    assert resolved["driver_sources"][0]["playwright"] == {
        "enabled": True, "url": "wss://$HUB?token=$${X}",
        "capabilities": {"headers": {"Authorization": "Bearer ${HUB}"}, "browser": "hub.example.com"},
    }


@pytest.mark.parametrize("value", ['["secret"]', "secret"])
def test_from_needs_a_json_object_and_keeps_its_value_out_of_the_error(monkeypatch, tmp_path, value):
    monkeypatch.setenv("CAPS", value)
    folder = str(tmp_path)
    with pytest.raises(OpticsError, match=r"\$from: \$CAPS") as error:
        resolve_config_file({"capabilities": {"$from": "$CAPS"}}, folder)
    assert "secret" not in error.value.message
