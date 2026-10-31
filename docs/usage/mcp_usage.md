# MCP Usage (`optics mcp`)

`optics mcp` runs a [Model Context Protocol](https://modelcontextprotocol.io)
server that exposes the optics-framework keyword engine to an LLM client
(Claude Desktop, Claude Code, Cursor, …). The model starts a device or browser
session, acts on the screen with **tools**, and reads device state through
**resources**. It drives a live target the way `optics live` does, but under
agent control.

The default toolset has ten tools. Each screen comes back as short rows, and
every row has a ref such as `e7`:

```
screen 21d99e · 7 rows
e1 h1 "Optics Store"
e3 input "Email" [tap,input] #email
e7 button "Sign in" [tap]
```

The agent picks a row by its ref, and the server turns the ref into a locator
that matches only that element. Every action returns what changed on the
screen and a verdict on the text the agent expected to see.

There are **no record, save or replay tools**. To turn a session into a suite,
the agent reads the `optics://session/{id}/steps` resource and the
`optics://project-format` resource, writes an optics CSV project, and runs it
with `optics execute`. The MCP layer is driver- and device-agnostic: it never
runs `adb` and does not assume a device type. It only calls the driver server
that you give to `start_session`.

It uses the same in-process keyword machinery as the REST server
(`optics serve`), so every driver and element source that optics supports works
here too.

---

## 1. Prerequisites

- **Python 3.12+** and optics-framework installed.
- **A driver target** that optics can reach — e.g. a local
  [Appium](https://appium.io) server with a connected device/emulator, or a
  remote Appium hub. You provide its URL and capabilities to `start_session`.
  For websites, Playwright launches its own browser, so there is nothing to run.
- An MCP-capable client (Claude Desktop/Code, Cursor, or the `fastmcp` Python
  client for scripting).
- *(Optional)* extras for richer element location: text detection
  (`googlevision`) and image detection (`templatematch`) require their own
  credentials/config, exactly as in a normal optics `config.yaml`.

## 2. Install

The MCP server depends on [`fastmcp`](https://github.com/PrefectHQ/fastmcp),
shipped as the `mcp` extra. The installer always includes it, finds a Python 3.12+
on its own and puts `optics` on your PATH. Then add the engine for your target:

```bash
curl -fsSL https://optics-framework.org/install | sh    # macOS, Linux
optics setup --install playwright    # websites (also downloads Chromium), or: appium
```

```powershell
irm https://optics-framework.org/install.ps1 | iex     # Windows
```

With pip, Optics needs **Python 3.12+** (an older Python fails or resolves an old
release) and you add the extra yourself:

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install 'optics-framework[mcp,playwright]' && playwright install chromium
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
| `--toolset` | `core` | `core` (ten tools) or `full` (also one tool per keyword); see §6 |

| Environment variable | Default | Meaning |
|----------------------|---------|---------|
| `OPTICS_MCP_TOOLSET` | `core` | the toolset when `--toolset` is not given |
| `OPTICS_MCP_SESSION_IDLE_S` | `900` | a session that is idle for longer is ended on the next call; `0` turns this off |
| `OPTICS_MCP_CALL_TIMEOUT_S` | `100` | the longest a tool call can take before it returns a timeout |
| `OPTICS_MCP_ALLOW_COORDINATE_TAPS` | off | `1` lets `run_keyword` reach `press_by_coordinates`, `press_by_percentage` and `swipe` |

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

In the Claude Code CLI, `claude mcp add optics -- optics mcp` writes the same entry.

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

1. **`start_session`** opens a session and returns its id. Pass `app` (a URL on
   the web, a package or bundle id on a device) and the reply also holds the
   first screen as rows. `elements_sources` default per driver, so a `driver`
   alone is enough. A device is named in `capabilities` (for example
   `deviceName` or `udid`); the MCP needs no `adb` of its own.
2. **Act** with `tap`, `type_text`, `scroll`, `press_key` and `launch_app`.
   Give a ref from the latest screen as the `target`. An exact visible label or a
   locator (`css=`, `xpath=`, `text=`, an id, an image file) also works, but a ref
   is safer: the server acts on it only through locators, never through fuzzy
   text or OCR.
3. **Verify.** Add `expect_present` and `expect_absent` to an action, with text
   that you already know and that the action shows or removes. Text that was on
   the screen before the action does not prove the step, and a value typed with
   `type_text` or a typing keyword such as `enter_text` is not checked, because
   the rows often do not hold it.
   The reply has `verified`: `pass`, `fail` or `unknown`. `ok` only means that
   the keyword ran; it does not mean that the step worked. Use `check` for a
   verdict without an action.
4. **Observe** only when you need more than the change list: `observe(query=…)`
   finds rows on a long screen, and `observe(screenshot=true)` adds a JPEG.
5. **Build a reusable suite** (see below).
6. **`terminate_session`** releases the driver when you are done.

`session_id` can be left out when only one session is open. A session that is
idle for longer than the idle lease (15 minutes by default) is ended on the
next call to the server.

### What an action returns

```
tap e7 "Sign in" -> ok (XPath) · verified: pass
check present "Welcome": met
screen 4436bc · changed (+2 -0 ~0)
+ e8 p "Welcome, a@b.c"
+ e9 a "Orders" [tap]
```

- The first line gives the target, the keyword result with the locator
  strategy that found it, and the verdict.
- One line follows for each expectation.
- The screen line is `unchanged`, `changed` with the added (`+`), removed
  (`-`) and relabelled (`~`) rows, or `new screen` with every row. Pass
  `screen="full"` for every row, or `screen="none"` for no screen line.
- A ref never moves to a different element. A ref that is gone from the
  screen is an error that lists the current rows.

The verdict rule:

| Result | `verified` |
|--------|------------|
| the keyword failed | `fail` |
| no expectations | `unknown` |
| every expectation met, and the screen changed or an `expect_present` that was not met before is met now | `pass` |
| an expectation not met, and the screen did not change | `fail` |
| an expectation not met, but the screen changed | `unknown` (the unmet checks are listed) |
| the screen could not be read after the action | `unknown`; the expectations are not checked |

Each acting tool also returns `structuredContent`:

```json
{"ok": true, "verified": "pass", "verb": "tap", "keyword": "press_element",
 "target": {"ref": "e7", "label": "Sign in", "ladder": ["xpath=//button[normalize-space()='Sign in']"], "strategy": "XPath"},
 "checks": [{"text": "Welcome", "expect": "present", "met": true}],
 "screen": {"digest": "4436bc", "changed": true, "new_screen": false,
            "added": [{"ref": "e8", "label": "Welcome, a@b.c", "cls": "p", "act": []}],
            "removed": [], "relabelled": []}}
```

`error` (`{code, kind, message}`) is set when the call failed, `screen.rows`
holds every row on a new screen, after an error and with `screen="full"`, and
`result` holds the keyword's return value (for example from `get_text` through
`run_keyword`).

### Build a reusable suite (there is no save tool)

1. Read **`optics://session/{session_id}/steps`**. It lists the session's
   actions and gives `modules_csv` and `elements_csv` rows for the steps that
   ran. The element rows hold the locators the server used for each ref, with
   the label as the last fallback. A value typed into a secure or password
   field, a field that shows a masked value, a field right after a caption
   such as "PIN", or a field whose label, id or locator has a secret word, is
   written as `<redacted>`; put the real value back before you run the suite. This applies
   to `type_text` and to `enter_text`, `enter_number`, `enter_text_direct` and
   `enter_text_using_keyboard`; a keyword that types without an element uses
   the field of the step before it. A passed `check` becomes an
   `Assert Presence` row: a ref becomes its locator, and a text that is only
   part of a row's text is left out with a note.
2. Read **`optics://project-format`** for the folder layout, `config.yaml`,
   `${variables}` and escaping rules.
3. Write the files. Parameterize by putting run-specific values in
   `elements.csv` and referencing them as `${name}`. A whole cell must be
   exactly `${name}`.
4. For loops, branches, computed values or API calls, write the control-flow
   keywords (`Run Loop`, `Condition`, `Evaluate`, `Invoke Api`, …) into the
   modules. They run under `optics execute`, not as live tools.
5. Optionally lint the suite with the
   [optics-framework-lsp](https://github.com/mozarkai/optics-framework-lsp)
   language server: `optics-lsp lint <folder> --json`.
6. Run it: `optics execute <folder>`. Results go to `execution_output/`.

### `start_session` arguments

| Arg | Type | Notes |
|-----|------|-------|
| `driver` | str | driver name, e.g. `"appium"` (default) |
| `app` | str | optional URL or app id to open before the first screen is read |
| `url` | str | driver/hub URL (e.g. local `http://127.0.0.1:4723` or a remote hub) |
| `capabilities` | object | driver capabilities (platform, device, app, auth…) |
| `elements_sources` | list[str] | element sources to enable; **optional** — defaults to the driver's canonical set (see §8) |
| `text_detection` | list[str] | optional OCR sources (e.g. `["googlevision"]`) |
| `image_detection` | list[str] | optional template sources (e.g. `["templatematch"]`) |
| `project_path` | str | optional project folder (loads bundled templates) |
| `ai_self_heal` | bool | optional per-session override of the server's `OPTICS_AI_SELF_HEAL` default (see below) |
| `llm_provider` | str | optional per-session override of the server's `OPTICS_LLM_PROVIDER` default (see below) |
| `llm_model` | str | optional per-session override of the server's `OPTICS_LLM_MODEL` default (see below) |
| `strict_element_match` | bool | reject fuzzy text matches for labels and locators (default `false`) |

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

**Example — a website with Playwright:**

```json
{
  "driver": "playwright",
  "capabilities": { "browser": "chromium", "headless": false },
  "app": "https://www.saucedemo.com"
}
```

`launch_app(app="<url>")` later navigates the same browser. The capabilities are the
Playwright driver's `config.yaml` capabilities (`browser`, `headless`, `viewport`,
`element_timeout_ms`, …; see [Configuration](../configuration.md#driver-sources)).

### AI self-heal

Self-heal has two layers. Whoever starts `optics mcp` (or `optics serve`) can
set a **server-level default** once via environment variables, so a client
that's already integrated inherits it with no `start_session` changes:

| Env var | Default | Meaning |
|---------|---------|---------|
| `OPTICS_AI_SELF_HEAL` | off | `true`/`1`/`yes`/`on` (case-insensitive) enables it |
| `OPTICS_LLM_PROVIDER` | `gemini` | which `llm_models` entry to enable: `gemini`, `openai` or `anthropic` |
| `OPTICS_LLM_MODEL` | `gemini-2.5-flash` for `gemini` | model name; **required** for `openai` and `anthropic`, which have no default |

Because a single `serve`/`mcp` process is multi-tenant, each of these is also a
**per-session override** on `start_session`: `ai_self_heal`, `llm_provider`, and
`llm_model`. A per-session value always wins over the matching env-var default,
so a caller can opt in/out and pick its own model without being locked to the
operator's choice; anything left unset falls back to the env default (and then to
`gemini`). LLM credentials always come from the provider's own environment
variables (`GEMINI_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`) — never through
`start_session` — so choosing a provider/model per session exposes no secrets. The
same goes for the endpoint: to route the `openai` engine to OpenRouter or a local
server, the operator sets `OPENAI_BASE_URL`; a caller cannot point the server's key
at a URL of its choosing.

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
shape under `data`. In the core toolset the same object is the `result` of the
action. Un-healed calls return the bare result, unchanged.

## 6. Tools reference

### Core toolset (default)

Every tool except `start_session` takes an optional `session_id`. The acting
tools (`tap`, `type_text`, `scroll`, `press_key`, `launch_app`, `run_keyword`)
also take `expect_present`, `expect_absent` and `screen`.

| Tool | Params | Does |
|------|--------|------|
| `start_session` | `driver`, `app`, `url`, `capabilities`, … (§5) | starts a session; with `app`, returns the first screen |
| `terminate_session` | — | ends the session and releases the driver |
| `observe` | `query`, `screenshot`, `max_rows` (1–300, default 80) | lists the screen as rows; issues refs |
| `tap` | `target`, `repeat` (1–20) | taps an element |
| `type_text` | `target`, `text`, `clear_first` | types into a field |
| `scroll` | `direction` (up/down/left/right), `until`, `timeout_s` (1–60) | scrolls; `direction` is the way the content moves, so `down` shows what is below. Left and right swipe on a device and scroll the page on a web driver. `until` works with up and down only |
| `press_key` | `key` | `back` goes one screen back on every driver; otherwise an Android KeyEvent number (such as `66`) or a web key name |
| `launch_app` | `app` | opens a URL, a package or a bundle id; empty opens the configured app |
| `check` | `present`, `absent`, `timeout_s` (0–30) | waits for the texts or refs, then gives a verdict (`passed`); a locator in `present` is checked with `assert_presence`. `timeout_s` is the limit for the whole check |
| `run_keyword` | `keyword`, `params` | runs any other keyword; an element param can be the ref of a row that has a locator |

`check` is a verdict: it is an error only when the server cannot read the
screen. `run_keyword` reaches every keyword in `optics://keywords` except the
session and capture internals. Coordinate keywords (`press_by_coordinates`,
`press_by_percentage`, `swipe`) need `OPTICS_MCP_ALLOW_COORDINATE_TAPS=1`,
because a pixel guessed from a screenshot often hits the wrong element. Keyword
timeout params are capped at 60 seconds. `run_keyword` is marked destructive
because it reaches `force_terminate_app`, `close_and_terminate_app` and
`execute_script`.

Every tool has the four MCP annotations (`readOnlyHint`, `destructiveHint`,
`idempotentHint`, `openWorldHint`).

### Errors

An error reply starts with the optics error code when there is one, then one
line of fact and one `Next:` line:

| Kind | Example |
|------|---------|
| `stale_ref` | `e3 ("Sign in" button) is not on the current screen.` and the current rows |
| `unknown_ref` | `No e99; current refs are e1–e12.` |
| `ambiguous` | the label matches more than one row; the refs are listed |
| `not_found` | `[E0201] not found: tried <locators>.` and the current rows |
| `invalid_params` | what is wrong, the real params and one correct call |
| `unsupported` | `[E0105] <tool> is not supported on this platform.` |
| `session_lost` | `[E0106] the driver session ended.` The session is closed. |
| `silent_failure` | `[E0401] the keyword logged an error and did not raise: …` and the screen change |
| `timeout` | the call took longer than `OPTICS_MCP_CALL_TIMEOUT_S` |
| `no_locator` | `run_keyword` got the ref of a row without a unique locator; pass a locator or the label |
| `busy` | an earlier call that timed out is still running on the device |
| `unknown_session` | the session ended after the idle lease, or never started |

### Full toolset (`--toolset full`)

The full toolset keeps the earlier surface for clients that call keywords by
name: one string-typed tool per keyword of `ActionKeyword`, `AppManagement`
and `Verifier` (each takes `session_id` and the keyword's params as strings),
the `screenshot` tool, and the core `start_session`, `terminate_session`,
`observe`, `tap`, `type_text`, `press_key`, `check` and `run_keyword`. The
keyword tools `scroll` and `launch_app` replace the core tools with the same
names. An element param of a keyword tool also accepts a ref from the latest
`observe`.

### Migration from the earlier surface

1. The default surface is now the core toolset. Use `run_keyword` or
   `--toolset full` for other keywords.
2. The `screenshot` tool is in the full toolset only. In the core toolset, use
   `observe(screenshot=true)`.
3. Acting tools return `structuredContent`; a keyword's return value is in
   `result`.
4. The `screen_elements` resource is deprecated.

## 7. Resources reference

| URI | Content |
|-----|---------|
| `optics://keywords` | the keywords `run_keyword` accepts (name, slug, description, params) |
| `optics://project-format` | how optics stores a suite (CSV layout and `${var}`) |
| `optics://session/{session_id}/steps` | the session's actions and `modules_csv`/`elements_csv` rows to replay them |
| `optics://session/{session_id}/screenshot` | screen as raw PNG bytes |
| `optics://session/{session_id}/source` | page source / UI hierarchy |
| `optics://session/{session_id}/elements` | compact elements without refs; reading it has no side effect |
| `optics://session/{session_id}/screen_elements` | deprecated: captured screen elements |

Only `observe` and the acting tools issue refs. The `elements` resource has the
same rows without refs.

## 8. Element sources decide what works

The keywords you can use depend on which `elements_sources` (and detection
sources) you enable in `start_session` — same rules as a normal optics project.
When you omit `elements_sources`, `start_session` enables the driver's canonical
trio automatically (appium → `appium_find_element`/`appium_page_source`/
`appium_screenshot`); pass the argument only to narrow or extend that. An
explicit **empty** list is passed through and disables element sources entirely
(the session then fails to start) — omit the argument for the defaults:

| Capability | Needs |
|------------|-------|
| Locate by `xpath` / `text` / `id`, tap, type | `appium_find_element` |
| Screenshots & image-based location | `appium_screenshot` |
| Page source, `observe`, `get_interactive_elements`, source-based extraction | `appium_page_source` |
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
  in this server before you use another tool; you cannot attach to a session
  created elsewhere. A session also ends after the idle lease
  (`OPTICS_MCP_SESSION_IDLE_S`).
- **Device busy / already allocated** — if your hub reports the device as busy,
  free it through your device-orchestration API, then retry `start_session`.
- **`observe` / `source` errors** — usually a missing element
  source; see §8.
- **Errors are tool results with `isError: true`.** The text gives the optics
  error code, the fact and the next step (see §6, Errors). The full toolset's
  keyword tools still raise a `ToolError` with the optics code and message.
- **A tool call timed out, and the next call says the session is busy.** The
  driver call is still running; it cannot be cancelled. Wait, then call
  `observe`. Raise `OPTICS_MCP_CALL_TIMEOUT_S` if your client allows longer
  calls.
- **`start_session` failed with "Element source configuration must be set".** You
  passed an explicit empty `elements_sources`, or the driver name doesn't match
  any installed element-source modules (e.g. a typo — check the name against
  §8). Omit `elements_sources` to get the driver defaults, or name the sources
  per §8.
- **Where do suites live?** Wherever you write them — the MCP has no workspace of
  its own. Author the CSV project (see `optics://project-format`) at a path you
  choose and run it with `optics execute`.
- **Setup errors (device unreachable, driver not running).** The MCP only talks to
  the driver server at the `url` you pass; make sure that server is up and can see
  the device (for Appium+Android that means its host has the Android SDK — the MCP
  itself needs none). `optics doctor` on that host diagnoses the toolchain.

## 10. How it works (pointer)

`optics mcp` is a thin in-process wrapper over `common/expose_api.py`. The core
tools map each verb to keyword calls with `common/agent_verbs.py`, run them
through the same `execute_keyword` path as the REST server, and read the screen
before and after with `common/screen_snapshot.py`, which issues the refs,
builds the locator ladders and computes the diff and the verdict. See
`optics_framework/helper/mcp_server.py` and the "MCP server journey" section of
`CLAUDE.md` for the internals.
