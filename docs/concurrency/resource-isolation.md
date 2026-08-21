# Resource Isolation

:material-check-circle: **Status: Current** — the *Target* section at the end is Phase 3 and not yet implemented.

Everything two sessions can collide over that isn't memory: files, directories, ports, and devices.

## Output paths

`execution_output_path` resolves to `<project_path>/execution_output`, or — when `project_path` is unset — **`os.getcwd()/execution_output`**. It is scoped **per project, not per session**.

Every artifact writer resolves that one directory:

| File | Session-scoped? | Write pattern |
|---|---|---|
| `junit_output_<session_id>.xml` (event handler) | :material-check: yes | append/rewrite tree |
| `detected_errors_<session_id>.json` | :material-check: yes | overwrite |
| `junit_output.xml` (pytest runner only) | :material-close: **no** | overwrite, last writer wins |
| screenshots `<µs-timestamp>-<name>.jpg` | :material-close: no directory scoping | write |
| `page_sources_log.xml` | :material-close: no | **read-modify-write** |
| `page_sources_log.html` | :material-close: no | append |
| `interactable_elements.json` | :material-close: no | overwrite |
| `api_details.log` | :material-close: no | append |
| `api_details.har` | :material-close: no | **read-modify-write** |
| `internal_logs.log` / `execution_logs.log` | :material-close: no | append, plus handler accumulation |

Screenshot *names* use microsecond timestamps so collisions are unlikely — but there is no session prefix, so artifacts from two runs are indistinguishable in one directory.

### The two dangerous ones

**`page_sources_log.xml`** is the most likely concrete corruption in the codebase, because it is rewritten on nearly every keyword — it's called from `get_page_source`, which every OCR and text strategy invokes.

The pattern: open `r+`, read the entire file, `seek(0)`, strip the trailing `</logs>`, write the whole thing back. No lock, no truncate. So:

1. Session A reads a 4 MB log and begins rewriting.
2. Session B reads the same 4 MB, appends its entry, writes.
3. A's write lands on top — B's entry is lost.
4. Because `r+` doesn't truncate, a shorter write leaves stale bytes *after* `</logs>`.
5. The next call's `endswith("</logs>")` guard fails, and page-source logging **silently stops for both sessions**.

**`api_details.har`** has the same shape — `json.load` the whole file, append an entry, `json.dump` back. One entry is lost per collision, and a partial write leaves invalid JSON, after which the `except json.JSONDecodeError` handler **discards the entire accumulated history** and starts fresh.

### `save_captures` is not honoured everywhere

`optics serve` sets `save_captures=False` specifically so HTTP sessions don't touch disk — callers get bytes and XML in the response and don't need a copy.

That flag is read by `Verifier` only. `ActionKeyword` and both UI helpers write regardless. So the server writes a JPEG and rewrites `page_sources_log.xml` on every keyword, into its shared working directory — in exactly the deployment where concurrent sessions are the norm, and where ephemeral container disk fills up silently.

## Ports and devices

!!! danger "There is no allocation, leasing, or locking of any kind"
    Grep the tree for `systemPort`, `wdaLocalPort`, `mjpegServerPort`, `chromedriverPort`: **zero occurrences**. Grep for `fcntl`, `flock`, or `filelock`: **zero occurrences**.

Those capabilities are *mandatory-unique* for parallel Appium sessions — UiAutomator2 defaults its server port to 8200, WDA defaults to 8100. Two sessions from the same `config.yaml` will either be rejected outright by Appium, or both UiAutomator2 servers will race for 8200 and **commands will land on the wrong device**.

Beyond ports:

- **No device lease.** Nothing stops two sessions targeting the same `udid`. Both also shell out to `adb` against the same serial, and to `ideviceinstaller -u <udid>`, with no serialization.
- **Shared default endpoints** with no arbitration: Appium at `127.0.0.1:4723`, Selenium at `localhost:4444/wd/hub`, remote OIR at `127.0.0.1:8080`.
- **Exclusive hardware**: the camera element source takes a single `camera_index` or TCP stream with no arbitration — two sessions on `camera_index: 0` conflict at the `cv2.VideoCapture` level.
- **`existingSessionId` collisions**: two configs carrying the same value both attach to the *same* Appium session, and the driver's cleanup path calls `driver.quit()` — killing it for the other session.

### Mitigation today

Set the ports explicitly, per session, in your own capabilities:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: http://127.0.0.1:4723
      capabilities:
        platformName: Android
        appium:udid: "<device-serial>"
        appium:systemPort: 8201        # unique per parallel session
        appium:mjpegServerPort: 7811   # unique per parallel session
        # iOS instead:
        # appium:wdaLocalPort: 8101
```

Optics passes capabilities through untouched, so this works — it just won't generate them for you. Allocating a device to exactly one session remains your responsibility; a Grid or cloud device farm does this for you and is the reason the served deployment targets one.

## Live-session collisions

`optics live` stamps its artifact directory and log file with `strftime("%Y-%m-%dT%H-%M-%S")` — **second precision, no session id**. Two sessions started in the same second share `screenshots/session_<stamp>/` (created with `exist_ok=True`, so silently) and both `FileHandler`s open the same log path.

`/tmp/optics_live_stderr.log` is a **fixed path opened `"w"`** (truncating) and then `dup2`'d onto fd 2. A second session truncates the file the first session's stderr points at, and both then write at independent offsets. On a shared host this is also a predictable-path write into a world-writable directory.

`/save` has a TOCTOU between its name-conflict check and its append: two sessions both pass the check, both append, and the duplicate is resolved downstream by an "Overwriting" warning — silently losing one recording. It then does `shutil.rmtree(destination)` followed by `copytree`, so one session's cleanup can delete the artifacts another is mid-copy into.

## Verified safe

Worth stating, so effort isn't spent here:

- **Per-session temp directories are correct.** `mkdtemp(prefix="optics_session_")` is unique, and termination `rmtree`s only that directory — never a parent or shared path. The per-request template dirs in `expose_api` are the same shape.
- **No global-config writes during runs.** `~/.optics/global_config.yaml` is written only by the interactive `optics config` TUI. Sessions never read or write it.
- **No process-global side effects** beyond logging: no signal handlers, no `faulthandler`, no `warnings.filterwarnings`, no OpenCV or matplotlib global tuning. One `atexit` hook, for logging shutdown.
- **Server ports are configurable** — `--host` / `--port`, and the MCP server defaults to a different port. No hardcoded bind.

## Target

:material-alert: **Status: Planned** (Phase 3).

- `execution_output_path` becomes `<base>/execution_output/<session_id>/`. Most of the table above resolves as a side effect.
- `page_sources_log.xml` becomes an append-only stream of fragments — no read-modify-write, no `</logs>` guard to corrupt.
- `api_details.har` gets the same treatment: append JSONL, assemble the HAR at close.
- `save_captures` gates every writer, including `ActionKeyword` and both UI helpers.
- The pytest runner's `junit_output.xml` gains the session suffix the event-handler path already uses.

Port and device allocation is **deliberately out of scope** for the current phases: the served deployment delegates device management to a Grid or cloud farm, which assigns ports itself. Anyone running parallel sessions against a *local* Appium server will hit this immediately, and should set the capabilities manually as shown above.
