"""The optics MCP server on a desktop app: the core and full toolsets, the resources and a suite exported from a session."""

import asyncio
import base64
import json
import urllib.request

import pytest

from optics_framework.helper import mcp_server
from tests.integration.desktop.support import (
    driver_config,
    fresh_start,
    junit,
    run_optics,
    runs,
    scroll_position,
    write_project,
)

pytestmark = pytest.mark.black_box

fastmcp = pytest.importorskip("fastmcp")


class Agent:
    """One MCP client and its desktop session; every tool call must succeed unless told otherwise."""

    def __init__(self, client, desktop):
        self.client, self.desktop, self.sid = client, desktop, None

    async def call(self, tool, **args):
        result = await self.client.call_tool(tool, {"session_id": self.sid, **args} if self.sid else args,
                                             raise_on_error=False)
        text = "\n".join(c.text for c in result.content if getattr(c, "text", None))
        assert not result.is_error, text
        return result.structured_content or {}, text

    async def start(self, host, app, **caps):
        started, _ = await self.call("start_session", driver="appium", app=app, url=host,
                                     capabilities={"platformName": self.desktop.platform, "automationName": "Xa11y",
                                                   **fresh_start(), **caps})
        self.sid = started["session_id"]

    async def field(self):
        _, screen = await self.call("observe")
        return self.desktop.field_ref(screen)

    async def resource(self, name):
        return await self.client.read_resource(f"optics://session/{self.sid}/{name}")


def _journey(host, desktop, steps, toolset=None):
    async def run():
        async with fastmcp.Client(mcp_server.build_server(toolset)) as client:
            agent = Agent(client, desktop)
            await agent.start(host, desktop.editor)
            try:
                return await steps(agent)
            finally:
                await agent.call("terminate_session")

    return asyncio.run(run())


def test_an_agent_types_presses_chords_and_switches_apps(host, desktop):
    async def steps(agent):
        editor = await agent.field()
        typed, _ = await agent.call("type_text", target=editor, text="from an agent", expect_present=["from an agent"])
        away, _ = await agent.call("launch_app", app=desktop.calculator, expect_absent=["from an agent"])
        back, _ = await agent.call("launch_app", app=desktop.editor, expect_present=["from an agent"])
        await agent.call("press_key", key=desktop.select_all)
        cleared, _ = await agent.call("press_key", key="BackSpace", expect_absent=["from an agent"])
        await agent.call("run_keyword", keyword="force_terminate_app", params={"app_name": desktop.calculator})
        return [step["verified"] for step in (typed, away, back, cleared)]

    assert _journey(host, desktop, steps) == ["pass"] * 4
    assert not runs(host, desktop.calculator_name)


def _scroll_positions(host, desktop, moves):
    """The editor's vertical scroll position at the top of 120 lines, then after each run_keyword move."""
    async def steps(agent):
        async def position():
            return scroll_position(json.loads((await agent.resource("source"))[0].text)["page_source"])

        await agent.call("type_text", target=await agent.field(), text="\n".join(f"line {i}" for i in range(120)))
        await agent.call("press_key", key=desktop.to_top)
        positions = [await position()]
        for keyword, params in moves:
            await agent.call("run_keyword", keyword=keyword, params=params)
            positions.append(await position())
        return positions

    return _journey(host, desktop, steps)


def test_an_agent_scrolls_and_swipes_the_window(host, desktop):
    top, down, back, swiped, swiped_back = _scroll_positions(host, desktop, [
        ("scroll", {"direction": "down"}),
        ("scroll", {"direction": "up"}),
        ("swipe_by_percentage", {"percent_x": "50", "percent_y": "50", "direction": "up", "swipe_length": "30"}),
        ("swipe_from_element", {"element": desktop.field, "direction": "down", "swipe_length": "200"}),
    ])

    positions = (top, down, back, swiped, swiped_back)
    assert top < down, positions
    assert back < down, positions
    assert back < swiped, positions
    assert swiped_back < swiped, positions


def test_an_agent_cannot_start_a_desktop_session_without_an_app(host, desktop):
    async def start():
        async with fastmcp.Client(mcp_server.build_server()) as client:
            caps = {"platformName": desktop.platform, "automationName": "Xa11y"}
            result = await client.call_tool("start_session", {"driver": "appium", "url": host, "capabilities": caps},
                                            raise_on_error=False)
            return result.is_error, "\n".join(c.text for c in result.content if getattr(c, "text", None))

    failed, text = asyncio.run(start())
    assert failed, text
    assert "appName" in text, text


def test_an_agent_starts_the_app_named_in_appium_options(host, desktop):
    async def start():
        async with fastmcp.Client(mcp_server.build_server()) as client:
            agent = Agent(client, desktop)
            started, _ = await agent.call("start_session", driver="appium", url=host, capabilities={
                "platformName": desktop.platform, "automationName": "Xa11y",
                "appium:options": {"app": desktop.editor, **fresh_start()}})
            agent.sid = started["session_id"]
            try:
                return await agent.field()
            finally:
                await agent.call("terminate_session")

    assert asyncio.run(start())


def _percent_of_window(url, desktop, xpath):
    """Where the element is, in percent of the app's window, read through a session of its own."""
    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f"{url}{path}", data, {"Content-Type": "application/json"}, method=method)
        with urllib.request.urlopen(request, timeout=60) as response:  # nosec
            return json.load(response)["value"]

    caps = {"platformName": desktop.platform, "appium:automationName": "Xa11y", "appium:app": desktop.calculator}
    session = f"/session/{call('POST', '/session', {'capabilities': {'alwaysMatch': caps}})['sessionId']}"
    try:
        windows = call("POST", f"{session}/execute/sync", {"script": "xa11y: listWindows", "args": []})
        window = next(w for w in windows if w.get("active"))["bounds"]
        element = call("POST", f"{session}/element", {"using": "xpath", "value": xpath})
        rect = call("GET", f"{session}/element/{next(iter(element.values()))}/rect")
    finally:
        call("DELETE", session)
    centre_x, centre_y = rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2
    return round(100 * (centre_x - window["x"]) / window["width"]), round(100 * (centre_y - window["y"]) / window["height"])


def test_an_agent_presses_by_percentage_of_the_app_window(host, desktop, monkeypatch):
    monkeypatch.setenv(mcp_server.ENV_ALLOW_COORDINATE_TAPS, "true")
    percent_x, percent_y = _percent_of_window(host, desktop, desktop.key_of("7"))

    async def press():
        async with fastmcp.Client(mcp_server.build_server()) as client:
            agent = Agent(client, desktop)
            await agent.start(host, desktop.calculator)
            try:
                await agent.call("run_keyword", keyword="press_element", params={"element": desktop.key_of("clear")})
                pressed, _ = await agent.call("run_keyword", keyword="press_by_percentage", expect_present=["7"],
                                              params={"percent_x": str(percent_x), "percent_y": str(percent_y)})
                await agent.call("run_keyword", keyword="force_terminate_app", params={"app_name": desktop.calculator})
                return pressed["verified"]
            finally:
                await agent.call("terminate_session")

    assert asyncio.run(press()) == "pass", (percent_x, percent_y)


def test_the_full_toolset_has_a_tool_per_keyword(host, desktop):
    async def steps(agent):
        tools = {tool.name for tool in await agent.client.list_tools()}
        await agent.call("enter_text", element=desktop.field, text="by its own tool")
        await agent.call("assert_text", element=desktop.field, expected="by its own tool")
        shot = await agent.client.call_tool("screenshot", {"session_id": agent.sid})
        await agent.call("close_and_terminate_app")
        return tools, [c.type for c in shot.content]

    tools, shot = _journey(host, desktop, steps, toolset="full")
    assert {"enter_text", "press_keycode", "launch_other_app", "swipe_from_element", "screenshot"} <= tools
    assert "press_by_coordinates" not in tools
    assert "image" in shot


def test_the_resources_describe_the_desktop_session(host, desktop):
    async def steps(agent):
        await agent.call("type_text", target=await agent.field(), text="resource words")
        catalog = json.loads((await agent.client.read_resource("optics://keywords"))[0].text)
        project_format = (await agent.client.read_resource("optics://project-format"))[0].text
        shot = (await agent.resource("screenshot"))[0]
        source = json.loads((await agent.resource("source"))[0].text)
        elements = json.loads((await agent.resource("elements"))[0].text)
        screen = json.loads((await agent.resource("screen_elements"))[0].text)
        return catalog, project_format, shot, source, elements, screen

    catalog, project_format, shot, source, elements, screen = _journey(host, desktop, steps)
    assert "press_keycode" in {k["keyword_slug"] for k in catalog}
    assert "automationName: Xa11y" in project_format
    assert (shot.blob if isinstance(shot.blob, bytes) else base64.b64decode(shot.blob)).startswith(b"\x89PNG")
    assert "resource words" in json.dumps(source)
    assert any("input" in e["act"] for e in elements), elements
    assert "resource words" in json.dumps(screen)


def test_a_suite_exported_from_a_session_runs_with_optics_execute(host, desktop, tmp_path):
    async def steps(agent):
        await agent.call("type_text", target=await agent.field(), text="exported", expect_present=["exported"])
        await agent.call("press_key", key=desktop.select_all)
        await agent.call("press_key", key="BackSpace", expect_absent=["exported"])
        await agent.call("type_text", target=await agent.field(), text="replayed")
        await agent.call("check", present=["replayed"])
        return json.loads((await agent.resource("steps"))[0].text)

    recorded = _journey(host, desktop, steps)
    assert all(step["ok"] for step in recorded["steps"]), recorded
    folder = write_project(tmp_path, driver_config(host, desktop.editor), {
        "test_cases/test_cases.csv": "test_case,test_step\nReplay,recorded\nReplay,Close\n",
        "modules/modules.csv": recorded["modules_csv"].rstrip("\n") + "\nClose,Close And Terminate App\n",
        "test_data/elements.csv": recorded["elements_csv"],
    })

    ran = run_optics("execute", folder)
    assert ran.returncode == 0, recorded["modules_csv"] + recorded["elements_csv"] + ran.stdout[-3000:] + junit(tmp_path)
