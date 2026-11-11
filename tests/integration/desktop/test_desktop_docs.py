"""Each desktop example in the docs runs as written; only the desktop, app, editor locator and server URL change per Host."""

import asyncio
import json
import os
import re
import shutil
import socket
import subprocess  # nosec B404
import sys
import textwrap
import time
import urllib.request
from pathlib import Path

import pytest

from optics_framework.helper import mcp_server
from tests.integration.desktop.support import OPTICS, SCRIPTS, junit, runs, started_fresh

pytestmark = pytest.mark.black_box

ROOT = Path(__file__).resolve().parents[3]
FENCE = re.compile(r"^(?P<indent>[ \t]*)```(?P<lang>\w*)\n(?P<code>.*?)^(?P=indent)```", re.MULTILINE | re.DOTALL)


def _section(page, heading):
    """The text under a markdown heading, up to the next heading of the same or a higher level."""
    text = (ROOT / page).read_text(encoding="utf-8")
    start = re.search(rf"^(#+) {re.escape(heading)}\n", text, re.MULTILINE)
    assert start, f"{page} has no '{heading}' heading"
    end = re.compile(rf"^#{{1,{len(start.group(1))}}} ", re.MULTILINE).search(text, start.end())
    return text[start.end():end.start() if end else len(text)]


def _blocks(text):
    return [(m["lang"], textwrap.dedent(m["code"])) for m in FENCE.finditer(text)]


def _fenced_yaml(text):
    return next(code for lang, code in _blocks(text) if lang == "yaml")


def _indented(text):
    """The first block indented by four spaces."""
    block = re.search(r"\n((?: {4}.*\n)+)", text)
    assert block, text[:300]
    return textwrap.dedent(block.group(1))


def _on(desktop, host, text):
    """The example as it reads on this Host."""
    text = text.replace("com.apple.TextEdit", desktop.editor).replace("http://127.0.0.1:4723", host)
    text = text.replace("//text_area", desktop.field).replace("cmd+a", desktop.select_all)
    return started_fresh(re.sub(r"""(platformName["']?:\s*["']?)Mac\b""", rf"\g<1>{desktop.platform}", text))


def _bash():
    """Git Bash on Windows, where the bash on PATH can be the WSL launcher."""
    if os.name != "nt":
        return "bash"
    return str(Path(shutil.which("git")).resolve().parents[1] / "bin" / "bash.exe")


def _shell(script, cwd):
    env = {**os.environ, "PATH": f"{SCRIPTS}{os.pathsep}{os.environ['PATH']}"}
    return subprocess.run([_bash(), "-euo", "pipefail", "-c", script], cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=600, check=False)  # nosec B603 B607


def test_the_cli_page_runs_the_desktop_sample(tmp_path, host, desktop):
    (_, init), (_, config), (_, run) = _blocks(_section("docs/usage/CLI_usage.md", "Desktop apps"))

    made = _shell(init, tmp_path)
    assert made.returncode == 0, made.stdout + made.stderr
    (tmp_path / "notes" / "config.yaml").write_text(_on(desktop, host, config), encoding="utf-8")
    ran = _shell(run, tmp_path)
    assert ran.returncode == 0, ran.stdout[-3000:] + junit(tmp_path / "notes")
    assert not runs(host, desktop.editor_name)


def test_the_library_page_types_into_the_editor(host, desktop):
    [(lang, code)] = _blocks(_section("docs/usage/library_usage.md", "Desktop apps"))

    assert lang == "python"
    exec(compile(_on(desktop, host, code), "library_usage.md", "exec"), {})  # noqa: S102  # nosec B102
    assert not runs(host, desktop.editor_name)


def test_the_robot_page_types_into_the_editor(tmp_path, host, desktop):
    pytest.importorskip("robot")
    [(_, suite)] = _blocks(_section("docs/usage/robot_usage.md", "Desktop apps"))
    (tmp_path / "notes.robot").write_text(_on(desktop, host, suite), encoding="utf-8")

    ran = subprocess.run([sys.executable, "-m", "robot", "--outputdir", str(tmp_path), "notes.robot"], cwd=tmp_path,
                         capture_output=True, text=True, timeout=600, check=False)  # nosec B603
    assert ran.returncode == 0, ran.stdout[-3000:]


@pytest.fixture
def server():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    process = subprocess.Popen([OPTICS, "serve", "--port", str(port)],  # nosec B603
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"{url}/health", timeout=1).close()  # nosec
                break
            except OSError:
                time.sleep(0.5)
        yield url
    finally:
        process.terminate()
        process.wait(timeout=30)


def test_the_rest_page_types_into_the_editor(tmp_path, host, desktop, server):
    [(_, script)] = _blocks(_section("docs/usage/REST_API_usage.md", "Desktop apps"))

    ran = _shell(_on(desktop, host, script).replace("http://localhost:8000", server), tmp_path)
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert "Hello from Optics" in ran.stdout, ran.stdout


def test_the_mcp_page_starts_a_desktop_session(host, desktop):
    fastmcp = pytest.importorskip("fastmcp")
    [(_, arguments)] = _blocks(_section("docs/usage/mcp_usage.md", "Desktop apps"))

    async def journey():
        async with fastmcp.Client(mcp_server.build_server()) as client:
            started = await client.call_tool("start_session", json.loads(_on(desktop, host, arguments)))
            sid = started.structured_content["session_id"]
            try:
                screen = await client.call_tool("observe", {"session_id": sid})
                return screen.content[0].text
            finally:
                await client.call_tool("terminate_session", {"session_id": sid})

    screen = asyncio.run(journey())
    assert desktop.field_ref(screen)


@pytest.mark.parametrize("page, start, extract", [
    ("docs/configuration.md", '=== "Desktop (Appium)"', _fenced_yaml),
    ("optics_framework/helper/mcp_project_format.md", "A desktop app (macOS", _indented),
])
def test_the_config_pages_run_the_desktop_sample(tmp_path, host, desktop, page, start, extract):
    text = (ROOT / page).read_text(encoding="utf-8")
    config = extract(text[text.index(start):])
    made = _shell("optics init notes --template desktop_editor", tmp_path)
    assert made.returncode == 0, made.stderr
    (tmp_path / "notes" / "config.yaml").write_text(_on(desktop, host, config), encoding="utf-8")

    ran = _shell("optics execute notes", tmp_path)
    assert ran.returncode == 0, ran.stdout[-3000:] + junit(tmp_path / "notes")
