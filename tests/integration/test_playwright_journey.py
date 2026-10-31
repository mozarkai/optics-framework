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


async def test_mcp_journey_by_ref_records_a_replayable_suite(tmp_path):
    fastmcp = pytest.importorskip("fastmcp")
    from optics_framework.helper.mcp_server import build_server

    async with fastmcp.Client(build_server("core")) as client:
        started = await client.call_tool(
            "start_session", {"driver": "playwright", "capabilities": {"headless": True}, "app": STORE_URL}
        )
        session_id = started.structured_content["session_id"]
        try:
            observed = await client.call_tool("observe", {})
            fields = {row["label"]: row["ref"] for row in observed.structured_content["rows"] if "input" in row["act"]}
            for label, text in (("Email", "ava@example.com"), ("Password", "secret")):
                typed = await client.call_tool("type_text", {"target": fields[label], "text": text})
                assert typed.structured_content["ok"]

            sign_in = next(r["ref"] for r in observed.structured_content["rows"] if r["label"] == "Sign in")
            tapped = await client.call_tool("tap", {"target": sign_in, "expect_present": ["Welcome"]})
            assert tapped.structured_content["verified"] == "pass"

            stale = await client.call_tool("tap", {"target": "e99"}, raise_on_error=False)
            assert stale.is_error

            steps = json.loads((await client.read_resource(f"optics://session/{session_id}/steps"))[0].text)
        finally:
            await client.call_tool("terminate_session", {})

    assert "secret" not in steps["elements_csv"] + steps["modules_csv"]
    files = {
        "config.yaml": CONFIG,
        "test_cases/test_cases.csv": "test_case,test_step\nReplay,recorded\n",
        "modules/modules.csv": steps["modules_csv"],
        "test_data/elements.csv": steps["elements_csv"].replace("<redacted>", "secret"),
    }
    for name, content in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(content, encoding="utf-8")
    assert await asyncio.to_thread(run_project, str(tmp_path), use_printer=False) is True


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
