"""`optics mcp install`: register the MCP server so that every folder and every shell can start it."""
import json
import os
import shutil
import subprocess  # nosec B404
import sys

NAME = "optics"
NEXT_STEP = "A client loads its MCP servers only when a session starts: start a new session, then check that the optics tools are listed."
CLAUDE_STEP = "In Claude Code, `claude --continue` keeps the conversation, and /mcp shows optics as connected."


def server_command(toolset: str | None = None) -> list[str]:
    """This install's server as an absolute command, so it starts without PATH or a working folder."""
    script = os.path.abspath(sys.argv[0])
    if os.path.splitext(os.path.basename(script))[0] == "optics" and os.path.isfile(script):
        command = [script, "mcp"]
    else:
        command = [os.path.abspath(sys.executable), "-m", "optics_framework.helper.cli", "mcp"]
    return [*command, "--toolset", toolset] if toolset else command


def _claude_mcp(claude: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([claude, "mcp", *args], capture_output=True, text=True, check=False)  # nosec B603


def install(toolset: str | None = None) -> None:
    """Register the server with Claude Code for all folders, or print the config another client takes."""
    from optics_framework.helper.mcp_server import _require_fastmcp

    _require_fastmcp()
    command = server_command(toolset)
    claude = shutil.which("claude")
    if claude is None:
        print("Claude Code is not on PATH. Add this server to your MCP client's config:\n")
        print(json.dumps({"mcpServers": {NAME: {"command": command[0], "args": command[1:]}}}, indent=2))
        print(f"\n{NEXT_STEP}")
        return
    _add_user_entry(claude, command)
    # A local entry, such as one a plain `claude mcp add` made in this folder, wins over the user entry here.
    _claude_mcp(claude, "remove", "--scope", "local", NAME)
    print(f"Registered {NAME} for all folders: {' '.join(command)}")
    winners = _entries_that_win()
    if winners:
        print(f"These {NAME} entries win over the user entry where they apply:", *winners, sep="\n  ")
    print(f"{NEXT_STEP}\n{CLAUDE_STEP}")


def _add_user_entry(claude: str, command: list[str]) -> None:
    """Replace the user entry only once the add is known to fail for no reason but the old entry."""
    add = ("add", "--scope", "user", NAME, "--", *command)
    added = _claude_mcp(claude, *add)
    if added.returncode != 0 and "already exists" in added.stdout + added.stderr:
        _claude_mcp(claude, "remove", "--scope", "user", NAME)
        added = _claude_mcp(claude, *add)
    if added.returncode != 0:
        raise SystemExit(f"claude mcp add failed: {(added.stderr or added.stdout).strip()}")


def _json_object(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _defines(settings: object) -> bool:
    servers = settings.get("mcpServers") if isinstance(settings, dict) else None
    return isinstance(servers, dict) and NAME in servers


def _entries_that_win() -> list[str]:
    """This folder's project entry and other folders' local entries: each outranks the user entry where it applies."""
    here = os.path.abspath(".")
    project = os.path.join(here, ".mcp.json")
    winners = [f"{project}: remove {NAME} from mcpServers"] if _defines(_json_object(project)) else []
    config = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~"), ".claude.json")
    folders = _json_object(config).get("projects")
    for folder, settings in (folders.items() if isinstance(folders, dict) else ()):
        if folder != here and _defines(settings):
            winners.append(f"{folder}: run `claude mcp remove --scope local {NAME}` there")
    return winners
