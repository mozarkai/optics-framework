"""Web journeys through a chromedriver WebDriver server against the local fixture site."""

import json
import pathlib
import socket
import subprocess
import time

import pytest

pytest.importorskip("selenium")

from selenium.webdriver.common.selenium_manager import SeleniumManager  # noqa: E402

from optics_framework.helper.execute import run_project  # noqa: E402

pytestmark = pytest.mark.black_box

FIXTURE = pathlib.Path(__file__).parent / "web_fixture"
STORE_URL = (FIXTURE / "index.html").as_uri()
CAPABILITIES = {"browserName": "chrome", "goog:chromeOptions": {"args": ["--headless=new"]}}

CONFIG = """\
driver_sources:
  - selenium:
      enabled: true
      url: {url}
      capabilities:
        browserName: chrome
        goog:chromeOptions:
          args: ["--headless=new"]
elements_sources:
  - selenium_find_element:
      enabled: true
  - selenium_page_source:
      enabled: true
  - selenium_screenshot:
      enabled: true
"""

TEST_CASES = """\
test_case,test_step
Suite Setup,Open Store
Shop,Journey
Suite Teardown,Close Store
"""

ELEMENTS = """\
Element_Name,Element_ID
password_field,css=#password
orders_link,css=#welcome a
"""


@pytest.fixture(scope="module")
def server_url():
    try:
        chromedriver = SeleniumManager().binary_paths(["--browser", "chrome", "--offline"])["driver_path"]
    except Exception as e:
        pytest.skip(f"chromedriver is not installed: {e}")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = subprocess.Popen(
        [chromedriver, f"--port={port}", "--url-base=/wd/hub"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(50):
            with socket.socket() as ready:
                if ready.connect_ex(("127.0.0.1", port)) == 0:
                    break
            time.sleep(0.2)
        yield f"http://127.0.0.1:{port}/wd/hub"
    finally:
        server.kill()
        server.wait()


def _project(tmp_path, server_url, steps):
    modules = [
        "module_name,module_step,param_1,param_2",
        f"Open Store,Launch App,{STORE_URL}",
        *(f"Journey,{step}" for step in steps),
        "Close Store,Close And Terminate App",
    ]
    files = {
        "config.yaml": CONFIG.format(url=server_url),
        "test_cases/test_cases.csv": TEST_CASES,
        "modules/modules.csv": "\n".join(modules) + "\n",
        "test_data/elements.csv": ELEMENTS,
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(tmp_path)


SIGN_IN_TO_ORDERS = [
    "Enter Text,${password_field},secret",
    "Press Element,Sign in",
    "Press Element,${orders_link}",
]


def test_suite_replays_a_sign_in_journey(tmp_path, server_url):
    project = _project(tmp_path, server_url, [
        "Enter Text,${password_field},secret",
        "Press Element,Sign in",
        "Assert Presence,Welcome",
        "Press Element,${orders_link}",
        "Assert Visibility,Your orders",
        "Go Back",
        "Assert Presence,Optics Store",
    ])
    assert run_project(project, use_printer=False) is True


def test_a_press_waits_for_content_rendered_after_a_click(tmp_path, server_url):
    project = _project(tmp_path, server_url, [*SIGN_IN_TO_ORDERS, "Assert Presence,Gift wrap"])
    assert run_project(project, use_printer=False) is True


def test_visibility_fails_for_an_element_its_parent_clips(tmp_path, server_url):
    project = _project(tmp_path, server_url, [*SIGN_IN_TO_ORDERS, "Assert Presence,Gift wrap", "Assert Visibility,Gift wrap,2"])
    assert run_project(project, use_printer=False) is False


def test_a_missing_element_fails_the_run(tmp_path, server_url):
    project = _project(tmp_path, server_url, ["Assert Presence,css=#not-on-the-page,2"])
    assert run_project(project, use_printer=False) is False


def test_a_failed_step_leaves_the_screen_elements(tmp_path, server_url):
    project = _project(tmp_path, server_url, ["Press Element,#not-on-the-page"])

    assert run_project(project, use_printer=False) is False
    failures = json.loads((tmp_path / "execution_output" / "failed_steps_elements.json").read_text())
    assert [f["step"] for f in failures] == ["Press Element (#not-on-the-page)"]
    assert any(e.get("label") == "Sign in" for e in failures[0]["elements"])


async def test_mcp_session_offers_unique_locators_for_the_suite(server_url, monkeypatch):
    fastmcp = pytest.importorskip("fastmcp")
    from optics_framework.common import expose_api
    from optics_framework.helper.mcp_server import build_server
    monkeypatch.setattr(expose_api, "server_reads_allowed", True)
    monkeypatch.setenv("OPTICS_MCP_TOOLSET", "full")
    async with fastmcp.Client(build_server()) as client:
        started = await client.call_tool(
            "start_session",
            {"driver": "selenium", "url": server_url, "capabilities": {**CAPABILITIES, "browserURL": STORE_URL}},
        )
        session_id = started.data["session_id"]
        try:
            resource = await client.read_resource(f"optics://session/{session_id}/elements")
            compact = json.loads(resource[0].text)
            locators = {el["label"]: el.get("loc") for el in compact}

            assert locators["Email"] == "css=#email"
            assert locators["Password"] == 'css=[data-testid="password"]'
            screen = await client.read_resource(f"optics://session/{session_id}/screen_elements")
            full = json.loads(screen[0].text)["elements"]
            assert len(full) == len(compact)
            assert {el.get("loc") for el in full} == {el.get("loc") for el in compact}

            for element, text in ((locators["Email"], "ava@example.com"), (locators["Password"], "secret")):
                await client.call_tool("enter_text", {"session_id": session_id, "element": element, "text": text})
            await client.call_tool("press_element", {"session_id": session_id, "element": "Sign in"})
            await client.call_tool(
                "assert_presence", {"session_id": session_id, "elements": "Welcome, ava@example.com"}
            )
        finally:
            await client.call_tool("terminate_session", {"session_id": session_id})
