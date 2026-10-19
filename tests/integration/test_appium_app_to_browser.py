"""Follows an SMS link from Messages into Chrome and back; wipes app data, so only on a disposable emulator.

    appium --allow-insecure=uiautomator2:chromedriver_autodownload
    OPTICS_E2E_EMULATOR=emulator-5554 pytest tests/integration/test_appium_app_to_browser.py
"""

import functools
import http.server
import json
import os
import pathlib
import shutil
import subprocess  # nosec
import threading
import urllib.request

import pytest

from optics_framework.helper.execute import run_project

pytestmark = pytest.mark.black_box

APPIUM_URL = os.environ.get("OPTICS_E2E_APPIUM_URL", "http://127.0.0.1:4723")
FIXTURE = pathlib.Path(__file__).parent / "mobile_web_fixture"
MESSAGES = "com.google.android.apps.messaging"
CHROME = "com.android.chrome"

CONFIG = """\
driver_sources:
  - appium:
      enabled: true
      url: "{url}"
      capabilities:
        platformName: Android
        automationName: UiAutomator2
        udid: {udid}
        deviceName: {udid}
elements_sources:
  - appium_find_element:
      enabled: true
  - appium_page_source:
      enabled: true
  - appium_web:
      enabled: true
  - appium_screenshot:
      enabled: true
"""

TEST_CASES = """\
test_case,test_step
Suite Setup,Open Messages
Order Link,Journey
"""


def _adb(serial, *args):
    return subprocess.run(["adb", "-s", serial, *args], capture_output=True, text=True, check=True).stdout  # nosec


@pytest.fixture(scope="module")
def emulator():
    serial = os.environ.get("OPTICS_E2E_EMULATOR")
    if not serial:
        pytest.skip("Set OPTICS_E2E_EMULATOR to a disposable emulator; the test clears Messages and Chrome data")
    if not serial.startswith("emulator-"):
        pytest.skip(f"{serial} is not an emulator; the test sends its SMS through the emulator console")
    try:
        urllib.request.urlopen(f"{APPIUM_URL}/status", timeout=3)  # nosec
    except OSError as e:
        pytest.skip(f"No Appium server at {APPIUM_URL}: {e}")
    if shutil.which("adb") is None:
        pytest.skip("adb is not on PATH")
    devices = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout  # nosec
    serials = [line.split()[0] for line in devices.splitlines()[1:] if line.endswith("\tdevice")]
    if serial not in serials:
        pytest.skip(f"{serial} is not booted")
    return serial


@pytest.fixture(scope="module")
def site(emulator):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    _adb(emulator, "reverse", f"tcp:{port}", f"tcp:{port}")
    yield f"http://localhost:{port}"
    _adb(emulator, "reverse", "--remove", f"tcp:{port}")
    server.shutdown()


@pytest.fixture
def order_link(emulator, site):
    """Fresh Messages and Chrome, and an SMS that is only a one-line link, so a centre tap opens it."""
    for package in (MESSAGES, CHROME):
        _adb(emulator, "shell", "pm", "clear", package)
    # Chrome asks for notifications at a varying delay after a wipe, sometimes over the page.
    _adb(emulator, "shell", "pm", "grant", CHROME, "android.permission.POST_NOTIFICATIONS")
    link = f"{site}/"
    _adb(emulator, "emu", "sms", "send", "5551234", link)
    return link


def _project(tmp_path, emulator, link, steps):
    modules = [
        "module_name,module_step,param_1,param_2",
        f"Open Messages,Launch App,{MESSAGES}",
        # Messages and Chrome take up to half a minute to show first-run screens after a wipe.
        "Open Messages,Detect And Press,Use Messages without an account,40",
        f"Journey,Press Element,{link}",
        "Journey,Sleep,2",
        f"Journey,Press Element,{link}",
        "Journey,Detect And Press,Stay signed out,40",
        *(f"Journey,{step}" for step in steps),
    ]
    files = {
        "config.yaml": CONFIG.format(url=APPIUM_URL, udid=emulator),
        "test_cases/test_cases.csv": TEST_CASES,
        "modules/modules.csv": "\n".join(modules) + "\n",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(tmp_path)


def test_app_script_continues_into_chrome_and_back(tmp_path, emulator, order_link):
    project = _project(tmp_path, emulator, order_link, [
        "Assert Presence,Optics Shop|css=#email,20,all",
        "Enter Text,css=#email,ava@example.com",
        "Press Element,//button[@type='submit']",
        "Assert Visibility,css=#welcome,10",
        "Assert Presence,\"Welcome, ava@example.com\",10",
        "Go Back",
        f"Assert Presence,{order_link},20",
    ])

    assert run_project(project, use_printer=False) is True


def test_pressing_a_hidden_web_element_fails(tmp_path, emulator, order_link):
    project = _project(tmp_path, emulator, order_link, [
        "Assert Presence,css=#promo,20",
        "Press Element,css=#promo",
    ])

    assert run_project(project, use_printer=False) is False
    report = (tmp_path / "execution_output" / "junit_output.xml").read_text(encoding="utf-8")
    assert "'css=#promo' is in the page but not displayed" in report


def test_screen_elements_list_the_whole_page_with_locators(tmp_path, emulator, order_link):
    project = _project(tmp_path, emulator, order_link, [
        "Assert Presence,css=#email,20",
        "Get Interactive Elements,compact=true",
    ])

    assert run_project(project, use_printer=False) is True
    elements = json.loads((tmp_path / "execution_output" / "interactable_elements.json").read_text(encoding="utf-8"))
    by_loc = {element.get("loc"): element for element in elements}
    assert "offscreen" not in by_loc["xpath=//h1[normalize-space()='Optics Shop']"]
    # The form sits below a tall spacer, so it is listed but flagged for scrolling.
    assert by_loc["css=#email"]["offscreen"] is True
    assert by_loc["xpath=//button[normalize-space()='Sign in']"]["offscreen"] is True
    # Chrome's accessibility mirror of the heading is replaced; the WebView itself stays, labelled
    # with the page title, as the container to scroll.
    assert [element["act"] for element in elements if element["label"] == "Optics Shop"] == [["scroll"], []]


def test_full_screen_elements_keep_the_webview_to_scroll(tmp_path, emulator, order_link):
    project = _project(tmp_path, emulator, order_link, [
        "Assert Presence,css=#email,20",
        "Get Screen Elements",
    ])

    assert run_project(project, use_printer=False) is True
    elements = json.loads((tmp_path / "execution_output" / "interactable_elements.json").read_text(encoding="utf-8"))
    # The page's html and body cover the native WebView, which must stay as the target to scroll.
    assert [element["extra"]["class"] for element in elements
            if element.get("xpath", "").startswith("//android.webkit.WebView")
            and element["extra"].get("class") == "android.webkit.WebView"] == ["android.webkit.WebView"]
    assert any(element.get("loc") == "css=#email" for element in elements)
