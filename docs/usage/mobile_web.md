# Mobile Web (browsers and WebViews on Android and iOS)

Optics drives web pages on Android and iOS devices through the same Appium session that
drives native apps. One test can open an app, follow a link into Chrome or Safari, fill a
web form, and come back to the app, with no browser driver and no context switching in
your test steps.

## How it works

The Appium session stays in the native context the whole time. Native locators (visible
text, resource ids, native XPath) and OCR keep working on web pages, because Chrome and
Safari put the page's text, links, inputs and buttons into the native UI tree.

Web locators, `css=...` and DOM XPath such as `//button[@type='submit']`, go through the
`appium_web` element source. For each lookup it:

1. finds the web content of the app in front and its front tab: Chrome or a debuggable
   WebView on Android; Safari, an in-app Safari sheet (`SFSafariViewController`), an
   inspectable `WKWebView` or another inspectable browser on iOS,
2. finds the element in the page and scrolls it into view, closing the on-screen
   keyboard if it covers the element,
3. converts the element's position to screen coordinates, and
4. returns to the native context.

The press or text entry that follows is a native action at those coordinates.

## Configuration

Enable `appium_web` next to the other Appium sources. `optics quickstart` and
`optics configure` enable it for Android and iOS projects.

```yaml
driver_sources:
  - appium:
      enabled: true
      url: "http://localhost:4723"
      capabilities:
        platformName: Android
        automationName: UiAutomator2
        deviceName: emulator-5554

elements_sources:
  - appium_find_element:
      enabled: true
  - appium_page_source:
      enabled: true
  - appium_web:
      enabled: true
  - appium_screenshot:
      enabled: true
```

Web lookups use a chromedriver that matches the Chrome version on the device. Let
Appium download it by starting the server with
`appium server --allow-insecure uiautomator2:chromedriver_autodownload`, or point
`appium:chromedriverExecutableDir` at a folder of chromedriver binaries.

### iOS

The same `elements_sources` work with `platformName: iOS` and `automationName: XCUITest`.
No extra driver is needed; XCUITest reads web pages through Safari's Web Inspector.

- **Real devices:** turn on **Web Inspector** and **Remote Automation** in
  Settings > Apps > Safari > Advanced. Simulators need no setup.
- **Apps:** a `WKWebView` is readable when the app sets `isInspectable = true`, which
  apps built with the iOS 16.4 SDK or later need even in debug builds. Safari and in-app
  Safari sheets are always readable.
- **Other browsers:** Chrome, Firefox and other iOS browsers are read the same way when
  they allow inspection. Firefox's tabs are inspectable out of the box; Chrome needs
  Settings > Content Settings > Web Inspector.

Native iOS sessions start with `includeSafariInWebviews: true`,
`additionalWebviewBundleIds: ["*"]`, `webviewConnectRetries: 1` and
`webviewAtomWaitTimeout: 5000`, so Optics sees every app's web content and a page that
cannot answer costs seconds, not minutes. Values in your `capabilities` take precedence.

## Opening pages

`Launch App` and `Launch Other App` open a URL in the app that handles it, a browser
for web addresses:

```csv
module_name,module_step,param_1
Open Shop,Launch App,https://shop.example.com/
Back To The App,Launch App,com.example.shop
```

To open a web page in Chrome even when another browser is the default, call the deep link
with Chrome's package:

```csv
module_name,module_step,param_1
Open Shop In Chrome,Execute Script,"{""script"": ""mobile: deepLink"", ""args"": {""url"": ""https://shop.example.com/"", ""package"": ""com.android.chrome""}}"
```

To test only the browser on Android, add `browserName: Chrome` to the capabilities: the
session starts in Chrome, web addresses open there whatever the default browser is, and
every keyword works as in an app session.

## Locators

| Locator | Example | Resolved by |
|---|---|---|
| Visible text | `Sign in` | native tree, then OCR |
| HTML id | `searchInput` | native tree (Chrome reports HTML ids as resource ids) |
| CSS | `css=input[name='q']`, `#searchInput` | `appium_web` |
| DOM XPath | `//button[@type='submit']` | native sources first, then `appium_web` (assertions skip the native sources for HTML-only paths) |

An XPath that names an Android or iOS class (`android.widget.Button`,
`XCUIElementTypeButton`) or a native attribute (`@resource-id`, `@text`, `@bounds` and
the like) outside quotes is left to the native sources. A web locator that matches a
hidden element fails with "not displayed"; one that matches nothing fails at once.

`Assert Presence` with a web locator checks the DOM, so an element scrolled off screen or
hidden passes. `Assert Visibility` passes only for an element displayed in the visible part
of the page.

## Screen elements

`Get Interactive Elements`, `Get Screen Elements`, the MCP `elements` resource and the
natural-language mode of `optics live` list the web page next to the app's native
elements. With `appium_web` enabled, one call returns:

- the native elements (Chrome's toolbar, the app around a WebView);
- every control, piece of text and image of the web page, with screen bounds. An element
  outside the visible part of the page carries `offscreen: true`; pressing it by locator
  scrolls it into view;
- a `loc` on each web control and text: a `css=` or `xpath=` locator that matches only
  that element. Put it in your elements file.

A web entry replaces the copy of it that UiAutomator2 or XCUITest shows in the native tree.

`loc` stays the same from run to run. It prefers a test attribute (`data-testid`), an id,
a name or label, or the element's text; when those are shared, it names the element inside
its card or row, for example
`xpath=//article[.//h2[normalize-space()='Red Mug']]//button[normalize-space()='Add to cart']`,
and last a CSS path. It skips generated ids and classes, and matches "Cart (0)" by its
leading words, so a changing count does not change it.

## Moving between apps and the browser

Nothing in a test says which app is in front. Every lookup looks at the screen as it is
now, so a test follows the user's path from one app to another:

```csv
module_name,module_step,param_1,param_2
Read The Policy,Press Element,Google Play refund policy
Read The Policy,Assert Presence,css=h1,30
Read The Policy,Get Text,css=h1
Read The Policy,Go Back
Read The Policy,Assert Presence,Google Play refund policy,20
```

- **App to browser.** Tapping a link in an app opens Chrome or a Chrome Custom Tab, the
  in-app browser most apps use. Web locators then find elements in the page in front.
  `Go Back` closes the tab and returns to the app.
- **Browser to app.** A web link that an app handles, such as a Play Store link, opens
  that app. Its native elements are available as soon as it is in front.
- **Choosing an app.** When more than one app can open a link, Android asks which one to
  use. Answer with `Detect And Press` on the app's name, then on `Just once`.
- **Other browsers.** If a link opens in another browser, such as Samsung Internet, web
  locators fail with "Cannot read the web page shown by …"; text locators and OCR work
  there. To keep a test in Chrome, make Chrome the device's default browser.
- **A Custom Tab right after another.** When an app opens a Custom Tab just after one
  closed, Chrome ignores the first tap on the new tab. Before typing into a field there,
  press the field once.
- **Covered elements.** When a banner, a dialog or a panel lies over the element a web
  locator matches, Optics does not tap it, because the tap would land on whatever covers
  it. Dismiss the cover first. `Get Text` still reads a covered element.

On iOS, when several web views are on screen, web locators use the one that holds the
element. To return from Safari to the app, use `Launch App` with the app's bundle id.

## Chrome's own pop-ups

A fresh Chrome shows native pop-ups over the page: a notification prompt, a sign-in
offer, or terms to accept. While one is open, nothing on the page can be tapped. Grant the
notification permission before opening Chrome so the prompt never shows:

```csv
module_name,module_step,param_1
Prepare Chrome,Launch App
Prepare Chrome,Execute Script,"{""script"": ""mobile: changePermissions"", ""args"": {""permissions"": ""android.permission.POST_NOTIFICATIONS"", ""appPackage"": ""com.android.chrome"", ""action"": ""grant""}}"
```

Dismiss any other pop-up with `Detect And Press` on its text or button id:
`Stay signed out` or `signin_fre_dismiss_button` (sign-in offer), `No thanks` or
`negative_button` (notification prompt).
Give it a timeout of 5 seconds or more, because the prompt appears after the page loads.

## WebViews in apps

An app's WebView is reachable only when the app enables WebView debugging
(`WebView.setWebContentsDebuggingEnabled(true)`), which debug builds usually do. Without
it, web locators fail with "No web content found in the foreground app". Native
locators and OCR still work on the WebView's content.

Android sessions start with `ensureWebviewsHavePages: false` and
`extractChromeAndroidPackageFromContextName: true`, so a frozen background app with WebView
debugging on adds at most 2 s to a web lookup. Values in your `capabilities` take precedence.

## Limits

- Web locators search the top-level page only, not the inside of an `iframe`. Use text
  locators or OCR for content in a frame.
- When several Chrome tabs show the same address and Optics cannot tell which is on
  screen, web locators fail with "Cannot tell which browser tab is on screen". Close the
  extra tabs.
- A web lookup costs one to two seconds on a remote device. Prefer native locators where
  they work.
- iOS sign-in sheets (`ASWebAuthenticationSession`) cannot be inspected. Use text
  locators and OCR there.

The `mobile_web` sample (`optics init --template mobile_web`) runs a Wikipedia search in
Chrome with these steps.
