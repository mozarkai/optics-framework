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


def test_a_name_that_stands_for_several_permissions_sets_each_one():
    instance = _driver(permissions={"Location": "allow"})
    instance.apply_permissions()
    assert [c[1]["permissions"] for c in _calls(instance)] == [
        [permission] for permission in _ANDROID_PERMISSIONS["location"]]


@pytest.mark.parametrize("name", _ANDROID_PERMISSIONS)
def test_each_name_sets_the_permissions_it_stands_for(name):
    instance = _driver(permissions={name: "allow"})
    instance.apply_permissions()
    assert [c[1]["permissions"][0] for c in _calls(instance)] == list(_ANDROID_PERMISSIONS[name])


def test_a_full_permission_name_is_used_as_it_is():
    full_name = _ANDROID_PERMISSIONS["sms"][1]
    instance = _driver(permissions={full_name: "allow"})
    instance.apply_permissions()
    assert _calls(instance)[0][1]["permissions"] == [full_name]


def test_an_unknown_permission_name_is_an_error_and_nothing_is_set():
    instance = _driver(permissions={"camera": "allow", "teleport": "allow"})
    with pytest.raises(OpticsError, match="Unknown permission 'teleport'") as caught:
        instance.apply_permissions()
    assert caught.value.code == Code.E0501 and _calls(instance) == [("mobile: changePermissions", {
        "action": "grant", "appPackage": "in.app", "permissions": [CAMERA]})], "the ones before it were already set"


def test_a_permission_the_app_does_not_declare_does_not_stop_the_others():
    instance = _driver(permissions={"location": "allow", "camera": "allow"})
    instance.driver.execute_script.side_effect = [RuntimeError("not a changeable permission type"), None, None]
    instance.apply_permissions()
    assert len(_calls(instance)) == 3


def test_without_an_app_package_nothing_is_applied():
    instance = _driver(permissions={"camera": "allow"}, package=None)
    instance.apply_permissions()
    assert _calls(instance) == []


@pytest.mark.parametrize("permissions", [{}, None, MagicMock()])
def test_no_permissions_means_no_commands(permissions):
    instance = _driver()
    instance.event_sdk = SimpleNamespace(config_handler=SimpleNamespace(config=SimpleNamespace(permissions=permissions)))
    instance.apply_permissions()
    assert _calls(instance) == []


def _ios_selector(instance):
    (settings,), _ = instance.driver.update_settings.call_args
    return settings["autoClickAlertSelector"]


def test_on_ios_allow_makes_wda_press_the_allow_buttons_and_grants_nothing():
    instance = _driver(platform="iOS", permissions={"camera": "allow", "location": "allow"})
    instance.apply_permissions()
    instance.driver.update_settings.assert_called_once()
    selector = _ios_selector(instance)
    assert selector.startswith("**/XCUIElementTypeButton[`") and 'label == "Allow While Using App"' in selector
    assert "Don" not in selector and _calls(instance) == []


def test_on_ios_deny_names_the_buttons_that_keep_it_off_including_the_follow_up_alert():
    instance = _driver(platform="iOS", permissions={"location": "deny"})
    instance.apply_permissions()
    selector = _ios_selector(instance)
    assert 'label == "Don’t Allow"' in selector and 'label == "Keep Location Services Off"' in selector
    assert "Allow While Using App" not in selector


def test_on_ios_a_mix_of_allow_and_deny_is_an_error_since_the_prompt_is_answered_by_its_button():
    instance = _driver(platform="iOS", permissions={"camera": "allow", "location": "deny"})
    with pytest.raises(OpticsError, match="every one 'deny'") as caught:
        instance.apply_permissions()
    assert caught.value.code == Code.E0501 and not instance.driver.update_settings.called


def test_on_ios_a_failed_setting_is_logged_and_the_session_carries_on():
    instance = _driver(platform="iOS", permissions={"camera": "allow"})
    instance.driver.update_settings.side_effect = RuntimeError("no such setting")
    instance.apply_permissions()


def test_a_new_session_applies_the_permissions_and_still_returns_its_id():
    instance = _driver(permissions={"camera": "allow"})
    instance.capabilities = {"platformName": "Android"}
    instance._cleanup_existing_driver = MagicMock()
    instance._apply_app_identifier_caps = MagicMock()
    instance._try_attach_or_clear_session_caps = MagicMock(return_value=None)
    instance._get_platform_and_options = MagicMock(return_value=(MagicMock(), {}))
    instance._create_new_driver_session = MagicMock(return_value="sid-1")
    assert instance.start_session() == "sid-1"
    assert _calls(instance)[0][1]["action"] == "grant"


def test_attaching_to_a_running_session_leaves_its_permissions_alone():
    instance = _driver(permissions={"camera": "allow"})
    instance.capabilities = {"platformName": "Android"}
    instance._cleanup_existing_driver = MagicMock()
    instance._apply_app_identifier_caps = MagicMock()
    instance._try_attach_or_clear_session_caps = MagicMock(return_value="attached")
    assert instance.start_session() == "attached"
    assert _calls(instance) == []


def test_answer_alert_presses_the_named_button_on_ios():
    instance = _driver(platform="iOS")
    instance.answer_alert("Allow")
    instance.driver.execute_script.assert_called_once_with("mobile: alert", {"action": "accept", "buttonLabel": "Allow"})


def test_answer_alert_is_refused_on_android():
    with pytest.raises(OpticsError) as caught:
        _driver().answer_alert("Allow")
    assert caught.value.code == Code.E0105


def test_read_alert_returns_the_text_and_buttons_of_the_system_alert_on_ios():
    instance = _driver(platform="iOS")
    instance.driver.execute_script.return_value = ["Allow Once", "Don’t Allow"]
    instance.driver.switch_to.alert.text = "Allow “Maps” to use your location?"
    assert instance.read_alert() == ("Allow “Maps” to use your location?", ["Allow Once", "Don’t Allow"])
    instance.driver.execute_script.assert_called_once_with("mobile: alert", {"action": "getButtons"})


def test_read_alert_is_none_when_no_alert_is_open():
    instance = _driver(platform="iOS")
    instance.driver.execute_script.side_effect = RuntimeError("An attempt was made to operate on a modal dialog when one was not open")
    assert instance.read_alert() is None


def test_read_alert_is_refused_on_android_without_asking_the_device():
    with pytest.raises(OpticsError) as caught:
        _driver().read_alert()
    assert caught.value.code == Code.E0105
