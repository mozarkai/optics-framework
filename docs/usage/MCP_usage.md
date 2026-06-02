# :material-robot: MCP Server Usage

The Optics Framework ships a **Model Context Protocol (MCP) server** that exposes every Optics keyword as a tool an AI assistant can call. Once it's running, any MCP-compatible client — Claude Desktop, Claude Code, Cursor, claude.ai web, or any custom client built on the MCP SDK — can drive a real Android, iOS, or web session through Optics in natural language.

## :material-help-circle: What is MCP, and why would I want this?

**Model Context Protocol** is an open standard ([modelcontextprotocol.io](https://modelcontextprotocol.io)) for letting LLMs use external tools. Instead of you writing test cases by hand, you give an MCP-aware assistant access to the Optics server and ask it things like:

> *"Open the contacts app, find the new-contact button, tap it, fill in 'Alex' as the first name, and screenshot the result."*

The model picks the right keywords (`optics_press_element`, `optics_enter_text`, `optics_screenshot`), passes the right arguments, observes the page source, retries on failures — all through the same Optics keyword surface CSV/YAML test cases use. The model never touches your device directly; it asks the Optics server to do it, and Optics handles drivers, fallback, screenshots, and events as it always has.

Use it when you want to:

- **Explore an app interactively** with an AI collaborator before writing test cases
- **Author test cases** by demonstrating the flow in natural language and copying the resulting steps into CSV/YAML
- **Reproduce bugs** an end-user described in plain English without translating them into code
- **Build agentic QA workflows** where an LLM drives the app end-to-end (smoke tests, accessibility audits, exploratory testing)

## :material-arrow-decision: Choosing a transport

The server supports two transports. Pick based on where your MCP client lives:

| Transport | When to use | Command |
|-----------|-------------|---------|
| **stdio** | Local AI clients (Claude Desktop, Claude Code, Cursor). The client launches the server as a child process and talks to it over stdin/stdout. | `optics mcp --transport stdio` |
| **streamable HTTP** | Hosted clients (claude.ai web), remote agents, anything that needs network access. The server listens on a port; clients POST to `/mcp`. | `optics mcp` (default) |

Most users want **stdio** — it's how desktop AI tools normally talk to MCP servers. Use HTTP when the client isn't running on your machine, or when you want multiple clients sharing one session.

## :material-rocket-launch: Quick start

### 1. Install Optics Framework

```bash
pip install optics-framework
```

The `mcp` Python SDK comes along as a dependency — nothing else to install.

### 2. Verify the server starts

```bash
optics mcp --help
```

You should see `--transport {http,stdio}` in the help output. Try a quick smoke test:

```bash
optics mcp --transport stdio
```

The process will block waiting for a client on stdin. Press `Ctrl+C` to exit — that just means the wiring works.

### 3. Wire it into your MCP client

See the [Client configuration](#client-configuration) section below for Claude Desktop, Claude Code, Cursor, and claude.ai web.

### 4. Use it

In your AI client, start a session:

> Start an Optics session for Appium against an Android device with package `com.example.app`. Then take a screenshot.

The model will call `optics_start_session` with the right config, then `optics_screenshot`. From there you can describe actions in plain English.

## :material-cog: Client configuration

### Claude Desktop (stdio)

Edit `claude_desktop_config.json` (location varies by OS — Claude Desktop > Settings > Developer shows the path):

```json
{
  "mcpServers": {
    "optics": {
      "command": "optics",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

If `optics` isn't on the `PATH` your client sees, use an absolute path: `which optics` (macOS/Linux) or `where optics` (Windows) tells you the full path. On Windows, you'll typically want `python -m optics_framework.helper.cli mcp --transport stdio` or the explicit `optics.exe` path inside your venv.

Restart Claude Desktop. The Optics tools appear under the :material-tools: hammer icon in the message composer.

### Claude Code (stdio)

```bash
claude mcp add optics --transport stdio -- optics mcp --transport stdio
```

Or edit `~/.claude.json` directly under the `mcpServers` key:

```json
{
  "mcpServers": {
    "optics": {
      "command": "optics",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

### Cursor (stdio)

In Cursor Settings → MCP, add a server with:

- **Command:** `optics`
- **Args:** `mcp --transport stdio`

### claude.ai web (HTTP)

Start the server on a port reachable from the public internet (or use a tunnel like `ngrok http 8090`):

```bash
optics mcp --host 0.0.0.0 --port 8090
```

In claude.ai → Settings → Connectors, add a custom MCP server pointing at `https://<your-host>/mcp`. **There is no auth in v1** — put it behind a reverse proxy with auth, or only expose it on a trusted network.

## :material-console: CLI reference

```bash
optics mcp [--transport {http,stdio}] [--host HOST] [--port PORT] [--cors-origin ORIGIN]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--transport` | `http` | `http` for streamable HTTP (hosted clients), `stdio` for local clients. |
| `--host` | `127.0.0.1` | Bind address (HTTP only). Use `0.0.0.0` to accept connections from other machines. |
| `--port` | `8090` | Listen port (HTTP only). |
| `--cors-origin` | `*` | Allowed CORS origin (HTTP only). Repeat for multiple origins, e.g. `--cors-origin https://claude.ai --cors-origin https://app.claude.ai`. |

When HTTP is running, three endpoints are exposed:

- `POST /mcp` — the MCP streamable HTTP endpoint
- `GET /healthz` — liveness probe
- `GET /` — service discovery (JSON with version + MCP endpoint URL)

## :material-tools: Tool reference

The server exposes three groups of tools. All keyword tools require a `session_id` from `optics_start_session`.

### Session lifecycle

| Tool | What it does |
|------|--------------|
| `optics_start_session` | Start an Optics session. Takes the same `driver_sources` / `elements_sources` / `text_detection` / `image_detection` config as `optics serve`. Returns a `session_id`. |
| `optics_terminate_session` | Release the driver, clear templates, remove the per-session temp dir. |
| `optics_list_sessions` | List currently active session IDs on this server. |

### State inspection

| Tool | What it does |
|------|--------------|
| `optics_screenshot` | Capture the current device screenshot. Returns base64 PNG bytes. |
| `optics_page_source` | Capture the XML/HTML page source. |
| `optics_screen_elements` | Parsed list of on-screen elements (text + bounds). |
| `optics_interactive_elements` | On-screen interactive elements, optionally filtered (`all`, `interactive`, `buttons`, `inputs`, `images`, `text`). |
| `optics_driver_session_id` | The underlying driver (e.g. Appium) session id. |

### Keywords

Every public method on the Optics API classes (`ActionKeyword`, `AppManagement`, `Verifier`, `FlowControl`) is exposed as an `optics_<method_name>` tool. The current build surfaces **43 tools** including:

- `optics_press_element`, `optics_press_coordinates`, `optics_detect_and_press`
- `optics_enter_text`, `optics_enter_number`, `optics_clear_text`
- `optics_swipe`, `optics_scroll`, `optics_swipe_until_element_appears`, `optics_scroll_until_element_appears`
- `optics_assert_presence`, `optics_assert_equality`, `optics_validate_element`
- `optics_sleep`, `optics_press_keycode`

To see the full list and per-tool schemas, run:

```bash
optics list
```

or inspect them through your MCP client — most clients show schemas in a panel when you hover a tool.

Tool schemas mirror the keyword signatures: required parameters are required, defaulted parameters are optional, and `List[str]` parameters accept either a single string or an array (the array form triggers the Optics fallback ladder).

### What's *not* exposed as a keyword tool

`optics_start_session` already calls `launch_app` for you, so it's hidden from the keyword surface. Same for `capture_screenshot` / `capture_pagesource` / `get_screen_elements` / `get_interactive_elements` / `get_driver_session_id` — they have dedicated inspect tools with friendlier names. Runner-only flow control (`run_loop`, `execute_module`, `condition`) is hidden too because it only makes sense inside a CSV/YAML test-case graph.

## :material-play: End-to-end example

A typical model-driven flow looks like:

```
User: Open the Android contacts app and add a contact named "Alex Doe".

Model: → optics_start_session({
         "driver_sources": [{"appium": {"url": "http://localhost:4723"}}],
         "elements_sources": ["appium_find_element"]
       })
       ← {"session_id": "abc-123", "driver_id": "..."}

Model: → optics_screenshot({"session_id": "abc-123"})
       ← <base64 screenshot of contacts list>

Model: → optics_press_element({"session_id": "abc-123", "element": "Create contact"})
       ← {"execution_id": "...", "status": "SUCCESS"}

Model: → optics_enter_text({"session_id": "abc-123", "element": "First name", "text": "Alex"})
       ← {"execution_id": "...", "status": "SUCCESS"}

Model: → optics_enter_text({"session_id": "abc-123", "element": "Last name", "text": "Doe"})
       ← {"execution_id": "...", "status": "SUCCESS"}

Model: → optics_press_element({"session_id": "abc-123", "element": "Save"})
       ← {"execution_id": "...", "status": "SUCCESS"}

Model: → optics_terminate_session({"session_id": "abc-123"})
```

The session is real: drivers launch, screenshots come back as actual PNGs the model can see, and every keyword runs through the same `ExecutionEngine` your CSV/YAML test cases use.

## :material-link-variant: Coexisting with `optics serve`

The MCP server and the REST API server share the **same in-process `SessionManager`**. That means:

- A session you start over MCP can be inspected with the REST API (`GET /v1/sessions/{id}/screenshot`).
- A session you start over REST can be driven from an MCP client.
- If you run `optics serve` and `optics mcp` in the same process, sessions are shared. If you run them in separate processes, they each have their own session pool.

For most workflows you'll pick one or the other.

## :material-shield-alert: Security notes

- **No auth in v1.** The HTTP transport binds to `127.0.0.1` by default for that reason. Don't expose it to the public internet without putting it behind a reverse proxy with authentication.
- **MCP clients can do anything a keyword can do.** Treat the server like a remote shell: only connect it to AI clients you trust, and only point it at sessions you're comfortable letting the AI drive.
- **Screenshots and page source contain real app state** — including any PII or credentials on screen. The model sees what you'd see; assume anything you screenshot can end up in the model's context.

## :material-bug: Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| Client connects but no tools appear | The server didn't start. Run `optics mcp --transport stdio` in a terminal to see startup errors. |
| `optics_start_session` returns "session creation failed" | Driver isn't reachable (e.g. Appium not running) or `driver_sources` config is wrong. Same diagnostics as `optics serve`. |
| `session_id is required` on every keyword call | Model is skipping `optics_start_session`. The system prompt tells it to call that first; if your client doesn't pick up the prompt, mention it explicitly. |
| HTTP transport returns 307 on POST | A reverse proxy is rewriting `/mcp` to `/mcp/`. MCP clients POST to `/mcp` exactly — fix the proxy. |
| Tools show but calls hang | If you're on Windows and using `optics.exe` from a Poetry venv, make sure the client's working directory has access to the venv's drivers. |

## :material-source-branch: See also

- [CLI Usage](CLI_usage.md#serving-the-mcp-server) — `optics mcp` command summary
- [REST API Usage](REST_API_usage.md) — same keyword surface over HTTP
- [Keyword Usage](keyword_usage.md) — every keyword the MCP server exposes
- [MCP Server Architecture](../architecture/mcp_layer.md) — how the layer is built
- [Model Context Protocol spec](https://modelcontextprotocol.io)
