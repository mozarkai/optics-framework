"""A REST session start carries the project's popup rules and permissions into the session's Config."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from optics_framework.common import expose_api
from optics_framework.common.expose_api import AppiumSettingsUpdate, PopupRulesUpdate, SessionConfig, SessionModulesUpdate
from optics_framework.common.models import ElementData
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


def test_an_unknown_permission_name_stops_the_start_as_a_client_error(monkeypatch):
    with pytest.raises(HTTPException) as caught:
        _start(monkeypatch, permissions={"telepathy": "allow"})
    assert caught.value.status_code == 400
    assert "Unknown permission 'telepathy'" in str(caught.value.detail)


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
    call = expose_api.replace_popup_rules("s1", PopupRulesUpdate(popup_rules=[{"name": "bad"}]))
    with pytest.raises(HTTPException):
        asyncio.run(call)
    assert session.config.popup_rules is keep


def test_appium_settings_reach_the_drivers_session(monkeypatch):
    seen = []
    _running(monkeypatch, SimpleNamespace(driver=SimpleNamespace(active_instance=SimpleNamespace(update_appium_settings=seen.append))))
    result = asyncio.run(expose_api.update_appium_settings("s1", AppiumSettingsUpdate(settings={"autoClickAlertSelector": "x"})))
    assert seen == [{"autoClickAlertSelector": "x"}]
    assert result == {"settings": ["autoClickAlertSelector"]}


def test_a_driver_that_cannot_change_settings_is_refused_and_a_device_refusal_is_reported(monkeypatch):
    _running(monkeypatch, SimpleNamespace(driver=SimpleNamespace(active_instance=SimpleNamespace())))
    call = expose_api.update_appium_settings("s1", AppiumSettingsUpdate(settings={"a": 1}))
    with pytest.raises(HTTPException) as refused:
        asyncio.run(call)
    assert refused.value.status_code == 400

    def refuse(settings):
        raise RuntimeError("no such setting")

    _running(monkeypatch, SimpleNamespace(driver=SimpleNamespace(active_instance=SimpleNamespace(update_appium_settings=refuse))))
    call = expose_api.update_appium_settings("s1", AppiumSettingsUpdate(settings={"a": 1}))
    with pytest.raises(HTTPException) as failed:
        asyncio.run(call)
    assert failed.value.status_code == 500
    assert "no such setting" in failed.value.detail


MODULES_CSV = "module_name,module_step,param_1\nDismiss Rate Us,press element,${maybe_later}\n"
ELEMENTS_CSV = "Element_Name,Element_ID\nmaybe_later,Maybe Later\n"


def test_a_session_started_with_modules_and_elements_can_run_them(monkeypatch):
    captured = {}

    def create_session(config, **kwargs):
        captured.update(kwargs)
        return "session-1"

    async def launch_app(session_id, request):
        return SimpleNamespace(data={"result": "driver-1"})

    monkeypatch.setattr(expose_api.session_manager, "create_session", create_session)
    monkeypatch.setattr(expose_api, "execute_keyword", launch_app)
    monkeypatch.setattr(expose_api, "reconfigure_logging", lambda config: None)
    asyncio.run(expose_api.create_session(SessionConfig(modules_csv=MODULES_CSV, elements_csv=ELEMENTS_CSV)))
    assert captured["modules"].modules == {"Dismiss Rate Us": [("press element", ["${maybe_later}"])]}
    assert captured["elements"].elements == {"maybe_later": ["Maybe Later"]}


def test_the_modules_of_a_running_session_are_replaced_and_its_elements_added_to(monkeypatch):
    session = SimpleNamespace(modules=None, elements=ElementData(elements={"kept": ["Kept"]}))
    _running(monkeypatch, session)
    result = asyncio.run(expose_api.replace_session_modules("s1", SessionModulesUpdate(modules_csv=MODULES_CSV, elements_csv=ELEMENTS_CSV)))
    assert result == {"modules": 1, "elements": 1}
    assert list(session.modules.modules) == ["Dismiss Rate Us"]
    assert session.elements.elements == {"kept": ["Kept"], "maybe_later": ["Maybe Later"]}
    asyncio.run(expose_api.replace_session_modules("s1", SessionModulesUpdate()))
    assert session.modules is None, "empty text removes the modules"


def test_an_unknown_session_is_a_404_for_the_modules_route(monkeypatch):
    monkeypatch.setattr(expose_api.session_manager, "get_session", lambda session_id: None)
    call = expose_api.replace_session_modules("nope", SessionModulesUpdate())
    with pytest.raises(HTTPException) as caught:
        asyncio.run(call)
    assert caught.value.status_code == 404
