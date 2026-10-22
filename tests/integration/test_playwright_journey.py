"""Web journeys against a local fixture site in headless Chromium."""

import asyncio
import json
import os
import pathlib

import pytest

playwright_api = pytest.importorskip("playwright.async_api")

from optics_framework.helper.execute import run_project  # noqa: E402

pytestmark = pytest.mark.black_box

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


def _project(tmp_path, steps):
    modules = [
        "module_name,module_step,param_1,param_2",
        f"Open Store,Launch App,{STORE_URL}",
        *(f"Journey,{step}" for step in steps),
        "Close Store,Close And Terminate App",
    ]
    files = {
        "config.yaml": CONFIG,
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


def test_pressing_a_disabled_button_fails(tmp_path):
    project = _project(tmp_path, [
        f"Launch App,{(FIXTURE / 'orders.html').as_uri()}",
        "Press Element,Reorder",
    ])

    assert run_project(project, use_printer=False) is False


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
            locators = {el["label"]: el.get("loc") for el in json.loads(resource[0].text)}

            assert locators["Email"] == "#email"
            assert locators["Password"] == '[data-testid="password"]'

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
