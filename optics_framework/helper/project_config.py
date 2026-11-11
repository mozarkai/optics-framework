"""Interactive builder for a project-specific ``config.yaml``.

Asks the beginner only about the platform they picked (a Selenium user never
sees an Appium question), then renders a fully commented config file that keeps
the explanatory comments of the ``_STARTER_CONFIG`` template in
``initialize.py`` — which is why the output is built as text rather than
serialised from a model with ``yaml.dump``.

Answers schema (normalised):
  platform:       "android" | "ios" | "desktop" | "web-playwright" | "web-selenium"
  android:        device_name, platform_name, app_package, app_activity,
                  appium_url
  ios:            device_name, platform_name, bundle_id, appium_url
  desktop:        platform_name (Mac | Linux | Windows), app, appium_url
  web-selenium:   selenium_url
  web-playwright: browser, headless (bool)
  common:         ocr (bool), log_level (str, default "INFO")
"""
import os
import sys
from pathlib import Path

import yaml
from rich.prompt import Confirm, Prompt

from optics_framework.common import safe_fs
from optics_framework.common.config_handler import load_project_yaml
from optics_framework.helper.onboarding import blank_line

# Every platform maps to exactly ONE action driver; that driver owns the
# matching ``<driver>_*`` elements sources.
_DRIVER_BY_PLATFORM = {
    "android": "appium",
    "ios": "appium",
    "desktop": "appium",
    "web-selenium": "selenium",
    "web-playwright": "playwright",
}

_ELEMENTS_SOURCES_BY_DRIVER = {
    "appium": ["appium_find_element", "appium_page_source", "appium_web", "appium_screenshot"],
    "selenium": ["selenium_find_element", "selenium_page_source", "selenium_screenshot"],
    "playwright": ["playwright_find_element", "playwright_page_source", "playwright_screenshot"],
}

_SOURCE_GROUP_TITLES = {
    "appium": "Appium locators / page source / web pages / screenshots:",
    "selenium": "Selenium locators / page source / screenshots:",
    "playwright": "Playwright locators / page source / screenshots:",
}

_PLATFORMS_BY_DOMAIN = {
    "mobile": (["android", "ios"], "android"),
    "web": (["web-playwright", "web-selenium"], "web-playwright"),
    "desktop": (["desktop"], "desktop"),
}

_APPIUM_URL = "http://127.0.0.1:4723"
_DESKTOP_APPS = {"Mac": "com.apple.TextEdit", "Linux": "gedit", "Windows": "notepad.exe"}


def _this_desktop() -> str:
    return {"darwin": "Mac", "win32": "Windows"}.get(sys.platform, "Linux")


def _quote(value: object) -> str:
    """Render a value as a double-quoted YAML scalar (embedded quotes folded
    to singles so the generated file always stays parseable)."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', "'") + '"'


def _enabled(enabled: bool) -> str:
    return "true" if enabled else "false"


def _ask(question: str, **kwargs) -> str:
    blank_line()
    return Prompt.ask(question, **kwargs)


def _confirm(question: str, **kwargs) -> bool:
    blank_line()
    return Confirm.ask(question, **kwargs)


def prompt_project_config(domain: str | None = None) -> dict:
    """Ask the beginner about their target, one platform at a time.

    When ``domain`` is given (quickstart already knows whether the user picked
    mobile or web), the platform question only offers that domain's platforms —
    so a mobile user can never pick an engine they didn't install.
    ``domain=None`` (standalone ``optics configure``) offers all four.

    Question order (stable — callers' tests rely on it): platform, then only
    that platform's fields, then the shared OCR / log-level questions."""
    choices, default = _PLATFORMS_BY_DOMAIN.get(domain or "", (list(_DRIVER_BY_PLATFORM), "android"))
    platform = choices[0] if len(choices) == 1 else _ask(
        "What are you automating?",
        choices=choices,
        default=default,
    )
    answers: dict = {"platform": platform}
    if platform == "desktop":
        answers.update(_desktop_answers())
    elif platform == "android":
        answers["device_name"] = _ask(
            "Device or emulator name (see `adb devices`)",
            default="emulator-5554")
        answers["platform_name"] = _ask(
            "Platform name", default="Android")
        answers["app_package"] = _ask(
            "App package id (e.g. com.example.app)", default="com.example.app")
        answers["app_activity"] = _ask(
            "App main activity (e.g. com.example.app.MainActivity)",
            default="com.example.app.MainActivity")
        answers["appium_url"] = _ask_appium_url()
    elif platform == "ios":
        # iOS launches apps by bundle identifier — appPackage/appActivity are
        # Android-only capabilities and would be ignored (or rejected) by
        # XCUITest.
        answers["device_name"] = _ask(
            "Device or simulator name", default="iPhone 15")
        answers["platform_name"] = _ask(
            "Platform name", default="iOS")
        answers["bundle_id"] = _ask(
            "Bundle identifier (e.g. com.example.app)", default="com.example.app")
        answers["appium_url"] = _ask_appium_url()
    elif platform == "web-selenium":
        answers["selenium_url"] = _ask(
            "Selenium/WebDriver server URL", default="http://127.0.0.1:4444/wd/hub")
    else:  # web-playwright
        answers["browser"] = _ask(
            "Which browser?", choices=["chromium", "firefox", "webkit"],
            default="chromium")
        answers["headless"] = _confirm(
            "Run headless (no browser window)?", default=False)
    answers["ocr"] = _confirm(
        "Also find elements by on-screen text (EasyOCR)? Useful when there "
        "are no stable locators.", default=False)
    answers["log_level"] = _ask(
        "Log level", choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO")
    return answers


def _desktop_answers() -> dict:
    platform_name = _ask("Which desktop?", choices=list(_DESKTOP_APPS), default=_this_desktop())
    return {
        "platform_name": platform_name,
        "app": _ask("App to launch: a bundle id or .app path on macOS, an executable elsewhere",
                    default=_DESKTOP_APPS[platform_name]),
        "appium_url": _ask_appium_url(),
    }


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
        "# Full reference: https://optics-framework.org/docs/reference/config?ref=config",
        "",
    ]


def _appium_block(answers: dict, enabled: bool) -> list[str]:
    platform = answers.get("platform", "android")
    return [
        *(_DESKTOP_COMMENT if platform == "desktop" else _MOBILE_COMMENT),
        "  - appium:",
        f"      enabled: {_enabled(enabled)}",
        f"      url: {_quote(answers.get('appium_url') or _APPIUM_URL)}",
        "      capabilities:",
        *_CAPABILITIES.get(platform, _android_capabilities)(answers),
    ]


def _android_capabilities(answers: dict) -> list[str]:
    return [
        f"        appPackage: {_quote(answers.get('app_package') or 'com.example.app')}",
        "        appActivity: "
        + _quote(answers.get("app_activity") or "com.example.app.MainActivity"),
        "        automationName: UiAutomator2",
        f"        deviceName: {_quote(answers.get('device_name') or 'emulator-5554')}",
        f"        platformName: {_quote(answers.get('platform_name') or 'Android')}",
    ]


def _ios_capabilities(answers: dict) -> list[str]:
    """XCUITest launches apps by bundleId; appPackage/appActivity are Android-only and must not appear here."""
    return [
        f"        bundleId: {_quote(answers.get('bundle_id') or 'com.example.app')}",
        "        automationName: XCUITest",
        f"        deviceName: {_quote(answers.get('device_name') or 'iPhone 15')}",
        f"        platformName: {_quote(answers.get('platform_name') or 'iOS')}",
    ]


def _ask_appium_url() -> str:
    return _ask("Appium server URL", default=_APPIUM_URL)


_MOBILE_COMMENT = [
    "  # Native Android/iOS via Appium. Needs a running Appium server and a",
    "  # connected device/emulator.  Install:  optics setup --install appium",
]

_DESKTOP_COMMENT = [
    "  # Desktop apps via Appium and appium-xa11y-driver. Needs a running Appium server.",
    "  # Install:  optics setup --install appium",
    "  #           appium driver install --source=npm appium-xa11y-driver",
]


def _desktop_capabilities(answers: dict) -> list[str]:
    platform_name = answers.get("platform_name") or _this_desktop()
    return [
        f"        platformName: {_quote(platform_name)}",
        "        automationName: Xa11y",
        f"        app: {_quote(answers.get('app') or _DESKTOP_APPS.get(platform_name, 'gedit'))}",
    ]


_CAPABILITIES = {"ios": _ios_capabilities, "desktop": _desktop_capabilities}


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
    """Write ``text`` with the project's ``name`` to ``<folder>/config.yaml``
    (creating ``folder`` if needed) and return the written path.

    The name is the one the replaced config.yaml held, else the folder's name,
    so the report keeps it wherever the project is unpacked. Overwrites
    unconditionally — the caller owns any confirm-before-overwrite UX (see
    ``quickstart.py``, which asks before clobbering a template)."""
    path = os.path.join(folder, "config.yaml")
    safe_fs.write_text(path, _with_name(text, _project_name(path)))
    return path


def _project_name(config_path: str) -> str:
    """The ``name`` config.yaml holds as written, else its folder's name with ``$`` kept literal."""
    try:
        saved = load_project_yaml(config_path)
    except (OSError, yaml.YAMLError):
        saved = None
    name = saved.get("name") if isinstance(saved, dict) else None
    if isinstance(name, str) and name:
        return name
    return Path(config_path).parent.resolve().name.replace("$", "$$")


def _with_name(text: str, name: str) -> str:
    """Put a ``name:`` line after the config's leading comment block."""
    lines = text.splitlines(keepends=True)
    after_header = next(
        (i for i, line in enumerate(lines) if line.strip() and not line.startswith("#")), len(lines))
    lines.insert(after_header, yaml.safe_dump({"name": name}) + "\n")
    return "".join(lines)
