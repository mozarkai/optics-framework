"""A REST session start carries the project's popup rules and permissions into the session's Config."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from optics_framework.common import expose_api
from optics_framework.common.expose_api import AppiumSettingsUpdate, PopupRulesUpdate, SessionConfig
from tests.units.popup_helpers import rule_dict


def _start(monkeypatch, **fields):
    captured = {}

    def create_session(config, **_):
        captured["config"] = config
        return "session-1"

    async def launch_app(session_id, request):
        return SimpleNamespace(data={"result": "driver-1"})

    monkeypatch.setattr(expose_api.session_manager, "create_session", create_session)
    monkeypatch.setattr(expose_api, "execute_keyword", launch_app)
    monkeypatch.setattr(expose_api, "reconfigure_logging", lambda config: None)
    asyncio.run(expose_api.create_session(SessionConfig(**fields)))
    return captured["config"]


def test_the_session_keeps_the_rules_and_permissions_it_was_started_with(monkeypatch):
    config = _start(monkeypatch, popup_rules=[rule_dict("Rate us")], permissions={"camera": "allow", "sms": "deny"})
    assert [rule.name for rule in config.popup_rules] == ["Rate us"]
    assert config.permissions == {"camera": "allow", "sms": "deny"}


def test_a_rule_list_that_cannot_be_read_stops_the_start(monkeypatch):
    with pytest.raises(HTTPException) as caught:
        _start(monkeypatch, popup_rules=[{"name": "bad"}])
    assert "popup_rules" in str(caught.value.detail)


def _running(monkeypatch, session):
    monkeypatch.setattr(expose_api.session_manager, "get_session", lambda session_id: session)


def test_the_rules_of_a_running_session_are_replaced(monkeypatch):
    session = SimpleNamespace(config=SimpleNamespace(popup_rules=[]))
    _running(monkeypatch, session)
    assert asyncio.run(expose_api.replace_popup_rules("s1", PopupRulesUpdate(popup_rules=[rule_dict("Rate us")]))) == {"popup_rules": 1}
    assert [rule.name for rule in session.config.popup_rules] == ["Rate us"]
    asyncio.run(expose_api.replace_popup_rules("s1", PopupRulesUpdate(popup_rules=[])))
    assert session.config.popup_rules == []


def test_a_rule_list_that_cannot_be_read_leaves_the_live_rules_alone(monkeypatch):
    keep = ["kept"]
    session = SimpleNamespace(config=SimpleNamespace(popup_rules=keep))
    _running(monkeypatch, session)
    with pytest.raises(HTTPException):
        asyncio.run(expose_api.replace_popup_rules("s1", PopupRulesUpdate(popup_rules=[{"name": "bad"}])))
    assert session.config.popup_rules is keep


def test_appium_settings_reach_the_drivers_session(monkeypatch):
    seen = []
    _running(monkeypatch, SimpleNamespace(driver=SimpleNamespace(active_instance=SimpleNamespace(update_appium_settings=seen.append))))
    result = asyncio.run(expose_api.update_appium_settings("s1", AppiumSettingsUpdate(settings={"autoClickAlertSelector": "x"})))
    assert seen == [{"autoClickAlertSelector": "x"}]
    assert result == {"settings": ["autoClickAlertSelector"]}


def test_a_driver_that_cannot_change_settings_is_refused_and_a_device_refusal_is_reported(monkeypatch):
    _running(monkeypatch, SimpleNamespace(driver=SimpleNamespace(active_instance=SimpleNamespace())))
    with pytest.raises(HTTPException) as refused:
        asyncio.run(expose_api.update_appium_settings("s1", AppiumSettingsUpdate(settings={"a": 1})))
    assert refused.value.status_code == 400

    def refuse(settings):
        raise RuntimeError("no such setting")

    _running(monkeypatch, SimpleNamespace(driver=SimpleNamespace(active_instance=SimpleNamespace(update_appium_settings=refuse))))
    with pytest.raises(HTTPException) as failed:
        asyncio.run(expose_api.update_appium_settings("s1", AppiumSettingsUpdate(settings={"a": 1})))
    assert failed.value.status_code == 500
    assert "no such setting" in failed.value.detail
