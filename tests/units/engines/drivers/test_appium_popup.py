"""AppiumScreen: what the popup guard reads from one Appium page source."""
import pytest

from optics_framework.common.models import PopupDetect
from optics_framework.engines.drivers.appium_popup import AppiumScreen

TEXT_VIEW, BUTTON = "android.widget.TextView", "android.widget.Button"


def node(tag=TEXT_VIEW, **attrs):
    return f"<{tag} " + " ".join(f'{key.replace("_", "-")}="{value}"' for key, value in attrs.items()) + "/>"


def android(*children):
    return "<hierarchy>" + "".join(children) + "</hierarchy>"


PLAIN = android(node(text="Balance", package="in.app"))
RATE_US = android(
    node(text="Balance", package="in.app"), node(resource_id="android:id/alertTitle", text="Rate Us"),
    node(BUTTON, text="Maybe Later", bounds="[100,200][300,260]"), node(BUTTON, text="Rate Now", bounds="[400,200][600,260]"),
)
UPDATE_PANEL = android(
    node(text="Balance", package="in.app"),
    '<android.widget.LinearLayout resource-id="in.app:id/parentPanel">'
    + node(text="Update ready") + node(BUTTON, resource_id="android:id/button1", text="Install")
    + "</android.widget.LinearLayout>",
)
CAMERA_PROMPT = android(
    "<android.widget.FrameLayout package='com.android.permissioncontroller'><n text='Allow Camera?'/></android.widget.FrameLayout>"
)
IOS_ALERT = (
    "<AppiumAUT><XCUIElementTypeApplication name='Maps'/>"
    "<XCUIElementTypeAlert name='Allow notifications?' label='Allow notifications?'>"
    "<XCUIElementTypeButton name='Allow' label='Allow' x='10' y='20' width='100' height='40'/>"
    "</XCUIElementTypeAlert></AppiumAUT>"
)
IOS_APP = "<AppiumAUT><XCUIElementTypeApplication name='Maps'/></AppiumAUT>"


class TestReadable:
    @pytest.mark.parametrize(("source", "readable"), [
        pytest.param(PLAIN, True, id="an Android page source"),
        pytest.param("not xml", False, id="text that is not XML"),
        pytest.param(None, False, id="no page source"),
    ])
    def test_readable(self, source, readable):
        assert AppiumScreen(source).readable is readable


class TestPlatform:
    @pytest.mark.parametrize(("source", "platform"), [
        pytest.param(PLAIN, "android", id="an Android hierarchy"),
        pytest.param(IOS_APP, "ios", id="an iOS AppiumAUT root"),
        pytest.param("<XCUIElementTypeApplication name='Maps'/>", "ios", id="an iOS element as the root"),
        pytest.param("not xml", None, id="text that is not XML"),
    ])
    def test_platform(self, source, platform):
        assert AppiumScreen(source).platform == platform


class TestAlertsOutsideSource:
    @pytest.mark.parametrize(("source", "outside"), [
        pytest.param(PLAIN, False, id="Android"),
        pytest.param(IOS_APP, True, id="iOS"),
        pytest.param(None, True, id="no page source"),
    ])
    def test_alerts_outside_source(self, source, outside):
        assert AppiumScreen(source).alerts_outside_source is outside


class TestAppId:
    @pytest.mark.parametrize(("source", "app_id"), [
        pytest.param(PLAIN, "in.app", id="the app's package"),
        pytest.param(CAMERA_PROMPT, None, id="only a system package"),
        pytest.param(IOS_ALERT, "Maps", id="the iOS application's name"),
        pytest.param(None, None, id="no page source"),
    ])
    def test_app_id(self, source, app_id):
        assert AppiumScreen(source).app_id == app_id


class TestDialog:
    @pytest.mark.parametrize(("source", "dialog"), [
        pytest.param(PLAIN, (False, "in_app", "Balance", "Balance"), id="a screen with no dialog"),
        pytest.param(RATE_US, (True, "in_app", "Rate Us", "Rate Us"), id="an in-app dialog title"),
        pytest.param(UPDATE_PANEL, (True, "in_app", "Update ready Install", "Update ready"), id="an in-app dialog panel"),
        pytest.param(CAMERA_PROMPT, (True, "system_alert", "Allow Camera?", "Allow Camera?"), id="an Android permission prompt"),
        pytest.param(IOS_ALERT, (True, "system_alert", "Allow notifications? Allow", "Allow notifications?"), id="an iOS alert"),
        pytest.param("not xml", (False, "in_app", "", ""), id="text that is not XML"),
    ])
    def test_has_dialog_kind_text_and_title(self, source, dialog):
        screen = AppiumScreen(source)
        assert (screen.has_dialog(), screen.kind(), screen.popup_text(), screen.popup_title()) == dialog

    def test_popup_text_of_one_text_under_several_attributes_and_spaces(self):
        assert AppiumScreen(android(node(text="  Rate   Us "), node(content_desc="Rate Us"))).popup_text() == "Rate Us"

    def test_popup_text_longer_than_the_limit(self):
        assert AppiumScreen(android(node(text="x" * 250))).popup_text() == "x" * 200


class TestPointOf:
    @pytest.mark.parametrize(("target", "point"), [
        pytest.param("Maybe Later", (200, 230), id="visible text"),
        pytest.param("maybe  LATER", (200, 230), id="text in other case and spacing"),
        pytest.param("text=Rate Now", (500, 230), id="a text= locator"),
        pytest.param("text_only:Maybe Later", (200, 230), id="a text_only: locator"),
        pytest.param("xpath=//android.widget.Button[@text='Rate Now']", (500, 230), id="an xpath= locator"),
        pytest.param("//*[@text='Maybe Later']", (200, 230), id="a bare XPath"),
        pytest.param("xpath=//*[", None, id="an XPath that is not valid"),
        pytest.param("Balance", None, id="a node without bounds"),
        pytest.param("Not on screen", None, id="text not on the screen"),
    ])
    def test_point_of_on_a_dialog(self, target, point):
        assert AppiumScreen(RATE_US).point_of(target) == point

    @pytest.mark.parametrize(("source", "target", "point"), [
        pytest.param(android(node(BUTTON, resource_id="in.app:id/later", bounds="[100,200][300,260]")), "in.app:id/later",
                     (200, 230), id="a resource-id"),
        pytest.param(android(node(BUTTON, resource_id="in.app:id/later", bounds="[100,200][300,260]")), "id:later",
                     (200, 230), id="an id: locator"),
        pytest.param(android(node(BUTTON, text="Later", bounds="[100,200][300,260]", displayed="false"),
                             node(BUTTON, text="Later", bounds="[0,0][10,10]")), "Later", (5, 5), id="the shown one of two"),
        pytest.param(android(node(BUTTON, text="Later", bounds="[100,200][300,260]", enabled="false")), "Later", None,
                     id="a disabled node"),
        pytest.param(android(node(BUTTON, text="Later", bounds="[100,200][100,260]")), "Later", None, id="a node with no width"),
        pytest.param(IOS_ALERT, "Allow", (60, 40), id="an iOS node's x, y, width and height"),
        pytest.param(android(node(BUTTON, text="Later", x="1", y="2", width="0", height="4")), "Later", None,
                     id="x, y, width and height with no width"),
        pytest.param(None, "Allow", None, id="no page source"),
    ])
    def test_point_of(self, source, target, point):
        assert AppiumScreen(source).point_of(target) == point


class TestMatches:
    @pytest.mark.parametrize(("detect", "matched", "missed"), [
        pytest.param({"text_contains": ["RATE us"]}, ["text_contains"], "", id="text in other case"),
        pytest.param({"text_contains": ["nothing", "rate"]}, ["text_contains"], "", id="one of several texts"),
        pytest.param({"text_contains": ["nothing"]}, None, "text_contains", id="text not on the screen"),
        pytest.param({"resource_id": "android:id/alertTitle"}, ["resource_id"], "", id="a resource-id"),
        pytest.param({"resource_id": "in.app:id/other"}, None, "resource_id", id="a resource-id not on the screen"),
        pytest.param({"xpath": "//android.widget.Button[@text='Rate Now']"}, ["xpath"], "", id="an XPath"),
        pytest.param({"xpath": "//android.widget.Button[@text='Nope']"}, None, "xpath", id="an XPath that finds nothing"),
        pytest.param({"text_contains": ["Rate Us"], "resource_id": "android:id/alertTitle", "xpath": "//*[@text='Rate Now']"},
                     ["text_contains", "resource_id", "xpath"], "", id="every key matches"),
        pytest.param({"text_contains": ["Rate Us"], "xpath": "//*[@text='Nope']"}, None, "xpath", id="one key of two misses"),
    ])
    def test_matches(self, detect, matched, missed):
        screen = AppiumScreen(RATE_US)
        assert screen.matches(PopupDetect(**detect)) == matched
        assert screen.missed == missed

    def test_matches_an_xpath_on_no_page_source(self):
        screen = AppiumScreen(None)
        assert screen.matches(PopupDetect(xpath="//*")) is None
        assert screen.missed == "xpath"


class TestAddAlert:
    def test_add_alert_to_no_page_source(self):
        screen = AppiumScreen(None)
        screen.add_alert("Allow?", ["Allow", "Deny"])
        assert screen.raw == (
            '<AppiumAUT><XCUIElementTypeAlert name="Allow?" label="Allow?">'
            '<XCUIElementTypeButton name="Allow" label="Allow"/><XCUIElementTypeButton name="Deny" label="Deny"/>'
            "</XCUIElementTypeAlert></AppiumAUT>"
        )
        assert (screen.platform, screen.kind(), screen.popup_text()) == ("ios", "system_alert", "Allow? Allow Deny")

    def test_add_alert_to_an_app_screen(self):
        screen = AppiumScreen(IOS_APP)
        screen.add_alert("Allow?", ["Allow"])
        assert screen.raw == (
            '<AppiumAUT><XCUIElementTypeApplication name="Maps"/><XCUIElementTypeAlert name="Allow?" label="Allow?">'
            '<XCUIElementTypeButton name="Allow" label="Allow"/></XCUIElementTypeAlert></AppiumAUT>'
        )
        assert (screen.app_id, screen.popup_title()) == ("Maps", "Allow?")
