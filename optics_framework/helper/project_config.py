"""Interactive builder for a project-specific ``config.yaml``.

Asks the beginner only about the platform they picked (a Selenium user never
sees an Appium question), then renders a fully commented config file that keeps
the explanatory comments of the ``_STARTER_CONFIG`` template in
``initialize.py`` — which is why the output is built as text rather than
serialised from a model with ``yaml.dump``.

Answers schema (normalised):
  platform:       "android" | "ios" | "web-playwright" | "web-selenium"
  android:        device_name, platform_name, app_package, app_activity,
                  appium_url
  ios:            device_name, platform_name, bundle_id, appium_url
  web-selenium:   selenium_url
  web-playwright: browser, headless (bool)
  common:         ocr (bool), log_level (str, default "INFO")
"""
import os

from rich.prompt import Confirm, Prompt

# Every platform maps to exactly ONE action driver; that driver owns the
# matching ``<driver>_*`` elements sources.
_DRIVER_BY_PLATFORM = {
    "android": "appium",
    "ios": "appium",
    "web-selenium": "selenium",
    "web-playwright": "playwright",
}

_ELEMENTS_SOURCES_BY_DRIVER = {
    "appium": ["appium_find_element", "appium_page_source", "appium_screenshot"],
    "selenium": ["selenium_find_element", "selenium_page_source", "selenium_screenshot"],
    "playwright": ["playwright_find_element", "playwright_page_source", "playwright_screenshot"],
}

_SOURCE_GROUP_TITLES = {
    "appium": "Appium locators / page source / screenshots:",
    "selenium": "Selenium locators / page source / screenshots:",
    "playwright": "Playwright locators / page source / screenshots:",
}

_AUTOMATION_NAME = {"android": "UiAutomator2", "ios": "XCUITest"}


def _quote(value: object) -> str:
    """Render a value as a double-quoted YAML scalar (embedded quotes folded
    to singles so the generated file always stays parseable)."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', "'") + '"'


def _enabled(enabled: bool) -> str:
    return "true" if enabled else "false"


def prompt_project_config(domain: str | None = None) -> dict:
    """Ask the beginner about their target, one platform at a time.

    When ``domain`` is given (quickstart already knows whether the user picked
    mobile or web), the platform question only offers that domain's platforms —
    so a mobile user can never pick an engine they didn't install.
    ``domain=None`` (standalone ``optics configure``) offers all four.

    Question order (stable — callers' tests rely on it): platform, then only
    that platform's fields, then the shared OCR / log-level questions."""
    if domain == "mobile":
        choices, default = ["android", "ios"], "android"
    elif domain == "web":
        choices, default = ["web-playwright", "web-selenium"], "web-playwright"
    else:
        choices, default = list(_DRIVER_BY_PLATFORM), "android"
    platform = Prompt.ask(
        "What are you automating?",
        choices=choices,
        default=default,
    )
    answers: dict = {"platform": platform}
    if platform == "android":
        answers["device_name"] = Prompt.ask(
            "Device or emulator name (see `adb devices`)",
            default="emulator-5554")
        answers["platform_name"] = Prompt.ask(
            "Platform name", default="Android")
        answers["app_package"] = Prompt.ask(
            "App package id (e.g. com.example.app)", default="com.example.app")
        answers["app_activity"] = Prompt.ask(
            "App main activity (e.g. com.example.app.MainActivity)",
            default="com.example.app.MainActivity")
        answers["appium_url"] = Prompt.ask(
            "Appium server URL", default="http://127.0.0.1:4723")
    elif platform == "ios":
        # iOS launches apps by bundle identifier — appPackage/appActivity are
        # Android-only capabilities and would be ignored (or rejected) by
        # XCUITest.
        answers["device_name"] = Prompt.ask(
            "Device or simulator name", default="iPhone 15")
        answers["platform_name"] = Prompt.ask(
            "Platform name", default="iOS")
        answers["bundle_id"] = Prompt.ask(
            "Bundle identifier (e.g. com.example.app)", default="com.example.app")
        answers["appium_url"] = Prompt.ask(
            "Appium server URL", default="http://127.0.0.1:4723")
    elif platform == "web-selenium":
        answers["selenium_url"] = Prompt.ask(
            "Selenium/WebDriver server URL", default="http://127.0.0.1:4444/wd/hub")
    else:  # web-playwright
        answers["browser"] = Prompt.ask(
            "Which browser?", choices=["chromium", "firefox", "webkit"],
            default="chromium")
        answers["headless"] = Confirm.ask(
            "Run headless (no browser window)?", default=False)
    answers["ocr"] = Confirm.ask(
        "Also find elements by on-screen text (EasyOCR)? Useful when there "
        "are no stable locators.", default=False)
    answers["log_level"] = Prompt.ask(
        "Log level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO")
    return answers


def _header(platform: str) -> list[str]:
    return [
        "# Optics Framework project configuration.",
        f"# Generated for the {platform} platform by optics.",
        "#",
        "# Getting started:",
        "#   1. Your driver is already enabled below; replace anything that",
        "#      still shows an example value.",
        "#   2. Install its packages if you haven't, e.g.",
        "#      optics setup --install appium",
        "#   3. Validate everything with  optics doctor <this-folder>",
        "#",
        "# Full reference: https://mozarkai.github.io/optics-framework/configuration/",
        "",
    ]


def _appium_block(answers: dict, enabled: bool) -> list[str]:
    platform = answers.get("platform", "android")
    if platform not in _DRIVER_BY_PLATFORM:
        platform = "android"
    lines = [
        "  # Native Android/iOS via Appium. Needs a running Appium server and a",
        "  # connected device/emulator.  Install:  optics setup --install appium",
        "  - appium:",
        f"      enabled: {_enabled(enabled)}",
        f"      url: {_quote(answers.get('appium_url') or 'http://127.0.0.1:4723')}",
        "      capabilities:",
    ]
    if platform == "ios":
        # XCUITest launches apps by bundleId; appPackage/appActivity are
        # Android-only and must not appear in an iOS config.
        lines += [
            f"        bundleId: {_quote(answers.get('bundle_id') or 'com.example.app')}",
            "        automationName: "
            + _AUTOMATION_NAME.get(platform, "UiAutomator2"),
            f"        deviceName: {_quote(answers.get('device_name') or 'iPhone 15')}",
            f"        platformName: {_quote(answers.get('platform_name') or 'iOS')}",
        ]
    else:
        lines += [
            f"        appPackage: {_quote(answers.get('app_package') or 'com.example.app')}",
            "        appActivity: "
            + _quote(answers.get("app_activity") or "com.example.app.MainActivity"),
            "        automationName: "
            + _AUTOMATION_NAME.get(platform, "UiAutomator2"),
            f"        deviceName: {_quote(answers.get('device_name') or 'emulator-5554')}",
            f"        platformName: {_quote(answers.get('platform_name') or 'Android')}",
        ]
    return lines


def _driver_sources_section(answers: dict, driver: str) -> list[str]:
    lines = [
        "driver_sources:",
        *_appium_block(answers, enabled=driver == "appium"),
        "  # Web via a Selenium/WebDriver server.  Install:  optics setup --install selenium",
        "  - selenium:",
        f"      enabled: {_enabled(driver == 'selenium')}",
        "      url: "
        + _quote(answers.get("selenium_url") or "http://localhost:4444/wd/hub"),
        "      capabilities: {}",
        "  # Web via Playwright (no external server).  Install:  optics setup --install playwright",
        "  - playwright:",
        f"      enabled: {_enabled(driver == 'playwright')}",
        "      capabilities:",
        f"        browser: {answers.get('browser') or 'chromium'}",
        f"        headless: {_enabled(bool(answers.get('headless', False)))}",
        "",
    ]
    return lines


def _elements_sources_section(driver: str) -> list[str]:
    wanted = _ELEMENTS_SOURCES_BY_DRIVER[driver]
    lines = ["elements_sources:"]
    for group, sources in _ELEMENTS_SOURCES_BY_DRIVER.items():
        lines.append(f"  # {_SOURCE_GROUP_TITLES[group]}")
        for source in sources:
            lines.append(f"  - {source}:")
            lines.append(f"      enabled: {_enabled(source in wanted)}")
        lines.append("")
    return lines


def _vision_sections(answers: dict) -> list[str]:
    return [
        "# Optional vision fallbacks: locate elements by on-screen text (OCR) or image.",
        "text_detection:",
        "  - easyocr:            # install: optics setup --install easyocr",
        f"      enabled: {_enabled(bool(answers.get('ocr', False)))}",
        "image_detection:",
        "  - templatematch:",
        "      enabled: false",
        "",
    ]


def render_project_config(answers: dict) -> str:
    """Render the commented ``config.yaml`` TEXT for the given answers.

    Pure function — no prompts, no filesystem. Missing keys fall back to the
    same example values the interactive flow suggests. Exactly one driver plus
    its matching elements sources end up ``enabled: true`` (plus EasyOCR under
    ``text_detection`` when ``answers["ocr"]``); every other entry stays listed
    but disabled so the file doubles as a reference."""
    platform = answers.get("platform")
    if platform not in _DRIVER_BY_PLATFORM:
        platform = "android"
    driver = _DRIVER_BY_PLATFORM[platform]

    sections: list[str] = [
        *_header(platform),
        *_driver_sources_section(answers, driver),
        *_elements_sources_section(driver),
        *_vision_sections(answers),
        f"log_level: {answers.get('log_level') or 'INFO'}",
        "json_log: true",
        "file_log: true",
    ]
    return "\n".join(sections) + "\n"


def write_project_config(folder: str, text: str) -> str:
    """Write ``text`` to ``<folder>/config.yaml`` (creating ``folder`` if
    needed) and return the written path.

    Overwrites unconditionally — the caller owns any confirm-before-overwrite
    UX (see ``quickstart.py``, which asks before clobbering a template)."""
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "config.yaml")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path
