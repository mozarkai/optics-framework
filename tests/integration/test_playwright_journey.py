"""Web journeys against a local fixture site in headless Chromium."""

import asyncio
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys

import pytest

playwright_api = pytest.importorskip("playwright.async_api")

from optics_framework.helper.execute import run_project  # noqa: E402

pytestmark = pytest.mark.black_box


@pytest.fixture(autouse=True)
def _local_full_mcp(monkeypatch):
    """The in-process MCP client is local, like stdio, and calls keyword tools only the full toolset has."""
    from optics_framework.common import expose_api
    monkeypatch.setattr(expose_api, "server_reads_allowed", True)
    monkeypatch.setenv("OPTICS_MCP_TOOLSET", "full")


FIXTURE = pathlib.Path(__file__).parent / "web_fixture"
STORE_URL = (FIXTURE / "index.html").as_uri()

CONFIG = """\
driver_sources:
  - playwright:
      enabled: true
      capabilities:
        headless: true
        element_timeout_ms: 3000
elements_sources:
  - playwright_find_element:
      enabled: true
  - playwright_page_source:
      enabled: true
  - playwright_screenshot:
      enabled: true
"""


@pytest.fixture(scope="module", autouse=True)
def _require_chromium():
    async def launch():
        async with playwright_api.async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            await browser.close()
    try:
        asyncio.run(launch())
    except Exception as e:
        if os.environ.get("CI"):
            raise
        pytest.skip(f"Chromium is not installed for Playwright: {e}")


def _project(tmp_path, steps, config=CONFIG):
    modules = [
        "module_name,module_step,param_1,param_2",
        f"Open Store,Launch App,{STORE_URL}",
        *(f"Journey,{step}" for step in steps),
        "Close Store,Close And Terminate App",
    ]
    files = {
        "config.yaml": config,
        "test_cases/test_cases.csv": TEST_CASES,
        "modules/modules.csv": "\n".join(modules) + "\n",
        "test_data/elements.csv": ELEMENTS,
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(tmp_path)


TEST_CASES = """\
test_case,test_step
Suite Setup,Open Store
Shop,Journey
Suite Teardown,Close Store
"""

ELEMENTS = """\
Element_Name,Element_ID,Element_ID_2
email_field,Email,
password_field,"[data-testid=""password""]",
sign_in,#stale-sign-in-id,Sign in
welcome,#welcome,
orders_link,"role=link[name=""Orders""]",
orders_heading,text=Your orders,
"""


def test_suite_replays_a_sign_in_journey(tmp_path):
    project = _project(tmp_path, [
        "Enter Text,${email_field},ava@example.com",
        "Enter Text,${password_field},@:secret",
        "Press Element,${sign_in}",
        "Assert Presence,${welcome}",
        "Validate Element,Welcome",
        "Press Element,${orders_link}",
        "Assert Presence,${orders_heading}",
        "Go Back",
        "Assert Presence,Sign in",
    ])
    assert run_project(project, use_printer=False) is True


def test_a_missing_css_element_fails_the_run(tmp_path):
    project = _project(tmp_path, ["Assert Presence,#not-on-the-page,2"])
    assert run_project(project, use_printer=False) is False


def test_a_failed_step_leaves_the_screen_elements(tmp_path):
    project = _project(tmp_path, ["Press Element,#not-on-the-page"])

    assert run_project(project, use_printer=False) is False
    failures = json.loads((tmp_path / "execution_output" / "failed_steps_elements.json").read_text())
    assert [f["step"] for f in failures] == ["Press Element (#not-on-the-page)"]
    assert any(e.get("label") == "Sign in" for e in failures[0]["elements"])


PHONE_CONFIG = CONFIG.replace("headless: true\n", "headless: true\n        device: Pixel 7\n")


# Neither page sets a meta viewport, so the phone profile zooms them out and taps by hand.
@pytest.mark.parametrize("config", [CONFIG, PHONE_CONFIG], ids=["desktop", "zoomed_phone"])
def test_pressing_a_disabled_button_fails(tmp_path, config):
    project = _project(tmp_path, [
        f"Launch App,{(FIXTURE / 'orders.html').as_uri()}",
        "Press Element,Reorder",
    ], config=config)
    assert run_project(project, use_printer=False) is False


@pytest.mark.parametrize("config", [CONFIG, PHONE_CONFIG], ids=["desktop", "zoomed_phone"])
def test_pressing_a_covered_button_fails(tmp_path, config):
    project = _project(tmp_path, [
        f"Launch App,{(FIXTURE / 'covered.html').as_uri()}",
        "Press Element,Pay",
    ], config=config)
    assert run_project(project, use_printer=False) is False


def test_typing_into_a_caption_reaches_the_field_it_names(tmp_path):
    project = _project(tmp_path, [
        f"Launch App,{(FIXTURE / 'fields.html').as_uri()}",
        "Enter Text,Mobile Number,9876543210",
        "Enter Text,text=First Name,John",
        "Enter Text,Last Name,Doe",
        "Assert Presence,text=9876543210/John/Doe,2",
    ])

    assert run_project(project, use_printer=False) is True


def test_assert_text_reads_a_field_and_fails_on_a_wrong_value(tmp_path):
    fields = (FIXTURE / "fields.html").as_uri()
    steps = [f"Launch App,{fields}", "Enter Text,Mobile Number,9876543210"]
    right = _project(tmp_path / "right", [*steps, 'Assert Text,css=input[type="tel"],9876543210,2'])
    wrong = _project(tmp_path / "wrong", [*steps, 'Assert Text,css=input[type="tel"],1234567890,2'])

    assert run_project(right, use_printer=False) is True
    assert run_project(wrong, use_printer=False) is False


def test_visibility_fails_for_an_element_its_parent_clips(tmp_path):
    orders = (FIXTURE / "orders.html").as_uri()
    visible = _project(tmp_path / "visible", [f"Launch App,{orders}", "Assert Visibility,Your orders,2"])
    clipped = _project(tmp_path / "clipped", [f"Launch App,{orders}", "Assert Visibility,Gift wrap,2"])
    assert run_project(visible, use_printer=False) is True
    assert run_project(clipped, use_printer=False) is False


async def test_mcp_session_offers_unique_locators_for_the_suite():
    fastmcp = pytest.importorskip("fastmcp")
    from optics_framework.helper.mcp_server import build_server
    async with fastmcp.Client(build_server()) as client:
        started = await client.call_tool(
            "start_session", {"driver": "playwright", "capabilities": {"headless": True}}
        )
        session_id = started.data["session_id"]
        try:
            await client.call_tool(
                "launch_app", {"session_id": session_id, "app_identifier": STORE_URL}
            )
            resource = await client.read_resource(f"optics://session/{session_id}/elements")
            compact = json.loads(resource[0].text)
            locators = {el["label"]: el.get("loc") for el in compact}

            assert locators["Email"] == "css=#email"
            assert locators["Password"] == 'css=[data-testid="password"]'
            # The full list that screenshot inspectors read has the same rows and locators.
            screen = await client.read_resource(f"optics://session/{session_id}/screen_elements")
            full = json.loads(screen[0].text)["elements"]
            assert len(full) == len(compact)
            assert {el.get("loc") for el in full} == {el.get("loc") for el in compact}

            for element, text in ((locators["Email"], "ava@example.com"), ("Password", "secret")):
                await client.call_tool(
                    "enter_text", {"session_id": session_id, "element": element, "text": text}
                )
            await client.call_tool("press_element", {"session_id": session_id, "element": "Sign in"})
            await client.call_tool(
                "assert_presence", {"session_id": session_id, "elements": "Welcome, ava@example.com"}
            )
        finally:
            await client.call_tool("terminate_session", {"session_id": session_id})


async def _core_session(client, page):
    started = await client.call_tool(
        "start_session",
        {"driver": "playwright", "app": (FIXTURE / page).as_uri(), "capabilities": {"headless": True}},
    )
    return started.data["session_id"]


async def test_mcp_tap_waits_through_a_blank_sign_in_hop():
    fastmcp = pytest.importorskip("fastmcp")
    from optics_framework.helper.mcp_server import build_server
    async with fastmcp.Client(build_server("core")) as client:
        session_id = await _core_session(client, "sign_in.html")
        try:
            tapped = await client.call_tool(
                "tap", {"target": "Continue with Microsoft", "expect_present": ["Settings"]}
            )
        finally:
            await client.call_tool("terminate_session", {"session_id": session_id})

    assert tapped.structured_content["verified"] == "pass"
    assert any(row["label"] == "Settings" for row in tapped.structured_content["screen"]["rows"])


async def test_mcp_offers_and_taps_an_option_a_script_handles():
    fastmcp = pytest.importorskip("fastmcp")
    from optics_framework.helper.mcp_server import build_server
    async with fastmcp.Client(build_server("core")) as client:
        session_id = await _core_session(client, "settings.html")
        try:
            seen = await client.call_tool("observe", {"query": "No meetings"})
            tapped = await client.call_tool(
                "tap", {"target": seen.structured_content["rows"][0]["ref"], "expect_present": ["Picked No meetings"]}
            )
        finally:
            await client.call_tool("terminate_session", {"session_id": session_id})

    assert seen.structured_content["rows"][0]["act"] == ["tap"]
    assert tapped.structured_content["verified"] == "pass"


async def test_mcp_press_key_takes_key_names_and_points_refs_to_tap():
    fastmcp = pytest.importorskip("fastmcp")
    from optics_framework.helper.mcp_server import build_server
    async with fastmcp.Client(build_server("core")) as client:
        session_id = await _core_session(client, "settings.html")
        try:
            escape = await client.call_tool("press_key", {"key": "esc"})
            ref = await client.call_tool("press_key", {"key": "e1"}, raise_on_error=False)
            unknown = await client.call_tool("press_key", {"key": "27"}, raise_on_error=False)
        finally:
            await client.call_tool("terminate_session", {"session_id": session_id})

    assert escape.structured_content["ok"] is True
    assert 'tap(target="e1")' in ref.content[0].text
    assert "Escape" in unknown.content[0].text


def test_stopping_serve_closes_its_browsers():
    from fastapi.testclient import TestClient
    from optics_framework.common import expose_api

    with TestClient(expose_api.app) as client:
        started = client.post("/v1/sessions/start", json={
            "driver_sources": [{"playwright": {"capabilities": {"headless": True}}}],
            "elements_sources": ["playwright_find_element"],
        })
        assert started.status_code == 200, started.text
        browser = expose_api.session_manager.get_session(started.json()["session_id"]).driver.active_instance._browser
        assert browser.is_connected()

    assert not browser.is_connected()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows ends a process without a SIGTERM to handle")
async def test_stopping_mcp_closes_its_browsers():
    fastmcp = pytest.importorskip("fastmcp")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen(
        [sys.executable, "-m", "optics_framework.helper.cli", "mcp", "--transport", "http", "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        for _ in range(100):
            with socket.socket() as ready:
                if ready.connect_ex(("127.0.0.1", port)) == 0:
                    break
            await asyncio.sleep(0.2)
        async with fastmcp.Client(f"http://127.0.0.1:{port}/mcp") as client:
            await client.call_tool("start_session", {"driver": "playwright", "capabilities": {"headless": True}})
        server.send_signal(signal.SIGTERM)
        output, _ = server.communicate(timeout=60)
    finally:
        server.kill()

    assert "Session terminated successfully" in output


# A page without a meta viewport is zoomed out to fit the phone.
@pytest.mark.parametrize("page", ["phone.html", "desktop_site.html"])
def test_a_phone_profile_taps_and_scrolls_with_touch(tmp_path, page):
    project = _project(tmp_path, [
        f"Launch App,{(FIXTURE / page).as_uri()}",
        "Assert Presence,Phone layout",
        "Press By Percentage,50,50",
        "Assert Presence,Touched",
        "Scroll,down",
        "Assert Presence,Scrolled",
        "Press Element,Buy",
        "Assert Presence,Bought",
    ], config=PHONE_CONFIG)
    assert run_project(project, use_printer=False) is True


def test_serve_refuses_read_data_from_its_environment():
    from fastapi.testclient import TestClient
    from optics_framework.common import expose_api

    with TestClient(expose_api.app) as client:
        started = client.post("/v1/sessions/start", json={
            "driver_sources": [{"playwright": {"capabilities": {"headless": True}}}],
            "elements_sources": ["playwright_find_element"],
        })
        assert started.status_code == 200, started.text
        session_id = started.json()["session_id"]
        try:
            refused = client.post(f"/v1/sessions/{session_id}/action", json={
                "mode": "keyword", "keyword": "Read Data", "params": ["${data}", "ENV:PATH"],
            })
        finally:
            client.delete(f"/v1/sessions/{session_id}/stop")

    assert refused.status_code == 400, refused.text
    assert os.environ["PATH"] not in refused.text


def test_a_launched_browser_does_not_announce_automation():
    from optics_framework.engines.drivers.playwright import Playwright

    driver = Playwright({"capabilities": {"headless": True}})
    try:
        driver.launch_app(STORE_URL)
        assert driver.execute_script("() => navigator.webdriver") is False
    finally:
        driver.terminate()


@pytest.mark.parametrize("locator, reaction", [
    ("alt=Company logo", "hit alt"),
    ("title=Close dialog", "hit title"),
    ("testid=buy-btn", "hit testid"),
    ('text="Save changes"', "hit save"),
    ("loc=css:#email", "hit css"),
    ("loc=role:button[name='Save changes']", "hit save"),
    ("loc=href:/pricing", "hit href"),
])
def test_suite_presses_by_each_locator_form(tmp_path, locator, reaction):
    project = _project(tmp_path, [
        f"Launch App,{(FIXTURE / 'locators.html').as_uri()}",
        f"Press Element,{locator}",
        f"Assert Presence,{reaction}",
    ])
    assert run_project(project, use_printer=False) is True


def test_quoted_text_matches_only_the_whole_text(tmp_path):
    project = _project(tmp_path, [
        f"Launch App,{(FIXTURE / 'locators.html').as_uri()}",
        'Press Element,text="Save"',
    ])
    assert run_project(project, use_printer=False) is False
