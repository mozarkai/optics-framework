# Mobile Web (browsers and WebViews on Android and iOS)

Optics drives web pages on Android and iOS devices through the same Appium session that
drives native apps. One test can open an app, follow a link into Chrome or Safari, fill a
web form, and come back to the app, with no separate browser driver and no context
switching in your test steps. The same test steps work on both platforms.

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
`optics configure` enable it for Android and iOS projects, and the MCP server includes it
when it fills in the Appium sources for you.

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

UiAutomator2 driver 8.6.0 or later converts element positions exactly, including pinch
zoom. Older drivers work too: Optics computes the position from the page's
geometry and the on-screen bounds of the WebView. For an app that shows several
WebViews, use 8.6.0 or later, so each element is placed in the WebView that holds it.

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

Optics starts native iOS sessions with `includeSafariInWebviews: true`,
`additionalWebviewBundleIds: ["*"]`, `webviewConnectRetries: 1` and
`webviewAtomWaitTimeout: 5000` (values in your `capabilities` take precedence). They let
it see every app's web content and keep a lookup on a page that cannot answer to seconds
instead of minutes. Sessions that start in Safari (`browserName`) keep Appium's defaults.

Web positions on iOS are converted to screen points from a measurement of the page on
screen, so Safari's toolbars, page zoom and the keyboard are accounted for.

## Locators

| Locator | Example | Resolved by |
|---|---|---|
| Visible text | `Sign in` | native tree, then OCR |
| HTML id | `searchInput` | native tree (Chrome reports HTML ids as resource ids) |
| CSS | `css=input[name='q']`, `#searchInput` | `appium_web` |
| DOM XPath | `//button[@type='submit']` | native sources first, then `appium_web` (assertions skip the native sources for HTML-only paths) |

An XPath that names an Android or iOS class (`android.widget.Button`,
`XCUIElementTypeButton`) or a native attribute (`@resource-id`, `@content-desc`, `@text`,
`@bounds`, `@clickable` and the other UiAutomator2 and XCUITest attributes) is left to the
native sources. Text inside quotes does not count, so `//a[contains(@href,'android.com')]`
is still a web locator. An assertion leaves an XPath whose path names only HTML
elements (`//input[@name='email']`, `//button`) to `appium_web` alone; `//*` and other
XPaths are still checked natively too. A web locator that matches an element hidden by the
page (`display: none`, zero size) fails with "not displayed" instead of tapping blindly.
A web locator that matches nothing fails at once, without waiting.

`Assert Presence` with a web locator checks the DOM, so it passes for any element in the
page, including one scrolled off screen or hidden. `Assert Visibility` passes only for an
element that is displayed and inside the visible part of the page. An assertion checks
every source that can take its locator in turn, round after round, until it passes or the
timeout runs out, so the web check does not wait for the native sources to give up.

## Screen elements

`Get Interactive Elements`, `Get Screen Elements`, the MCP `elements` resource and the
natural-language mode of `optics live` list the web page next to the app's native
elements. With `appium_web` enabled, one call returns:

- the native elements (Chrome's toolbar, the app around a WebView), as before;
- every control and every piece of text of the web page, top to bottom, with screen-pixel
  bounds. A sentence with links or bold words inside is one row, and the links get
  their own rows; images, icons and canvases are listed too. Layout wrappers that hold
  nothing of their own are left out. An element
  outside the visible part of the page carries `offscreen: true`: scroll toward it, or
  press it by locator, which scrolls it into view first;
- a `loc` on each web element that the compact list reports (controls and visible
  text): a `css=` or `xpath=` locator that matches only that element. Put it in your
  elements file. The full list carries the same `loc` next to its `xpath`.

The page's on-screen elements appear once. UiAutomator2 and XCUITest also show them in
the native tree, without locators; the web entry replaces that copy.

`loc` is built to stay the same from run to run. It uses, in order, a test attribute
(`data-testid` and similar), a hand-written id, `name`, `placeholder`, `aria-label`,
`title` or `alt`, a link's `href`, the element's own text, and a class. When those are
shared, it names the element inside the card or row that holds it, for example
`xpath=//article[.//h2[normalize-space()='Red Mug']]//button[normalize-space()='Add to cart']`.
When nothing else is unique, it falls back to a CSS path. Generated ids and classes
(`r-7f3a91c2`, `nav-help-83412`) are skipped. Text with digits, such as "Cart (0)", is
matched by its leading words (`starts-with(normalize-space(), 'Cart (')`), so a count
or a price that changes does not change the locator. The Playwright page source builds
`loc` the same way.

The listing adds about half a second on a local emulator, mostly finding and entering
the page. On a screen with no web content, the cost is one check for web content.

## Moving between apps and the browser

Nothing in a test says which app is in front. Every lookup looks at the screen as it is
now, so a test follows the user's path from one app to another:

```csv
module_name,module_step,param_1,param_2
Read The Policy,Press Element,Google Play refund policy
Read The Policy,Assert Presence,css=h1,30
Read The Policy,Go Back
Read The Policy,Assert Presence,Google Play refund policy,20
```

- **App to browser.** Tapping a link in an app opens Chrome or a Chrome Custom Tab, the
  in-app browser most apps use. Web locators then find elements in the page in front, and
  native locators and OCR keep working. `Go Back` closes the tab and returns to the app.
- **Browser to app.** A web link that an app handles, such as a Play Store link, opens
  that app. Its native elements are available as soon as it is in front.
- **Choosing an app.** When more than one app can open a link, Android asks which one to
  use. Answer with `Detect And Press` on the app's name, then on `Just once`.
- **Other browsers.** Web locators read Chrome and WebViews with debugging enabled. If a
  link opens in another browser, such as Samsung Internet, they fail with "Cannot read
  the web page shown by …"; text locators and OCR work there. To keep a test in Chrome,
  make Chrome the device's default browser.
- **The same link twice.** Opening a link that an earlier tab still shows leaves two tabs
  with the same address. When a web locator has already looked at the earlier tab, Optics
  tells the two apart and uses the one on screen.
- **A Custom Tab right after another.** When an app opens a Custom Tab just after one
  closed, Chrome ignores the first tap on the new tab. Before typing into a field there,
  press the field once.

On iOS, a link from an app opens in Safari or an in-app Safari sheet, and an app can
show its own `WKWebView`. Web locators find elements in whichever of them is on screen.
When an app shows several web views at once, a web locator finds its element in the one
that holds it.
To return from Safari to the app, use `Launch App` with the app's bundle id.

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

Every app with WebView debugging on keeps a DevTools socket open, including apps in the
background, and Android freezes background apps so their sockets stop answering. Optics
keeps web lookups from waiting on those sockets:
it starts Android sessions with `ensureWebviewsHavePages: false`,
`enableWebviewDetailsCollection: false` and `extractChromeAndroidPackageFromContextName: true`
(values in your `capabilities` take precedence), and it remembers the web context of each app
instead of listing the device's contexts on every lookup.

## Limits

- Web locators search the top-level page only, not the inside of an `iframe`. Use text
  locators or OCR for content in a frame.
- When several Chrome tabs show the same address and Optics cannot tell them apart (three
  or more, or two it has not looked at), web locators fail with "Cannot tell which
  browser tab is on screen". Close the extra tabs.
- Each web lookup, and each element listing with web content in front, switches context
  twice and costs about one to two seconds on a remote device. Prefer native locators where they work, and use `css=` where they do not.
- iOS sign-in sheets (`ASWebAuthenticationSession`) cannot be inspected. Use text
  locators and OCR there.
