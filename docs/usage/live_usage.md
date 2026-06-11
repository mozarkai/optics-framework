# Live Usage (`optics live`)

`optics live` opens a full-screen, interactive terminal session for building tests
keyword-by-keyword against a live target (device or browser). It looks and behaves like Claude Code or
lazygit: the screen is taken over and redrawn in place, with a persistent input box
and status bar pinned at the bottom. Every successful action is recorded so you can
save the session as a reusable module.

## Launch

```bash
optics live                  # uses the config.yaml in the current directory
optics live <project_folder> # uses the config.yaml in that project folder
```

`optics live` is **driver-agnostic and config-driven**: the same flow works for
Android/iOS (Appium), web (Selenium, Playwright), TV, etc. The driver comes entirely
from the project's `config.yaml`, so a session "just works" for whatever you configure.

A `config.yaml` is **required** — there are no built-in defaults. It must declare:

- **exactly one** enabled entry under `driver_sources` (`appium`, `selenium`,
  `playwright`, …), and
- at least one enabled entry under `elements_sources`.

If no usable config is found, or the config has no enabled driver / more than one
enabled driver / no enabled element source, `optics live` exits with a clear message.
A malformed `config.yaml` reports the actual parse/validation error (not "no config").

See `optics_framework/samples/` for ready-made configs: `contact` (Appium/Android),
`gmail_web` (Selenium), `playwright` (Playwright). Named elements from the project are
loaded lazily the first time they are needed.

The session is opened automatically on launch (a `launch_app` action appears as the
first history entry). Each driver interprets `launch_app` using its own config: Appium
launches the configured `appPackage`/`appActivity`, while Selenium/Playwright navigate
to the configured URL.

## Layout

- **History pane** (top, scrollable): one entry per executed action showing the call
  as typed, pass/fail status (`✓` / `✗` / `⋯` while running), execution time, and the
  winning locator strategy (`[XPath]`, `[Text]`, `[OCR]`, `[Image]`). New entries are
  appended and the view auto-scrolls to the newest.
- **Input box** (pinned): where you type keyword calls and slash commands.
- **Status bar** (pinned, bottom): the active **target** — labelled by driver, e.g.
  `appium:emulator-5554`, `selenium:chrome`, `playwright:chromium` — the always-on
  recording indicator (`rec ●`), and a hint of available commands.

## Running keywords

Type a keyword call and press Enter, for example:

```
launch_app
press_element ${login_btn} index=0
enter_text ${username} "hello world"
sleep 5
```

- Keyword names come live from the framework's `KeywordRegistry`, so autocomplete
  always matches what the runner supports.
- `${name}` references resolve against the project's named elements, with the same
  fallback behaviour as the batch runner (each locator is tried in order).
- `key=value` tokens are passed as keyword arguments.
- A failing keyword is shown as `✗` with a short error (and error code) and is **not**
  recorded; the prompt returns ready for the next command. The UI never crashes.

## Autocomplete & hints

- **Keyword completion** — start typing the first token and press Tab.
- **Element completion** — type `${` to get element names, each shown with its first
  locator.
- **Ghost-text parameter hints** — once a keyword is recognised, its parameter
  signature is shown dimmed after the cursor: required params in `<>`, optional in `[]`.
- **Keyword browser** — press `Ctrl-K` for a navigable list of every keyword
  (Up/Down to move, Enter to drop it into the input box, Esc to close).

## Slash commands

| Command          | Description |
|------------------|-------------|
| `/save <name>`   | Save the recorded actions to `modules/<name>.csv` + `test_cases/<name>.csv`, **and** snapshot every screenshot/artifact the framework generated this session to `execution_output/<name>/`. Re-saving updates the snapshot. |
| `/device [id]`   | **Android/Appium sessions only.** List connected `adb` devices; with no argument, pick one to switch the active device. For Selenium/Playwright/iOS sessions it reports that switching doesn't apply (the target is the configured browser/device). |
| `/elements`      | Open a read-only popup of named elements and their locators (Esc closes). |
| `/screenshot`    | Capture the current device screen to a file and note the path in the history. |
| `/help`          | Show the command reference (Esc closes). |
| `/quit`          | End the session, run the normal driver teardown/cleanup, and exit. |

## Keys

| Key            | Action |
|----------------|--------|
| `Enter`        | Run the command, or accept the highlighted completion |
| `Tab` / `S-Tab`| Cycle completions |
| `${`           | Suggest element names |
| `Ctrl-K`       | Toggle the keyword browser |
| `Ctrl-N`       | Toggle natural-language (AI) mode |
| `Ctrl-X`       | Abort a running AI run (stops at the next step) |
| `Esc`          | Close any popup or the keyword browser |
| `Ctrl-C`       | Quit |

## Natural-language mode (optional)

Press `Ctrl-N` to toggle AI mode, then type an instruction in plain English
(e.g. `type "movies for kids" in the search bar`). Each turn the LLM is given a
screenshot **and** a condensed UI hierarchy of the on-screen elements (their text,
content-desc, resource ids, bounds, and flags — when the driver exposes a page source),
and drives keywords step-by-step until the goal is reached; `Ctrl-X` aborts. A fully
successful run is recorded and `/save`-able like any manual session. It works with whatever
driver your config uses (Appium/Selenium/Playwright). Three steps to enable it:

**1. Install the optional LLM extra** (the SDK is not in the base install):

```bash
pip install 'optics-framework[llm]'
# or, from a clone with poetry:
poetry install --extras llm
```

**2. Provide Gemini credentials** (read from the environment — don't hardcode them).
Pick one backend:

```bash
# A. Gemini Developer API (key from Google AI Studio)
export GEMINI_API_KEY=your_key            # GOOGLE_API_KEY also works

# B. Vertex AI
export GOOGLE_GENAI_USE_VERTEXAI=true
export GOOGLE_CLOUD_PROJECT=your-project
export GOOGLE_CLOUD_LOCATION=us-east4
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service_account.json
```

**3. Enable `gemini` in your project's `config.yaml`** (alongside your driver/element sources):

```yaml
llm_models:
  - gemini:
      enabled: true
      capabilities:
        model: gemini-2.5-flash    # optional; this is the default
        # use_vertexai: true       # optional; else uses GOOGLE_GENAI_USE_VERTEXAI
        # project / location       # optional Vertex overrides; else env vars
```

All `capabilities` are optional — with none set, the SDK auto-detects everything from the
environment variables above. The `google-genai` SDK is **only imported when `gemini` is
enabled**, so non-AI users are unaffected. If you toggle `Ctrl-N` without an enabled
`llm_models` entry, the session simply tells you to add one; if the extra isn't installed,
you get a clear install hint.

## Recording & saving

Recording is always on. Every successful keyword is appended to an in-memory buffer
in the order it ran. The buffer is only written to disk when you run `/save`. If you
`/quit` with unsaved actions, you are warned once — run `/save <name>` to keep them,
or `/quit` again to discard and exit.

### Screenshots are saved automatically

The framework auto-generates screenshots for every keyword call (a pre-action
screenshot, the post-action result image, and annotated/AOI captures). In a live
session these are written to a **persistent per-session folder**,
`screenshots/session_<timestamp>/`, and **survive `/quit`** — so every keyword you run
(typed or AI-driven) leaves a visual record with no extra step. The `<timestamp>`
matches the session log (`logs/optics_live_<timestamp>.log`) so the two correlate. An
empty session folder (no screenshots captured) is removed on exit; otherwise it's kept.

`/save <name>` additionally bundles a **copy** of that session's screenshots into
`execution_output/<name>/`, alongside the saved module, so a saved test is
self-contained. Re-running `/save` with the same name refreshes the copy.

> The screenshots that the AI mode sends to the model are captured separately and are
> not written to disk; what you see in `screenshots/session_<timestamp>/` are the
> keyword pre/post/annotated frames.

## Logs

Every live session writes a chronological log of both the framework's internal
and execution loggers to `<project>/logs/optics_live_<timestamp>.log`. The path is
shown as the first entry in the history pane on startup, and again on stderr after
you `/quit`. Logs survive `/quit` regardless of whether you `/save` — they're for
diagnostics, not for the saved script.
