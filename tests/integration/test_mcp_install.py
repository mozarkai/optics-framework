"""`optics mcp install` registers a server that any folder and any shell can start."""

import asyncio
import json
import os
import sys

import pytest

fastmcp = pytest.importorskip("fastmcp")

pytestmark = [
    pytest.mark.black_box,
    pytest.mark.skipif(sys.platform == "win32", reason="the fake claude is a script with a shebang"),
]

FAKE_CLAUDE = """#!{python}
import json, os, sys
path = os.environ["FAKE_CLAUDE_STATE"]
entries = json.load(open(path)) if os.path.exists(path) else {}
args = sys.argv[1:]
verb, scope, name = args[1], args[3], args[4]
key = f"{scope}:{name}"
if verb == "add" and os.environ.get("FAKE_CLAUDE_BROKEN"):
    sys.exit("cannot write the config")
if verb == "add" and key in entries:
    print(f"MCP server {name} already exists in {scope} config")
    sys.exit(1)
if verb == "remove" and key not in entries:
    sys.exit(1)
if verb == "add":
    entries[key] = args[6:]
else:
    del entries[key]
json.dump(entries, open(path, "w"))
"""

OLD = {"user:optics": ["optics", "mcp"], "local:optics": ["optics", "mcp"]}


@pytest.fixture(scope="session")
def claude_bin(tmp_path_factory):
    """One fake claude for the session: macOS checks a new executable on its first run, which costs about 0.4 s."""
    bin_dir = tmp_path_factory.mktemp("bin")
    claude = bin_dir / "claude"
    claude.write_text(FAKE_CLAUDE.replace("{python}", sys.executable))
    claude.chmod(0o755)
    return f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin"


@pytest.fixture
def fake_claude(tmp_path, claude_bin):
    def with_entries(entries=None):
        if entries is not None:
            (tmp_path / "claude.json").write_text(json.dumps(entries))
        return claude_bin
    return with_entries


@pytest.fixture
def install(tmp_path, cli, monkeypatch):
    def run(path, *flags, **env):
        state = tmp_path / "claude.json"
        monkeypatch.chdir(tmp_path)
        for name, value in {"PATH": path, "FAKE_CLAUDE_STATE": str(state), "CLAUDE_CONFIG_DIR": str(tmp_path), **env}.items():
            monkeypatch.setenv(name, value)
        ran = cli("mcp", "install", *flags)
        return ran, json.loads(state.read_text()) if state.exists() else {}

    return run


def _tools(command, cwd):
    async def handshake():
        transport = fastmcp.client.transports.StdioTransport(command[0], command[1:], env={"PATH": "/usr/bin:/bin"}, cwd=str(cwd))
        async with fastmcp.Client(transport) as client:
            return {tool.name for tool in await client.list_tools()}

    return asyncio.run(handshake())


def test_install_replaces_the_old_entries_with_an_absolute_command_for_every_folder(tmp_path, install, fake_claude):
    ran, entries = install(fake_claude(OLD))

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert list(entries) == ["user:optics"]
    command = entries["user:optics"]
    assert os.path.isabs(command[0])
    assert "new session" in ran.stdout
    assert "start_session" in _tools(command, tmp_path)


def test_a_failed_install_keeps_the_entries_that_worked(tmp_path, install, fake_claude):
    ran, entries = install(fake_claude(OLD), FAKE_CLAUDE_BROKEN="1")

    assert ran.returncode != 0
    assert entries == OLD


def test_install_names_the_entries_that_outrank_the_user_entry(tmp_path, install, fake_claude):
    project = {"mcpServers": {"optics": {"command": "/old/optics", "args": ["mcp"]}}}
    (tmp_path / ".mcp.json").write_text(json.dumps(project))
    (tmp_path / ".claude.json").write_text(json.dumps({"projects": {"/elsewhere": project, "/plain": {"mcpServers": {}}}}))

    ran, _ = install(fake_claude())

    assert f"{tmp_path / '.mcp.json'}: remove optics" in ran.stdout
    assert "/elsewhere: run `claude mcp remove --scope local optics` there" in ran.stdout
    assert "/plain" not in ran.stdout
    assert json.loads((tmp_path / ".mcp.json").read_text()) == project


def test_install_registers_the_toolset_it_was_given(tmp_path, install, fake_claude):
    ran, entries = install(fake_claude(), "--toolset", "full")

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert entries["user:optics"][-2:] == ["--toolset", "full"]


def test_install_refuses_the_http_transport(tmp_path, install, fake_claude):
    ran, entries = install(fake_claude(), "--transport", "http")

    assert ran.returncode != 0
    assert "stdio" in ran.stderr
    assert not entries


def test_install_without_claude_prints_the_config_for_any_client(tmp_path, install):
    ran, entries = install(f"/usr/bin{os.pathsep}/bin")

    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert not entries
    config = json.loads(ran.stdout[ran.stdout.index("{"):ran.stdout.rindex("}") + 1])
    server = config["mcpServers"]["optics"]
    assert os.path.isabs(server["command"])
    assert "start_session" in _tools([server["command"], *server["args"]], tmp_path)
