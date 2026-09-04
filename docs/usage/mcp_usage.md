# MCP Usage (`optics mcp`)

`optics mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io)
server that exposes the optics-framework keyword engine to an LLM client
(Claude Desktop, Claude Code, Cursor, …). The model can start a device/browser
session, run automation keywords as **tools**, observe device state through
**resources**, and then **record what it did into a replayable test suite** —
driving a live target the way `optics live` does, but under agent control.

The full loop is: **onboard** (`doctor` / `list_devices` / `list_available_sources`)
→ **connect** (`start_session`) → **interact** (keyword tools + `find_elements`) →
**cherry-pick** (`start_recording`, drop mis-fires) → **build a suite**
(`save_test_case` / `save_suite`) → **replay** (`run_test_case` / `run_suite`), all
without leaving the MCP surface. Suites are stored as a real optics CSV project, so
they graduate to `optics execute` (CI, pipelines) via `export_optics_project`.

It reuses the in-process keyword machinery from the REST server
(`optics serve`), so whatever driver and element sources optics already supports
work here too. The driver is chosen at runtime via the `start_session` tool —
nothing is hard-coded.

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

0. **Onboard** *(optional but recommended)* — `doctor` checks your environment
   (Python, adb + Android SDK, Appium reachability, browser binaries) and returns
   an actionable `fix` for anything wrong; `list_devices` shows connected devices;
   `list_available_sources` lists valid engine source names so you never guess them.
1. **`start_session`** — open a session against your driver. Returns
   `{ "session_id", "driver_id" }`. The target app is launched automatically.
   Capture the `session_id`; **every** other tool and resource needs it.
   `elements_sources` now **defaults per driver** (appium →
   `appium_find_element`/`appium_page_source`/`appium_screenshot`), so
   `start_session` with just a `driver` works. Reconnect to a still-live session
   after a context reset with `list_sessions` / `session_info`.
2. **Observe** — call `find_elements` (filtered, compact, paginated — the
   token-safe way to inspect the screen) and `get_screen_size`; or read
   `optics://session/{session_id}/source`, or call `screenshot`. On Android,
   `get_current_app` / `list_installed_apps` avoid guessing package names.
3. **Act** — call keyword tools (`press_element`, `enter_text`, `swipe`,
   `assert_presence`, …) with the `session_id`. Prefer targeting a `find_elements`
   result's `xpath`/`text` over raw coordinates.
4. **Build a reusable suite** — see below.
5. **`terminate_session`** — release the driver when done.

### Record → save → replay (the reusable-artifact loop)

Individual keyword calls are one-offs. To turn a session into a suite:

1. **`start_recording(session_id)`** — from here, every *successful* action
   keyword is captured (with its resolved args); observers like `screenshot` and
   `get_interactive_elements` are not. Mis-fired taps that error aren't recorded.
2. **Cherry-pick** — `list_recorded_steps`, then `remove_step` / `edit_step` to
   drop or fix a step, or `clear_recording` to start over.
3. **`save_test_case(session_id, name, variables?)`** — persists the recording as
   a replayable test case in the MCP **workspace** (a real optics CSV project;
   defaults to `~/.optics/mcp_workspace`, override with `OPTICS_MCP_WORKSPACE`).
   `variables` (a JSON object `{ "hour": "22" }`) parameterizes the module: any arg
   equal to a default becomes `${hour}`, so the same test case replays with new
   values. You can also pass explicit `steps` instead of using the recording.
4. **Replay** — `run_test_case(session_id, name, params?)` replays against the
   live session and returns per-step pass/fail plus a base64 screenshot on the
   failing step; `params` overrides `${var}` values. Group test cases with
   `save_suite` and replay them together with `run_suite`.
5. **Graduate** — `export_optics_project(dest_path, session_id?)` writes a
   standalone `optics execute`-able project (CSVs + `config.yaml`);
   `import_optics_project(path)` reads an existing project back in for editing/replay.

### `start_session` arguments

| Arg | Type | Notes |
|-----|------|-------|
| `driver` | str | driver name, e.g. `"appium"` (default) |
| `url` | str | driver/hub URL (e.g. local `http://127.0.0.1:4723` or a remote hub) |
| `capabilities` | object | driver capabilities (platform, device, app, auth…) |
| `elements_sources` | list[str] | element sources to enable; **optional** — defaults to the driver's canonical set (see §8). Call `list_available_sources` to see the choices |
| `text_detection` | list[str] | optional OCR sources (e.g. `["googlevision"]`) |
| `image_detection` | list[str] | optional template sources (e.g. `["templatematch"]`) |
| `project_path` | str | optional project folder (loads bundled templates) |
| `ai_self_heal` | bool | optional per-session override of the server's `OPTICS_AI_SELF_HEAL` default (see below) |
| `llm_provider` | str | optional per-session override of the server's `OPTICS_LLM_PROVIDER` default (see below) |
| `llm_model` | str | optional per-session override of the server's `OPTICS_LLM_MODEL` default (see below) |

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

### AI self-heal

Self-heal has two layers. Whoever starts `optics mcp` (or `optics serve`) can
set a **server-level default** once via environment variables, so a client
that's already integrated inherits it with no `start_session` changes:

| Env var | Default | Meaning |
|---------|---------|---------|
| `OPTICS_AI_SELF_HEAL` | off | `true`/`1`/`yes`/`on` (case-insensitive) enables it |
| `OPTICS_LLM_PROVIDER` | `gemini` | which `llm_models` entry to enable |
| `OPTICS_LLM_MODEL` | provider default | optional model name override |

Because a single `serve`/`mcp` process is multi-tenant, each of these is also a
**per-session override** on `start_session`: `ai_self_heal`, `llm_provider`, and
`llm_model`. A per-session value always wins over the matching env-var default,
so a caller can opt in/out and pick its own model without being locked to the
operator's choice; anything left unset falls back to the env default (and then to
`gemini`). LLM credentials (e.g. `GOOGLE_API_KEY`/`GEMINI_API_KEY` for Gemini)
always come from the provider's own environment variables — never through
`start_session` — so choosing a provider/model per session exposes no secrets.

When self-heal recovers a keyword, the call still succeeds, and the result carries
the recovery so a client can learn *how* it was fixed:

```json
{
  "result": null,
  "healed": true,
  "heal_summary": "AI self-heal recovered 'press_element' after 2 steps: scroll down; press_element Login",
  "suggested_steps": [
    { "keyword": "scroll", "params": ["down"] },
    { "keyword": "press_element", "params": ["Login"] }
  ]
}
```

`suggested_steps` is the clean, replayable recovery sequence — only the steps that
actually worked, curated to the minimal set that reproduces the goal. A platform can
persist these to replace the failing step, so the next run passes without needing
self-heal. `optics serve`'s `POST /v1/sessions/{session_id}/action` returns the same
shape under `data`. Un-healed calls return the bare result, unchanged.

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

Beyond the reflected keywords, these **purpose-built** tools add onboarding,
discovery, and the reusable-suite surface:

- **Onboarding / diagnostics:** `doctor` (environment health with fixes),
  `list_devices` (adb + libimobiledevice), `list_available_sources` (engine
  source names by category/driver).
- **Discovery / reliability:** `find_elements` (filter by `text`/`resource_id`/
  `class_name`/`clickable`/`region`, paginate with `offset`/`limit`, `compact`
  by default), `get_screen_size` (pixel dimensions — target with element
  `bounds`/`center` instead of eyeballing), `get_current_app` /
  `list_installed_apps` (Android/adb, best-effort).
- **Session durability:** `list_sessions`, `session_info` — reconnect to a
  still-live session after a context reset instead of recreating it.
- **Recording:** `start_recording`, `stop_recording`, `recording_status`,
  `list_recorded_steps`, `edit_step`, `remove_step`, `clear_recording`.
- **Suite authoring/storage:** `save_test_case`, `list_test_cases`,
  `get_test_case`, `delete_test_case`, `save_suite`, `list_suites`, `get_suite`.
- **Suite execution:** `run_test_case`, `run_suite` (per-step pass/fail + a
  failure screenshot).
- **Portability:** `export_optics_project`, `import_optics_project`.

Tool args are strings (see §5); object/array args (e.g. `variables`, `steps`,
`test_cases`, `params`) are passed as **JSON strings**, e.g.
`variables='{"hour": "22"}'`.

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
sources) you enable in `start_session` — same rules as a normal optics project.
When you omit `elements_sources`, `start_session` enables the driver's canonical
trio automatically (appium → `appium_find_element`/`appium_page_source`/
`appium_screenshot`); pass the argument only to narrow or extend that:

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
- **`start_session` failed with "Element source configuration must be set".** You
  passed an explicit empty `elements_sources`. Omit it to get the driver defaults,
  or run `list_available_sources` to pick names.
- **`get_current_app` / `list_installed_apps` say "Android-only".** They shell out
  to `adb` and only work for Android sessions with `adb` on the server's PATH;
  they are best-effort helpers, not part of the driver contract.
- **Saved test cases aren't where I expect.** They live in the MCP workspace,
  `~/.optics/mcp_workspace` by default. Set `OPTICS_MCP_WORKSPACE` before starting
  the server to relocate it, or use `export_optics_project` to write a copy.
- **A recorded step is missing / extra.** Only *successful* action keywords are
  recorded; observers (`screenshot`, `get_interactive_elements`, `get_text`, …)
  and failed calls are skipped. Use `list_recorded_steps` + `remove_step` /
  `edit_step` to curate before `save_test_case`. Saving from the recording clears
  the buffer (one test case per save).
- **Recordings/sessions vanished.** Both live only in this server process; a
  restart clears them. Persist with `save_test_case` / `export_optics_project`.

## 10. How it works (pointer)

`optics mcp` is a thin in-process wrapper over `common/expose_api.py`. It
reflects the API keyword classes into typed tools and routes execution through
the same `execute_keyword` path the REST server uses; read-only observers become
resources. The onboarding/discovery helpers live in
`optics_framework/helper/mcp_diagnostics.py` (device + engine discovery, reusing
`optics doctor`'s checks), and recording + suite storage/replay in
`optics_framework/helper/mcp_authoring.py` (the canonical CSV project layout). See
`optics_framework/helper/mcp_server.py` and the "MCP server journey" section of
`CLAUDE.md` for the internals.
