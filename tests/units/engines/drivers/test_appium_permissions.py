from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.drivers.appium import _ANDROID_PERMISSIONS, Appium

pytestmark = pytest.mark.white_box

(CAMERA,) = _ANDROID_PERMISSIONS["camera"]


def _driver(platform="Android", permissions=None, package: str | None = "in.app"):
    webdriver = MagicMock()
    webdriver.capabilities = {"platformName": platform, **({"appPackage": package} if package else {})}
    instance = Appium.__new__(Appium)
    instance.driver = webdriver
    instance.capabilities = {}
    instance.event_sdk = SimpleNamespace(config_handler=SimpleNamespace(config=SimpleNamespace(permissions=permissions or {})))
    return instance


def _calls(instance):
    return [call.args for call in instance.driver.execute_script.call_args_list]


def test_allow_grants_and_deny_revokes_for_the_app_under_test():
    instance = _driver(permissions={"camera": "allow", "notifications": "deny"})
    instance.apply_permissions()
    assert _calls(instance) == [
        ("mobile: changePermissions", {"action": "grant", "appPackage": "in.app", "permissions": [CAMERA]}),
        ("mobile: changePermissions", {"action": "revoke", "appPackage": "in.app",
                                       "permissions": list(_ANDROID_PERMISSIONS["notifications"])}),
    ]


def test_an_unknown_permission_name_is_an_error_and_nothing_is_set():
    instance = _driver(permissions={"camera": "allow", "teleport": "allow"})
    with pytest.raises(OpticsError, match="Unknown permission 'teleport'") as caught:
        instance.apply_permissions()
    assert caught.value.code == Code.E0501
    assert _calls(instance) == [("mobile: changePermissions", {
        "action": "grant", "appPackage": "in.app", "permissions": [CAMERA]})], "the ones before it were already set"


def test_on_ios_permissions_set_nothing_and_say_where_the_prompt_rule_goes(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr("optics_framework.engines.drivers.appium.internal_logger", log)
    instance = _driver(platform="iOS", permissions={"camera": "allow", "location": "deny"})
    instance.apply_permissions()
    assert not instance.driver.update_settings.called
    assert _calls(instance) == []
    (message,), _ = log.warning.call_args
    assert "autoClickAlertSelector" in message
    assert "iOS" in message


def test_attaching_to_a_running_session_applies_the_permissions_too():
    instance = _driver(permissions={"camera": "allow"})
    instance.capabilities = {"platformName": "Android"}
    instance._cleanup_existing_driver = MagicMock()
    instance._apply_app_identifier_caps = MagicMock()
    instance._try_attach_or_clear_session_caps = MagicMock(return_value="attached")
    assert instance.start_session() == "attached"
    assert _calls(instance)[0][1]["action"] == "grant"


def test_a_new_android_session_opens_the_app_only_after_its_permissions_are_set():
    instance = _driver(permissions={"camera": "allow"})
    instance.capabilities = {"platformName": "Android", "appPackage": "in.app", "appActivity": ".Main"}
    options = MagicMock()
    instance._cleanup_existing_driver = MagicMock()
    instance._apply_app_identifier_caps = MagicMock()
    instance._try_attach_or_clear_session_caps = MagicMock(return_value=None)
    instance._get_platform_and_options = MagicMock(return_value=(options, {}))
    instance._create_new_driver_session = MagicMock(return_value="sid-1")
    assert instance.start_session() == "sid-1"
    options.set_capability.assert_any_call("appium:autoLaunch", False)
    assert [c[0] + " " + c[1].get("action", "") for c in _calls(instance)] == [
        "mobile: changePermissions grant", "mobile: startActivity ",
    ]
    assert _calls(instance)[1][1] == {"intent": "in.app/.Main"}


def test_a_permission_the_app_does_not_declare_does_not_stop_the_others():
    instance = _driver(permissions={"location": "allow", "camera": "allow"})
    instance.driver.execute_script.side_effect = [RuntimeError("not a changeable permission type"), None, None]
    instance.apply_permissions()
    assert len(_calls(instance)) == 3
