# Live Usage (`optics live`)

`optics live` opens a full-screen, interactive terminal session for building tests
keyword-by-keyword against a real device. It looks and behaves like Claude Code or
lazygit: the screen is taken over and redrawn in place, with a persistent input box
and status bar pinned at the bottom. Every successful action is recorded so you can
save the session as a reusable module.

## Launch

```bash
optics live                  # zero-config: works against the first connected device
optics live <project_folder> # use your project's config + elements
```

The folder is optional. With no config (or no folder), Optics auto-detects the
first Android device reported by `adb` and opens an Appium session using minimal
capabilities (`platformName: Android`, `automationName: UiAutomator2`, default
server at `http://127.0.0.1:4723`) — no `appPackage` required. You drop straight
into whatever screen the device is on (home screen, current app) and can `swipe`,
`press_keycode`, etc.

When a config.yaml **is** present, anything you specify wins and only missing
sections fall back to defaults. Named elements from the project are loaded lazily
the first time they are needed.

The session is opened automatically on launch (a `launch_app` action appears as
the first history entry). If your project's config specifies an `appPackage` /
`appActivity`, that app is launched; otherwise the session attaches without
starting any app.

## Layout

- **History pane** (top, scrollable): one entry per executed action showing the call
  as typed, pass/fail status (`✓` / `✗` / `⋯` while running), execution time, and the
  winning locator strategy (`[XPath]`, `[Text]`, `[OCR]`, `[Image]`). New entries are
  appended and the view auto-scrolls to the newest.
- **Input box** (pinned): where you type keyword calls and slash commands.
- **Status bar** (pinned, bottom): active device, the always-on recording indicator
  (`rec ●`), and a hint of available commands.

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
| `/device [id]`   | List connected devices; with no argument, pick one from a list to switch the active device (single active device only). |
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
| `Esc`          | Close any popup or the keyword browser |
| `Ctrl-C`       | Quit |

## Recording & saving

Recording is always on. Every successful keyword is appended to an in-memory buffer
in the order it ran. The buffer is only written to disk when you run `/save`. If you
`/quit` with unsaved actions, you are warned once — run `/save <name>` to keep them,
or `/quit` again to discard and exit.

### What `/save` persists vs. what `/quit` discards

The framework auto-generates screenshots and other diagnostic artifacts for every
keyword call (a pre-action screenshot, the post-action result image, AOI captures,
etc.). During a live session those land in a temp directory so they don't litter
your project.

* `/save <name>` snapshots that temp directory to `execution_output/<name>/` —
  so the screenshots that accompanied your recorded steps stay alongside the saved
  module. Re-running `/save` with the same name refreshes the snapshot.
* `/quit` (without a prior `/save`, or after discarding the unsaved-prompt) deletes
  the temp directory. Anything you explicitly chose to persist — `/save` outputs,
  `/screenshot` captures in `screenshots/` — is untouched.

## Logs

Every live session writes a chronological log of both the framework's internal
and execution loggers to `<project>/logs/optics_live_<timestamp>.log`. The path is
shown as the first entry in the history pane on startup, and again on stderr after
you `/quit`. Logs survive `/quit` regardless of whether you `/save` — they're for
diagnostics, not for the saved script.
