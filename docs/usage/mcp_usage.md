# MCP Usage (`optics mcp`)

`optics mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io)
server that exposes the optics-framework keyword engine to an LLM client
(Claude Desktop, Claude Code, Cursor, …). The model can start a device/browser
session, run automation keywords as **tools**, and observe device state through
**resources** — driving a live target the way `optics live` does, but under
agent control.

It reuses the in-process keyword machinery from the REST server
(`optics serve`), so whatever driver and element sources optics already supports
work here too. The driver is chosen at runtime via the `start_session` tool —
nothing is hard-coded.

> Verified end-to-end against a remote Appium hub on a physical Samsung A53:
> `start_session` → `screenshot` (rendered PNG) → `swipe` → `terminate_session`.

---

## 1. Prerequisites

- **Python 3.12+** and optics-framework installed.
- **A driver target** that optics can reach — e.g. a local
  [Appium](https://appium.io) server with a connected device/emulator, or a
  remote Appium hub. You provide its URL and capabilities to `start_session`.
- An MCP-capable client (Claude Desktop/Code, Cursor, or the `fastmcp` Python
  client for scripting).
- *(Optional)* extras for richer element location: text detection
  (`googlevision`) and image detection (`templatematch`) require their own
  credentials/config, exactly as in a normal optics `config.yaml`.

## 2. Install

The MCP server depends on [`fastmcp`](https://github.com/PrefectHQ/fastmcp),
shipped as an **optional extra**:

```bash
pip install 'optics-framework[mcp]'
# from source:
poetry install --extras mcp
```

If the extra is missing, `optics mcp` exits with a clear message telling you to
install it — the rest of the CLI is unaffected.

## 3. Run the server

```bash
# stdio transport (default) — for local clients that spawn the process
optics mcp

# HTTP transport — for networked / multi-client use
optics mcp --transport http --host 127.0.0.1 --port 8090
```

| Flag | Default | Meaning |
|------|---------|---------|
| `--transport` | `stdio` | `stdio` (local clients) or `http` |
| `--host` | `127.0.0.1` | bind host (http only) |
| `--port` | `8090` | bind port (http only) |

### Docker

Containerized MCP runs **HTTP transport** bound to `0.0.0.0:8090` (stdio is for
local clients that spawn the process). Images live under `Docker/mcp/` and
install the `[mcp]` extra (`fastmcp`).

**Docker Compose** (from the repo root):

```bash
# Production image (PyPI) — host port 8090
docker compose -f Docker/docker-compose.yml up --build mcp

# Development image (local .whl) — host port 8091
docker compose -f Docker/docker-compose.yml up --build mcp-dev
```

**Standalone build/run:**

```bash
docker build -f Docker/mcp/prod/Dockerfile -t optics-mcp-prod .
docker run -d -p 8090:8090 --name optics-mcp-prod optics-mcp-prod
```

Connect your MCP client to the container:

```json
{
  "mcpServers": {
    "optics": { "url": "http://127.0.0.1:8090/mcp" }
  }
}
```

Use port **8091** for the `mcp-dev` compose service. When `start_session`
targets Appium on the host, set `"url": "http://host.docker.internal:4723"`.
See [`Docker/deployment.md`](../../Docker/deployment.md) for vision-backend
build args, Google Vision credential mounts, and dev-wheel builds.

## 4. Connect an MCP client

**stdio** — the client launches the server itself. Add to your client's MCP
config (e.g. Claude Desktop `claude_desktop_config.json`, or Claude Code
`.mcp.json`):

```json
{
  "mcpServers": {
    "optics": {
      "command": "optics",
      "args": ["mcp"]
    }
  }
}
```

If `optics` isn't on the client's `PATH`, use the absolute path (e.g.
`/path/to/.venv/bin/optics`) or `"command": "python", "args": ["-m",
"optics_framework.helper.cli", "mcp"]`.

**HTTP** — start the server yourself (`optics mcp --transport http`) and point
the client at the URL:

```json
{
  "mcpServers": {
    "optics": { "url": "http://127.0.0.1:8090/mcp" }
  }
}
```

## 5. The expected journey

1. **`start_session`** — open a session against your driver. Returns
   `{ "session_id", "driver_id" }`. The target app is launched automatically.
   Capture the `session_id`; **every** other tool and resource needs it.
2. **Observe** — read a resource or call `screenshot` to see the screen, read
   `optics://session/{session_id}/source` for the UI hierarchy, or call
   `get_interactive_elements` for tappable elements.
3. **Act** — call keyword tools (`press_element`, `enter_text`, `swipe`,
   `assert_presence`, …) with the `session_id`.
4. **`terminate_session`** — release the driver when done.

### `start_session` arguments

| Arg | Type | Notes |
|-----|------|-------|
| `driver` | str | driver name, e.g. `"appium"` (default) |
| `url` | str | driver/hub URL (e.g. local `http://127.0.0.1:4723` or a remote hub) |
| `capabilities` | object | driver capabilities (platform, device, app, auth…) |
| `elements_sources` | list[str] | element sources to enable (see §7) |
| `text_detection` | list[str] | optional OCR sources (e.g. `["googlevision"]`) |
| `image_detection` | list[str] | optional template sources (e.g. `["templatematch"]`) |
| `project_path` | str | optional project folder (loads bundled templates) |

**Example — local Appium + Android emulator:**

```json
{
  "driver": "appium",
  "url": "http://127.0.0.1:4723",
  "capabilities": {
    "platformName": "Android",
    "appium:automationName": "UiAutomator2",
    "appium:deviceName": "emulator-5554",
    "appium:appPackage": "com.android.settings",
    "appium:appActivity": ".Settings"
  },
  "elements_sources": ["appium_find_element", "appium_page_source", "appium_screenshot"]
}
```

Omit `appPackage`/`appActivity` to attach to whatever is already on screen. For
a remote/managed hub, set `url` to the hub and include any hub-specific
capabilities (auth token, device id) just as you would in `config.yaml`.

### Keyword parameters are strings

Every keyword tool takes `session_id` plus that keyword's parameters, and all
parameters are typed as **strings** — pass `"2"`, not `2`. Element arguments
accept the same locators optics uses elsewhere: `xpath=…`, `text=…`, `css=…`, an
`id`, or an image template name.

## 6. Tools reference

`start_session`, `terminate_session`, and `screenshot` are purpose-built; every
other tool is an optics keyword auto-exposed from `ActionKeyword` /
`AppManagement` / `Verifier`. Representative set:

- **Session/app:** `start_session`, `terminate_session`, `launch_app`,
  `launch_other_app`, `close_and_terminate_app`, `get_app_version`,
  `get_driver_session_id`.
- **Interact:** `press_element`, `press_by_coordinates`, `press_by_percentage`,
  `press_keycode`, `enter_text`, `enter_number`, `clear_element_text`,
  `select_dropdown_option`, `detect_and_press`.
- **Gestures/scroll:** `swipe`, `swipe_by_percentage`, `swipe_from_element`,
  `swipe_until_element_appears`, `scroll`, `scroll_from_element`,
  `scroll_until_element_appears`.
- **Observe/verify:** `screenshot` (rendered image), `get_text`,
  `get_interactive_elements` (accepts `filter_config`, e.g. `"buttons"`),
  `is_element`, `assert_presence`, `assert_equality`, `validate_element`,
  `validate_screen`.
- **Misc:** `sleep`, `execute_script`.

The full machine-readable catalog (every keyword, its params and docs) is the
`optics://keywords` resource.

`screenshot` returns a rendered `image/png` your client can display inline —
prefer it over the screenshot resource when you want to *see* the screen.

## 7. Resources reference

| URI | Content |
|-----|---------|
| `optics://keywords` | full keyword catalog (name, slug, description, params) |
| `optics://session/{session_id}/screenshot` | screen as raw PNG bytes |
| `optics://session/{session_id}/source` | page source / UI hierarchy |
| `optics://session/{session_id}/elements` | interactive elements (unfiltered) |
| `optics://session/{session_id}/screen_elements` | captured screen elements |

`get_interactive_elements` is available **both** as a resource (unfiltered) and
as a tool (so the model can pass `filter_config`).

> The screenshot **resource** delivers raw PNG bytes with a generic
> `application/octet-stream` mime (a limitation of templated MCP resources). For
> an image your client renders as a picture, use the **`screenshot` tool**.

## 8. Element sources decide what works

The keywords you can use depend on which `elements_sources` (and detection
sources) you enable in `start_session` — same rules as a normal optics project:

| Capability | Needs |
|------------|-------|
| Locate by `xpath` / `text` / `id`, tap, type | `appium_find_element` |
| Screenshots & image-based location | `appium_screenshot` |
| Page source, `get_interactive_elements`, source-based extraction | `appium_page_source` |
| OCR / locate visible text on screen | a `text_detection` source (e.g. `googlevision`) |
| Image template matching | an `image_detection` source (e.g. `templatematch`) |

If you enable only `appium_find_element` + `appium_screenshot` and then call
`get_interactive_elements`, optics raises
`E0202: No interactive elements retrieved using available strategies` — that's
expected; enable `appium_page_source` (or a vision source) for that path.

## 9. Troubleshooting

- **First `start_session` is slow against a remote hub** (~30–60 s to allocate
  and launch). Give your client a generous timeout (the `fastmcp` Python client
  takes `Client(url, timeout=180)`).
- **`No module named 'fastmcp'` / "mcp extra required"** — install
  `optics-framework[mcp]`.
- **Sessions aren't shared with `optics serve` / `optics live`.** Each is a
  separate process with its own in-memory session store. Always `start_session`
  in this server before using a keyword tool; you cannot attach to a session
  created elsewhere.
- **Device busy / already allocated** — if your hub reports the device as busy,
  free it through your device-orchestration API, then retry `start_session`.
- **`get_interactive_elements` / `source` errors** — usually a missing element
  source; see §8.
- **Errors surface as MCP tool errors.** An optics failure (element not found,
  bad config, driver error) comes back as a `ToolError` carrying the optics
  error code/message, so the model can read and react to it.

## 10. How it works (pointer)

`optics mcp` is a thin in-process wrapper over `common/expose_api.py`. It
reflects the API keyword classes into typed tools and routes execution through
the same `execute_keyword` path the REST server uses; read-only observers become
resources. See `optics_framework/helper/mcp_server.py` and the "MCP server
journey" section of `CLAUDE.md` for the internals.
