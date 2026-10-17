# Mobile Web (Chrome and WebViews on Android)

Optics drives web pages on a real Android device through the same Appium session that
drives native apps. One test can open an app, follow a link into Chrome, fill a web form,
and come back to the app, with no separate browser driver and no context switching in
your test steps.

## How it works

The Appium session stays in the native context the whole time. Native locators (visible
text, resource ids, native XPath) and OCR keep working on web pages, because Chrome puts
the page's text, links, inputs and buttons into the native UI tree.

Web locators, `css=...` and DOM XPath such as `//button[@type='submit']`, go through the
`appium_web` element source. For each lookup it:

1. finds the web content of the app in front (Chrome, or a debuggable WebView) and its
   front tab,
2. finds the element in the page and scrolls it into view,
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
geometry and the on-screen bounds of the WebView.

## Opening pages

`Launch App` and `Launch Other App` open a URL in the app that handles it, a browser
for web addresses:

```csv
module_name,module_step,param_1
Open Shop,Launch App,https://shop.example.com/
Back To The App,Launch App,com.example.shop
```

In an app session the URL opens in the device's default handler: the default browser,
or the app that owns the link. To open a web page in Chrome even when another browser is
the default, call the deep link with Chrome's package:

```csv
module_name,module_step,param_1
Open Shop In Chrome,Execute Script,"{""script"": ""mobile: deepLink"", ""args"": {""url"": ""https://shop.example.com/"", ""package"": ""com.android.chrome""}}"
```

To test only the browser, start the session in Chrome by adding `browserName: Chrome`
to the capabilities. Web addresses then open in that Chrome whatever the default browser
is, and Optics moves the session to the native context as soon as it starts, so every
keyword works the same way as in an app session.

## Locators

| Locator | Example | Resolved by |
|---|---|---|
| Visible text | `Sign in` | native tree, then OCR |
| HTML id | `searchInput` | native tree (Chrome reports HTML ids as resource ids) |
| CSS | `css=input[name='q']`, `#searchInput` | `appium_web` |
| DOM XPath | `//button[@type='submit']` | native sources first, then `appium_web` |

An XPath that names Android or iOS classes or attributes (`android.`,
`XCUIElementType`, `@resource-id`, `@content-desc`, `@text`, `@package`, `@bounds`) is
left to the native sources. A web locator that matches an element hidden by the page
(`display: none`, zero size) fails with "not displayed" instead of tapping blindly.

`Assert Presence` with a web locator checks the DOM, so it passes for any element in the
page, including one scrolled off screen or hidden. `Assert Visibility` passes only for an
element that is displayed and inside the visible part of the page.

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
- **Covered elements.** When a banner, a dialog or a panel lies over the element a web
  locator matches, Optics does not tap it, because the tap would land on whatever covers
  it. Dismiss the cover first. `Get Text` still reads a covered element.

On iOS, a link from an app opens in Safari. Text locators and OCR work there.

## Chrome's own pop-ups

A fresh Chrome shows native pop-ups over the page: a notification prompt, a sign-in
offer, or terms to accept. While one is open, nothing on the page can be tapped. Grant the
notification permission before opening Chrome so the prompt never shows:

```csv
module_name,module_step,param_1
Prepare Chrome,Launch App
Prepare Chrome,Execute Script,"{""script"": ""mobile: changePermissions"", ""args"": {""permissions"": ""android.permission.POST_NOTIFICATIONS"", ""appPackage"": ""com.android.chrome"", ""action"": ""grant""}}"
```

Dismiss any other pop-up with `Detect And Press` on its button id:
`negative_button` (notification prompt) or `signin_fre_dismiss_button` (sign-in offer).
Give it a timeout of 5 seconds or more, because the prompt appears after the page loads.

## WebViews in apps

An app's WebView is reachable only when the app enables WebView debugging
(`WebView.setWebContentsDebuggingEnabled(true)`), which debug builds usually do. Without
it, web locators fail with "No web content found in the foreground app". Native
locators and OCR still work on the WebView's content.

## Limits

- Web locators work on Android. On iOS, use text locators and OCR: Safari's page content
  is in the native UI tree there too.
- Web locators search the top-level page only, not the inside of an `iframe`. Use text
  locators or OCR for content in a frame.
- When several Chrome tabs show the same address and Optics cannot tell them apart (three
  or more, or two it has not looked at), web locators fail with "Cannot tell which
  browser tab is on screen". Close the extra tabs.
- Each web lookup switches context twice and costs about one to two seconds on a
  remote device. Prefer native locators where they work, and use `css=` where they do not.

The `mobile_web` sample (`optics init --template mobile_web`) runs a Wikipedia search in
Chrome with these steps.
