# TV Platforms

The Appium driver supports smart TV automation via platform-specific profiles. **Android TV is supported via the Android profile** (same as phones). LG webOS and Samsung Tizen require separate setup.

## Supported TV platforms

| Platform | Notes |
|----------|-------|
| **Android TV** | Supported via Android profile — same as phone automation |
| **Samsung Tizen** | D-pad navigation + DOM access (limited) |
| **LG webOS** | D-pad navigation only (blind remote control) |

Both Tizen and webOS use D-pad buttons (UP/DOWN/ENTER/BACK) for navigation, not touch. Touch-based methods (`click_element`, `swipe`, `enter_text`) raise an error on TVs.

## Samsung Tizen setup

Prerequisites:
- **Tizen Studio** installed; `TIZEN_HOME` set
- TV in Developer Mode (Settings → enter 12345 via 123 button → Client IP = your Appium host)
- `sdb connect <tv-ip>` and verified with `sdb devices`
- **Appium 2.x** with driver: `appium driver install --source=npm appium-tizen-tv-driver`
- RC token: `appium driver run tizentv pair-remote --host <tv-ip>` (approve on-screen prompt)
- Debug-signed `.wgt` app binary
- Matching chromedriver version for the TV's Chromium

Example config:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: TizenTV
        appium:automationName: TizenTV
        appium:deviceAddress: 192.168.1.50
        appium:rcToken: "14184553"
        appium:appPackage: com.example.app
        appium:app: /path/to/app.wgt
        appium:chromedriverExecutableDir: /path/to/chromedrivers

elements_sources:
  - appium_page_source:
      enabled: true
  - appium_screenshot:
      enabled: true
```

## LG webOS setup

Prerequisites:
- **webOS SDK** installed (`ares-cli` on PATH)
- TV in Developer Mode (Dev Mode app → signed in → ON; expires every ~50 hours)
- `ares-setup-device -n <name> -i <ip>` to register the TV
- **Appium 2.x** with driver: `appium driver install --source=npm appium-lg-webos-driver`
- RC pairing happens automatically on first session (approve on-screen)
- Matching chromedriver version

Example config:

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: LGTV
        appium:automationName: webOS
        appium:deviceHost: 192.168.1.50
        appium:appId: com.example.app
        appium:rcMode: rc
        appium:useSecureWebsocket: true
        appium:newCommandTimeout: 600

elements_sources:
  - appium_screenshot:
      enabled: true
```

## TV-specific limitations

**Methods that don't work on TVs** — raise an error if called:
- Touch: `click_element`, `press_element`, `press_coordinates`, `tap_at_coordinates`, `swipe*`
- Keyboard: `enter_text`, `clear_text`
- Other: `scroll`, `get_text_element`, `press_xpath_using_coordinates`

**Methods that work everywhere**: `launch_app`, `press_keycode`, `execute_script`, `terminate`

**Text input on TVs**: Not supported. Use a sequence of `Press Keycode` steps for D-pad navigation through on-screen keyboards.

**Screenshots on Tizen**: Native screenshots can hang; Optics renders the DOM to canvas instead. DRM/video content appears black — judge playback from UI elements.

**Chromedriver version**: Must match the TV's Chromium exactly (e.g. webOS 6 → Chromium 79 → chromedriver 79.x). Mismatches cause "cannot connect to chrome" errors.

**Appium 2.x only**: webOS and Tizen drivers require Appium 2.x (no Appium 3 support yet).

## References

- **LG webOS**: [webostv.developer.lge.com](https://webostv.developer.lge.com/)
- **Samsung Tizen**: [Samsung Developer — Tizen TV](https://developer.samsung.com/smarttv)
- **Appium webOS driver**: [headspinio/appium-lg-webos-driver](https://github.com/headspinio/appium-lg-webos-driver)
- **Appium Tizen driver**: [headspinio/appium-tizen-tv-driver](https://github.com/headspinio/appium-tizen-tv-driver)
