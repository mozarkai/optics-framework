"""Follows an app's link into Safari, a Safari sheet and an in-app WKWebView on an iOS simulator.

    OPTICS_E2E_SIMULATOR=<simulator udid> pytest tests/integration/test_appium_ios_app_to_browser.py
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
APP_SOURCE = pathlib.Path(__file__).parent / "mobile_web_ios_app"
BUNDLE_ID = "org.opticsframework.links"

CONFIG = """\
driver_sources:
  - appium:
      enabled: true
      url: "{url}"
      capabilities:
        platformName: iOS
        automationName: XCUITest
        udid: {udid}
        bundleId: {bundle}
        processArguments:
          args: ["-fixtureURL", "{site}/{page}", "-inspectable", "YES"]
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
Suite Setup,Open Links
Journey,Journey
"""


def _xcrun(*args):
    return subprocess.run(["xcrun", *args], capture_output=True, text=True, check=True).stdout  # nosec


@pytest.fixture(scope="module")
def simulator(tmp_path_factory):
    udid = os.environ.get("OPTICS_E2E_SIMULATOR")
    if not udid:
        pytest.skip("Set OPTICS_E2E_SIMULATOR to the udid of a booted iOS simulator")
    if shutil.which("xcrun") is None:
        pytest.skip("xcrun is not on PATH")
    try:
        urllib.request.urlopen(f"{APPIUM_URL}/status", timeout=3)  # nosec
    except OSError as e:
        pytest.skip(f"No Appium server at {APPIUM_URL}: {e}")
    if udid not in _xcrun("simctl", "list", "devices", "booted"):
        pytest.skip(f"Simulator {udid} is not booted")
    app = tmp_path_factory.mktemp("ios_app") / "OpticsLinks.app"
    app.mkdir()
    shutil.copy(APP_SOURCE / "Info.plist", app / "Info.plist")
    _xcrun("-sdk", "iphonesimulator", "swiftc", "-parse-as-library", "-target", "arm64-apple-ios17.0-simulator",
           str(APP_SOURCE / "OpticsLinks.swift"), "-o", str(app / "OpticsLinks"))
    subprocess.run(["codesign", "-s", "-", str(app)], capture_output=True, check=True)  # nosec
    _xcrun("simctl", "install", udid, str(app))
    return udid


@pytest.fixture(scope="module")
def site(simulator):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def fresh_apps(simulator):
    for bundle in (BUNDLE_ID, "com.apple.mobilesafari"):
        subprocess.run(["xcrun", "simctl", "terminate", simulator, bundle], capture_output=True)  # nosec
    return simulator


def _project(tmp_path, simulator, site, steps, page=""):
    modules = [
        "module_name,module_step,param_1,param_2,param_3",
        f"Open Links,Launch App,{BUNDLE_ID}",
        *(f"Journey,{step}" for step in steps),
    ]
    files = {
        "config.yaml": CONFIG.format(url=APPIUM_URL, udid=simulator, bundle=BUNDLE_ID, site=site, page=page),
        "test_cases/test_cases.csv": TEST_CASES,
        "modules/modules.csv": "\n".join(modules) + "\n",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(tmp_path)


def _junit(tmp_path):
    return (tmp_path / "execution_output" / "junit_output.xml").read_text(encoding="utf-8")


@pytest.mark.parametrize("surface", ["Open in Safari", "Open in Safari View", "Open in Web View"])
def test_app_script_continues_into_web_content(tmp_path, fresh_apps, site, surface):
    project = _project(tmp_path, fresh_apps, site, [
        f"Press Element,{surface}",
        "Assert Presence,Optics Shop|css=#email,20,all",
        "Enter Text,css=#email,ava@example.com",
        "Press Element,//button[@type='submit']",
        "Assert Visibility,css=#welcome,10",
        "Assert Presence,\"Welcome, ava@example.com\",10",
    ])

    assert run_project(project, use_printer=False) is True, _junit(tmp_path)


def test_returns_from_safari_to_the_app(tmp_path, fresh_apps, site):
    project = _project(tmp_path, fresh_apps, site, [
        "Press Element,Open in Safari",
        "Assert Presence,css=#email,20",
        f"Launch App,{BUNDLE_ID}",
        "Assert Presence,Open in Safari View,10",
        "Assert Presence,css=#email,3",
    ])

    assert run_project(project, use_printer=False) is False
    # Safari is behind the app now, so its page is no longer the one on screen.
    assert "['css=#email'] not found" in _junit(tmp_path)


def test_pressing_a_hidden_web_element_fails(tmp_path, fresh_apps, site):
    project = _project(tmp_path, fresh_apps, site, [
        "Press Element,Open in Safari",
        "Assert Presence,css=#promo,20",
        "Press Element,css=#promo",
    ])

    assert run_project(project, use_printer=False) is False
    assert "'css=#promo' is in the page but not displayed" in _junit(tmp_path)


def test_missing_web_locators_fail_without_long_waits(tmp_path, fresh_apps, site):
    project = _project(tmp_path, fresh_apps, site, [
        "Press Element,Open in Safari",
        "Assert Presence,css=#email,20",
        "Press Element,css=#nowhere",
    ])

    assert run_project(project, use_printer=False) is False
    report = _junit(tmp_path)
    assert "'css=#nowhere' matched 0 web element(s)" in report
    press = report.split('name="Press Element" status="FAIL"')[1]
    assert float(press.split('elapsed="')[1].split('"')[0]) < 3, press


def test_finds_elements_in_either_of_two_web_views(tmp_path, fresh_apps, site):
    project = _project(tmp_path, fresh_apps, site, [
        "Press Element,Open Two Web Views",
        "Assert Presence,css=#deal|css=#email,20,all",
        "Press Element,css=#deal",
        "Assert Presence,Deal claimed,10",
        "Enter Text,css=#email,ava@example.com",
        "Press Element,//button[@type='submit']",
        "Assert Visibility,css=#welcome,10",
    ])

    assert run_project(project, use_printer=False) is True, _junit(tmp_path)


def test_presses_inside_a_modal_dialog(tmp_path, fresh_apps, site):
    project = _project(tmp_path, fresh_apps, site, [
        "Press Element,Open in Web View",
        "Assert Presence,css=#accept,20",
        "Press Element,css=#accept",
        "Assert Presence,Cookies accepted,10",
    ], page="modal.html")

    assert run_project(project, use_printer=False) is True, _junit(tmp_path)


def test_screen_elements_list_the_whole_page_with_locators(tmp_path, fresh_apps, site):
    project = _project(tmp_path, fresh_apps, site, [
        "Press Element,Open in Safari",
        "Assert Presence,css=#email,20",
        "Get Interactive Elements,compact=true",
    ])

    assert run_project(project, use_printer=False) is True, _junit(tmp_path)
    elements = json.loads((tmp_path / "execution_output" / "interactable_elements.json").read_text(encoding="utf-8"))
    by_loc = {element.get("loc"): element for element in elements}
    assert "offscreen" not in by_loc["xpath=//h1[normalize-space()='Optics Shop']"]
    assert by_loc["css=#email"]["offscreen"] is True
    # Safari's accessibility mirror of the heading is replaced by the web entry.
    assert [element["act"] for element in elements if element["label"] == "Optics Shop"] == [[]]
