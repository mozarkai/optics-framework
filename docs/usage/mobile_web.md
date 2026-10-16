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
- When two Chrome tabs show the same URL, Optics cannot tell which one is on screen and
  web locators fail with "Cannot tell which browser tab is on screen". Close the extra
  tab, or open the page once.
- `Get Text` does not return text for a web locator. Use a text locator, or
  `Assert Presence`.
- Each web lookup switches context twice and costs about one to two seconds on a
  remote device. Prefer native locators where they work, and use `css=` where they do not.

The `mobile_web` sample (`optics init --template mobile_web`) runs a Wikipedia search in
Chrome with these steps.
