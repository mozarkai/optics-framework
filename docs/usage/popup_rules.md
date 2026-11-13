# Popup Rules

Apps put popups in the way of a test: an offer, a rating request, a session warning. Without help, the step that
needs the element behind the popup fails with `E0201`. With popup rules, a step that cannot find its element first
checks the screen against your rules, runs the action you chose, confirms the popup is gone and looks again.

A popup that no rule covers is never guessed at. The step stops with the evidence a rule can be written from.

## How it works

1. A step looks for its element through every locator strategy and finds nothing.
2. Optics reads the page source once and tries the rules, lowest `priority` number first. The first rule whose
   `detect` matches wins.
3. The rule's `action` runs (`tap`, `back`, `alert` or `none`). Optics then reads the screen again, waiting up to
   5 seconds for the popup to go.
4. When it is gone, `then` decides what happens next: `retry_step` runs the step again, `fail_step` fails the step
   and `stop_run` ends the run.

A step that finds its element pays nothing: nothing is read and no rule is tried. The one exception is a rule with
`check: before_every_step`, which costs one page-source read before each step that locates an element.

Because the check runs only after the lookup failed, the step's own action never ran, so a retry cannot repeat a
transfer or a tap.

!!! note "Popup rules are off until you define one"
    With no rules, nothing changes: no extra page-source reads and no `E0206`. The unknown-popup check below needs
    at least one rule to be defined.

## Where the rules go

The rules are one list, in any of three places. They merge, and a later rule replaces an earlier one of the same
name.

| Where | When to use it |
|-------|----------------|
| `test_data/popup_rules.json` in the project folder | `optics execute` jobs. Found by its name, anywhere under the folder. It wins over a same-named rule in `config.yaml` |
| `popup_rules` at the top of `config.yaml` or of a session's config | Live sessions and the REST API |
| `Optics.add_popup_rules(rules)` (`Add Popup Rules` in Robot) | Library use |

A rule list that cannot be read stops the run with `E0501`, naming the file and the rule, instead of quietly
handling no popups.

## Rule fields

| Field | Values | Meaning |
|-------|--------|---------|
| `name` | text | Unique within the list |
| `priority` | integer, lowest first | The first matching rule wins. Give sensitive rules the lowest numbers |
| `detect.text_contains` | list of text | Any one, as a case-insensitive substring of the visible text. On iOS this includes the text of a system alert |
| `detect.resource_id` | text | Android `resource-id`, matched exactly. On iOS use `detect.xpath` on the `name` attribute (the accessibility id) |
| `detect.xpath` | text | An XPath over the page source |
| `check` | `on_failure` (default), `before_every_step` | When the rule is tried |
| `action.type` | `tap`, `back`, `alert`, `none` | `alert` is iOS only and answers a system alert, pressing the button named in `action.target` |
| `action.target`, `action.fallback` | an xpath, a resource-id or visible text | Where to press. The fallback is tried if the target is not found. See [How a target is found](#how-a-target-is-found) |
| `then` | `retry_step` (default), `fail_step`, `stop_run` | What happens after the action |
| `sensitive` | `true` or `false` | Never acts: saves evidence, then ends the run |
| `max_per_step`, `max_per_run` | integers, default 2 and 5 | Per-rule limits against a popup that keeps coming back |

Every `detect` key you give must match. At least one is required. `tap` and `alert` need a `target`. A rule that
retries the step needs an action that dismisses the popup, so `then: retry_step` with `action.type: none` is
refused. A `sensitive` rule ignores its `action` and `then`.

"Visible text" is the same text error detection reads: the `text`, `content-desc`, `label`, `value`, `hint` and
`name` attributes of the page source.

### A rule that sets every field

```json
[
  {
    "name": "Promotional offer",
    "priority": 30,
    "check": "on_failure",
    "detect": {
      "text_contains": ["Special offer", "Limited time"],
      "resource_id": "com.example.bank:id/promo_banner",
      "xpath": "//*[@resource-id='com.example.bank:id/promo_banner']"
    },
    "action": { "type": "tap", "target": "Not Now", "fallback": "com.example.bank:id/iv_close" },
    "then": "retry_step",
    "sensitive": false,
    "max_per_step": 2,
    "max_per_run": 5
  }
]
```

The file is plain JSON, so it cannot hold comments.

## What a popup does to a step

| Outcome | Step result | Code | Evidence kept |
|---------|-------------|------|---------------|
| Rule matched, popup gone, `retry_step` | Passes on the retry | none | Before and after screenshots |
| A limit was hit | Fails, the test case ends | `E0207` | One screenshot per occurrence |
| `fail_step`, or the popup stayed | Fails, the test case ends and the others run | `E0208` | Screenshot and page source |
| `stop_run`, or a `sensitive` rule | The run stops | `E0208` | Screenshot and page source, nothing pressed |
| A dialog is on screen and no rule covers it | Fails, the run continues, self-heal is skipped | `E0206` | Screenshot, page source and the first 200 characters of its text |
| Nothing on screen | Fails as before | `E0201` or `X0201` | Nothing new. Self-heal runs if it is on |

When a run stops, test cases that have not started are skipped. The suite teardown still runs, because it often
closes the app.

A step that fails with one of these codes is not retried with the next value of a `${variable}` locator list: the
same popup would be in the way of every value.

The popup check runs before AI self-heal. A matching rule costs nothing and does the same thing every run, while
self-heal costs an LLM call and can press the wrong button.

### How a target is found

A `tap` target is looked for in the page source optics has just read: an xpath, a resource-id, or the exact visible
text (ignoring case). When it is there, optics presses the centre of its bounds. This needs no other element source,
so it works with `appium_find_element` alone. Only when the page source does not hold the target does optics ask
the usual locators (text detection, image), which need their own sources enabled.

### System alerts on iOS

iOS keeps system alerts, such as a permission prompt, out of the app's page source. Optics reads them through the
driver instead and adds them to the screen as an `XCUIElementTypeAlert` with one `XCUIElementTypeButton` for each
button, so `text_contains` and `xpath` rules match them. Answer one with an `alert` action, which presses a button
by its label. A `tap` cannot, since these buttons have no bounds in the page source.

### Which dialogs count as a popup

For `E0206`, optics looks for what only a dialog has: on Android an `alertTitle`, `parentPanel`, `button1` or
`design_bottom_sheet` id, or a node from the system permission controller or package installer; on iOS an
`XCUIElementTypeAlert`, `Sheet` or `Popover`. A screen with none of these is treated as having no popup.

## Permissions

Permissions are a session setting, separate from the rules. Optics grants or revokes each one right after the
session starts, so the prompt never appears:

```yaml
permissions:
  camera: allow
  location: allow
  notifications: deny
```

The setting sits at the top of the config, not inside the Appium capabilities, which are sent to Appium whole.

- **Android.** Each permission is set with `mobile: changePermissions` (`pm grant` and `pm revoke`) for the
  `appPackage` capability. The names are `camera`, `microphone`, `location`, `background_location`, `notifications`,
  `contacts`, `calendar`, `phone`, `call_log`, `sms`, `storage`, `photos`, `videos`, `audio`, `bluetooth`, `nearby_wifi`,
  `body_sensors` and `activity_recognition`; any full `android.permission.*` name works too. Some stand for several
  permissions, and one the device's Android version does not have is skipped. An unknown name stops the start with `E0501`.
  A permission the app does not declare cannot be changed. That is logged and the others are still set.
- **iOS.** Nothing can be granted ahead of a prompt on a real device, so optics sets WebDriverAgent's
  `autoClickAlertSelector` once and the prompt is answered the moment it appears. See [iOS](#permissions-on-ios).

### Permissions on iOS

WebDriverAgent answers every system alert by pressing the button whose label the setting names. It cannot tell which
permission a prompt is for, so the names under `permissions:` do not matter on iOS, only the decision:

| Decision | Buttons pressed |
|----------|-----------------|
| `allow` | `Allow While Using App`, `Allow`, `OK`, `Allow Full Access` |
| `deny` | `Don’t Allow`, `Don't Allow`, `Keep Location Services Off` |

- Every permission must be `allow`, or every one `deny`. A mix stops the start with `E0501`. To answer two prompts
  differently, leave `permissions` out and use an `alert` rule for each.
- The setting applies to every system alert, not only permission prompts, so an `OK` on any other alert is pressed too.
- A prompt with no button in the table stays on screen. A popup rule can answer it, or the step fails with `E0206`.
- After a denial an app can raise a second alert that offers `Turn On in Settings` or `Keep Location Services Off`.
  The `deny` list holds the second, so the denial stays.
- The labels are English. Another language needs an `alert` rule.
- If the setting cannot be applied, optics logs a warning and the session carries on.

To answer one prompt on its own terms, use a rule whose action is `alert`:

```json
{
  "name": "Notifications prompt",
  "priority": 5,
  "detect": { "text_contains": ["to send you notifications"] },
  "action": { "type": "alert", "target": "Allow" }
}
```

Read this way, a rule written for `Allow While Using App` or `Allow Once` is matched on the alert's own text, for
example `"text_contains": ["use your location"]`.

A dialog that still appears, such as an OEM overlay, is caught by an ordinary text rule.

## Covering the usual popups

| Popup | Rule |
|-------|------|
| Promotional offer | `tap` "Not Now", with the close icon as `fallback` |
| Rate Us | `tap` "Maybe Later" |
| Session about to expire | `check: before_every_step`, `tap` "Continue Session". It does not wait for a failure, which a session timeout never causes |
| Network unavailable | `tap` "Retry" with `max_per_step: 3`. When the retries run out the step fails with `E0207` and the run continues |
| App update | Optional: `tap` "Later". Mandatory: a second rule with `action.type: none` and `then: stop_run`, with the lower `priority` number so it wins |
| Permission | `permissions`, plus a text rule for a dialog that still appears |
| Service unavailable | `tap` "OK" with `max_per_step: 2`, then the step fails with `E0207` |
| What's New | `tap` "Close" |
| A popup that keeps coming back | Not a rule: `max_per_step` and `max_per_run` end the loop |
| Unknown or sensitive | A `sensitive: true` rule for a known one. An unknown one fails the step with `E0206` and presses nothing |

## What a run records

Every inspection leaves one `popup` event. The popup, the action, the time and the result are all in it. See
[Event System](../architecture/event_system.md#popup-events) for the fields.

- Without a runner (the `Optics` class or Robot), nothing publishes the events. Read them with
  `Optics.get_popup_events()` (`Get Popup Events`), which returns one dict for each inspection, oldest first, and the
  counts with `Optics.get_popup_summary()` (`Get Popup Summary`). Reading does not clear them.
- One info log line for each handled, limited, stopped or unknown popup, with the rule, the action and the
  milliseconds it took.
- At debug level, one line for each rule tried that did not match.
- At the end of the run, a summary counts each rule's handled, limit and stopped verdicts and lists every unknown
  popup, so patterns show without reading every event.
- Screenshots and page sources are saved in the run's output folder as `popup_<n>_before.png`,
  `popup_<n>_after.png` and `popup_<n>_source.xml`. They need `save_captures` on. Screenshots also need a screenshot
  source such as `appium_screenshot` in `elements_sources`; `appium_find_element` cannot capture the screen, so
  without one a popup is still handled and the page source is still kept, but there are no images.

## What is not covered

- **Steps that do not locate an element.** Pressing by coordinates or percentage, swiping and scrolling skip the
  lookup, so neither check sees them. A popup on screen takes their tap or swipe. A popup that appears during a
  `Sleep` is caught at the next locating step.
- **Running a module as the action**, such as logging in again, and **suggesting rules after a run**.
- **Granting one iOS permission ahead of its prompt on a real device**, which Appium cannot do. The setting above
  answers the prompt when it appears.
