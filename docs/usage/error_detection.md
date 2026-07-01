# On-Screen Error Detection

Optics can scan the screen for user-defined error messages (crash dialogs,
network errors, "Session expired", etc.) and report any matches. Detection runs
automatically at the end of a CLI run, and is also exposed to library consumers
as a plain data primitive.

## How matching works

Each error definition has a `match_string`. Optics extracts the **visible text**
from the current screen and checks whether any `match_string` (or the
`error_code` itself) appears in it.

!!! important "Matching is case-insensitive substring matching — not regex"
    `match_string` is treated as **plain text** and tested with a substring
    (`in`) check, **after lower-casing both sides**. It is **not** a regular
    expression or a glob pattern.

    - `Session expired` matches `Your session expired, please log in`.
    - `session expired` also matches `SESSION EXPIRED` (case-insensitive).
    - Regex metacharacters are **not** interpreted: `error.*` only matches the
      literal text `error.*`.

### What "visible text" means

Optics only searches text a user can actually see, to avoid false positives
against internal identifiers:

- **Mobile (Appium / XCUITest XML):** values of the `text`, `content-desc`,
  `label`, `value`, `hint`, and `name` attributes. Resource-ids, class names,
  bounds, and other metadata are ignored.
- **Web (Selenium / Playwright HTML):** the rendered text content
  (via BeautifulSoup `get_text()`); tags, attributes, and CSS class names are
  stripped. HTML detection requires the `beautifulsoup4` package (installed as a
  dependency).

## Defining errors via CSV (CLI)

Place an `error_definitions.csv` file in your project's `test_data/` directory
(alongside your test cases and modules). Optics discovers it automatically by
its header row.

```csv
error_code,match_string,description,severity
ERR_001,Unfortunately,App has crashed with a system dialog,critical
ERR_002,has stopped,App stopped unexpectedly,critical
ERR_009,Session expired,User session timed out,high
```

| Column         | Required | Meaning                                                        |
| -------------- | -------- | -------------------------------------------------------------- |
| `error_code`   | yes      | Stable identifier for the error.                               |
| `match_string` | yes      | Case-insensitive substring to look for in on-screen text.      |
| `description`  | no       | Human-readable explanation.                                    |
| `severity`     | no       | Free-form severity label (e.g. `critical`, `high`, `medium`).  |

At the end of an `optics execute` run, any matches are:

- logged as warnings in the execution log,
- written to `execution_output/detected_errors_<session_id>.json`,
- and added to `junit_output.xml` as a synthetic `on-screen-error-detection`
  testcase with one `<failure>` per match — so CI tools (Jenkins, GitLab CI,
  GitHub Actions) surface them and fail the build.

## Defining errors via the library (Python)

When using the `Optics` class directly, you decide how to supply the
definitions — no CSV required. Pass a raw dict or an `ErrorDefinitions` model:

```python
from optics_framework import Optics

optics = Optics()
optics.setup(...)

# Register error definitions (merges on repeated calls)
optics.add_error_definitions({
    "ERR_009": {
        "match_string": "Session expired",
        "description": "User session timed out",
        "severity": "high",
    },
})

# ... drive the app ...

# Scan the current screen; returns a list of matched dicts (never raises).
matches = optics.capture_and_detect(context_label="after_login", test_case="login_flow")
for m in matches:
    print(m["error_code"], m["matched_on"], m["match_string"])
```

`capture_and_detect` is a pure data primitive: it writes no files and swallows
driver errors (returning `[]`) so a flaky session never breaks your workflow.
Each returned dict contains `error_code`, `matched_on` (`"match_string"` or
`"code"`), `match_string`, `description`, `severity`, plus `detected_at`
(the `context_label`) and, when provided, `test_case`.
