# TV Device Drivers (Appium platform profiles)

Optics supports smart TV automation through the Appium driver, which now spans **four device families** — phones and TVs — with a single honest driver powered by **platform profiles**.

## Supported platforms

| Platform | Type | Remote Delivery | DOM / Introspection | Capture |
|----------|------|----------|----------|----------|
| **Android** | Phone | Appium (UiAutomator2) | Full WebDriver | Native screenshot |
| **iOS** | Phone | Appium (XCUITest) | Full WebDriver | Native screenshot |
| **Samsung Tizen TV** | TV | WebSocket remote control + `tizen: pressKey` | App DOM (CDP) | `html2canvas` (DRM→black) |
| **LG webOS TV** | TV | WebSocket remote control + `webos: pressKey` | App DOM (inspector port) | Native (when available) |

TVs are **fundamentally different** from phones: they navigate via D-pad and named buttons (UP/DOWN/ENTER/BACK), not touch; they lack traditional accessibility trees; and their vendor-specific remote protocols (Samsung RC WebSocket, LG's SSAP) are the delivery mechanism for control, not Android UIAutomator or iOS XCUITest.

## How it works

The `Appium` driver (`engines/drivers/appium.py`) now contains all four platforms, selected by the `platformName` capability:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: Android  # or iOS, TizenTV, LGTV
        # ... rest of caps ...
```

No separate `webos.py` or `tizen.py` driver file. All platform-specific data lives in a **`PlatformProfile` registry** (`engines/drivers/appium_platforms.py`), so adding a new platform is **pure data, not code**.

### Platform profiles

Each profile declares:

- **Options class**: which Appium options object builds the session (UiAutomator2Options / XCUITestOptions / generic AppiumOptions for TVs).
- **App-id capability names**: where to put the app identifier — `appPackage` (Android), `bundleId` (iOS), `appId` (webOS), `appPackage` (Tizen).
- **Keycode strategy**: how to deliver a button press — `press_keycode(int)` for phones, `execute_script("<vendor>: pressKey", {...})` for TVs.
- **Remote command & key map** (TVs only): the vendor command string (`"tizen: pressKey"` / `"webos: pressKey"`) and the mapping from canonical names (UP/ENTER/BACK) to vendor key codes (KEY_UP / KEY_ENTER / KEY_RETURN / UP / ENTER).

**Adding a new remote-control TV** (e.g. Roku, Fire TV):

1. Create a `PlatformProfile` in `appium_platforms.py`:
   ```python
   register_profile(PlatformProfile(
       name="roku",
       label="Roku TV",
       options_factory=AppiumOptions,
       app_id_caps=("appId",),
       keycode_strategy=KEYCODE_RC_NAMED,
       rc_command="roku: pressKey",
       rc_key_map={
           "UP": "Up", "DOWN": "Down", "ENTER": "Select", "BACK": "Back",
       },
       default_options={"newCommandTimeout": 600, "noReset": True},
   ))
   ```

2. Add to config defaults in `Config.__init__` (`common/config_handler.py`):
   ```python
   self.driver_sources = [
       ...,
       {"roku": DependencyConfig(enabled=False, url=None, capabilities={})},
   ]
   ```

3. Create a sample in `samples/your_app_roku/` with docs.

4. Done — `launch_app`, `press_keycode`, `Assert Presence`, self-healing, events, live mode, MCP all work unchanged.

### Capability guards

Methods that only work on phones are guarded with the `@supported_on(*MOBILE)` decorator. Calling them on a TV raises a clear `OpticsError(E0105)` up front:

**Methods unsupported on TVs** (raise E0105 on Tizen/webOS):
- `click_element`, `press_element`, `press_coordinates`, `press_percentage_coordinates` (no touch)
- `enter_text`, `enter_text_element`, `enter_text_using_keyboard`, `clear_text`, `clear_text_element` (no on-screen keyboard)
- `tap_at_coordinates` (no tap)
- `swipe`, `swipe_percentage`, `swipe_element` (no swipe)
- `scroll` (TVs use `press_keycode` UP/DOWN for navigation)
- `get_text_element` (no element handle)
- `press_xpath_using_coordinates` (coordinates are phone-only)

**Methods that work everywhere**:
- `launch_app`, `launch_other_app`, `force_terminate_app`, `terminate`
- `press_keycode` (mapped per platform)
- `execute_script` (vendor-specific commands)
- `get_driver_session_id`

---

## Setup: external prerequisites

TVs require more setup than phones. You'll need:

### Samsung Tizen TV
- **Tizen Studio** installed; `TIZEN_HOME` environment variable set (so `sdb` is on PATH).
- TV in **Developer Mode**: Settings → enter `12345` (via the 123 button on the remote) → "Client IP" = your Appium host IP → toggle Developer Mode ON.
- **`sdb connect <tv-ip>`** run and verified with `sdb devices`.
- **Appium 2.x** server with the driver: `appium driver install --source=npm appium-tizen-tv-driver`.
- **RC token** from the pairing handshake: `appium driver run tizentv pair-remote --host <tv-ip>`. The TV will show an on-screen prompt the first time — approve with the physical remote. The token is returned and must be passed in `appium:rcToken`.
- **Debug-signed app (.wgt)** — the test app binary, installed at session creation via the `appium:app` capability.
- **Matching chromedriver** version for the TV's embedded Chromium (typically older; use `appium:chromedriverExecutableDir` for auto-download or `appium:chromedriverExecutable` for a specific binary).
- **Same subnet** as the TV — Samsung TVs don't allow cross-VLAN WebSocket RC.

### LG webOS TV
- **webOS SDK** installed (`ares-cli`); ensure `ares-setup-device`, `ares-launch` etc. are on PATH.
- TV in **Developer Mode**: the TV's "Dev Mode" app must be installed, signed in, and toggled ON. Mode expires every ~50 hours and is auto-renewed by Appium when a session starts (controlled by `appium:autoExtendDevMode`).
- **`ares-setup-device -n <name> -i <ip>`** run so the TV is registered and shows in `ares-launch --device-list`.
- **Appium 2.x** server with the driver: `appium driver install --source=npm appium-lg-webos-driver`.
- **RC pairing**: happens automatically on first session (TV shows an on-screen accept prompt; approve with the physical remote).
- **Matching chromedriver** version (usually even older than Tizen; use `appium:chromedriverExecutable`).
- **Network reachability**: the Appium host must be able to reach the TV via the device's configured IP (`appium:deviceHost`).

---

## Config examples

### LG webOS (blind D-pad navigation)
```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: LGTV
        appium:automationName: webOS
        appium:deviceName: MyWebOSTV
        appium:deviceHost: 192.168.1.50
        appium:appId: com.myapp.test
        appium:rcMode: rc                      # remote control mode (blind navigation)
        appium:useSecureWebsocket: true
        appium:noReset: true
        appium:newCommandTimeout: 600

elements_sources:
  - appium_screenshot:
      enabled: true    # for OCR/template fallback only; not used in pure D-pad flow
```

### Samsung Tizen (DOM-based with text assertions)
```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: TizenTV
        appium:automationName: TizenTV
        appium:deviceName: "192.168.1.50:26101"
        appium:deviceAddress: 192.168.1.50
        appium:chromedriverExecutableDir: /usr/local/chromedriver
        appium:rcMode: remote
        appium:rcToken: "14184553"             # from pair-remote
        appium:appPackage: com.myapp.test
        appium:app: /path/to/app.wgt
        appium:noReset: true
        appium:newCommandTimeout: 3600

elements_sources:
  - appium_page_source:                        # live DOM for Assert Presence
      enabled: true
  - appium_screenshot:
      enabled: true
```

---

## Caveats & known issues

### Screenshot behavior
- **Android/iOS**: native screenshot from the driver (reliable).
- **Tizen**: native screenshot hangs the device's compositor. Optics uses `html2canvas` to render the app DOM to a canvas and reads it out as PNG. **DRM/video content appears BLACK** — judge playback state from UI elements, not pixels.
- **webOS**: native screenshot usually works, but can be slow.

### Text input on TVs
`enter_text` is not supported (guarded with `@supported_on(*MOBILE)`). On-screen keyboard entry must be authored as an explicit sequence of `Press Keycode` steps (hunt-and-peck D-pad navigation through the keyboard UI).

### Chromedriver version fragility
Every webOS/Tizen version pins a specific Chromium version, and the embedded engine never updates. A webOS 6 TV runs Chromium 79 forever. You must have the *exact* matching chromedriver:
- webOS 6 (Chromium 79) → chromedriver 79.x
- Tizen 2024 (Chromium 120) → chromedriver 120.x
- Older TVs may need chromedriver 2.36 (a legacy line for Chromium <63)

Mismatched versions → cryptic "cannot connect to chrome at 127.0.0.1:XXXXX" errors.

### Key map coverage
The built-in `press_keycode` mappings cover D-pad (UP/DOWN/LEFT/RIGHT), navigation (ENTER/SELECT/BACK/HOME), and media (PLAY/PAUSE/STOP/REWIND/FF). For custom keys (e.g. channel_up on a TV with live-TV features), pass the vendor key directly (e.g. `press_keycode("KEY_CHUP"` for Tizen, which declares `rc_passthrough_unknown=False` and rejects unmapped names — or just add it to the profile's `rc_key_map`).

### Appium 2.x only
LG webOS and Samsung Tizen drivers require **Appium 2.x** (the drivers have no Appium 3 support as of Jan 2026). Appium 3 support is a future follow-up.

---

## Running the samples

```bash
# LG webOS
optics execute optics_framework/samples/disney_webos/
# (Edit config.yaml first: TV IP, app ID, chromedriver path, Appium URL)

# Samsung Tizen
optics execute optics_framework/samples/crunchyroll_tizen/
# (Edit config.yaml first: TV IP, rcToken, .wgt path, chromedriver dir, Appium URL)
```

See `docs/usage/execute_journey.md` for the execution model and how elements sources/strategies interact.

---

## Design notes for contributors

- **The `@supported_on` decorator** lets methods declare which platforms they support. Unsupported calls raise `E0105` up front instead of failing mid-execution. This is preferable to a "TV driver" that stubs half its interface with `NotImplementedError`.
- **Platform profiles are pure data**. The driver queries the active profile at runtime (from the live session capabilities) and delegates options class, app-id caps, and keycode delivery to the profile. Adding a new platform touches zero driver logic.
- **No separate TV driver file**. Two standalone drivers (`webos.py`, `tizen.py`) that both lied about `NAME="appium"` to reuse element sources would be a maintenance nightmare. One honest driver with pluggable profiles scales cleanly.

---

## References

- **LG webOS**: [webostv.developer.lge.com](https://webostv.developer.lge.com/) — Dev mode, app debugging, Web Inspector.
- **Samsung Tizen**: [Samsung Developer — Tizen TV](https://developer.samsung.com/smarttv) — SDK, SDB, remote control API.
- **Appium LG webOS driver**: [headspinio/appium-lg-webos-driver](https://github.com/headspinio/appium-lg-webos-driver)
- **Appium Samsung Tizen driver**: [headspinio/appium-tizen-tv-driver](https://github.com/headspinio/appium-tizen-tv-driver)
