"""The apps of each desktop platform, and the project and Host helpers the desktop tests share."""

import json
import os
import re
import shutil
import subprocess  # nosec B404
import sysconfig
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml
from lxml import etree

APPIUM_URL = os.environ.get("OPTICS_E2E_XA11Y_URL", "")
PLATFORM = os.environ.get("OPTICS_E2E_XA11Y_PLATFORM", "Linux")
SCRIPTS = Path(sysconfig.get_path("scripts"))
OPTICS = shutil.which("optics", path=str(SCRIPTS))


@dataclass(frozen=True)
class Desktop:
    platform: str
    editor: str
    editor_name: str
    field: str
    calculator: str
    calculator_name: str
    key: str
    keys: dict
    select_all: str
    to_top: str
    find: str

    def key_of(self, label: str) -> str:
        return self.key.format(self.keys.get(label, label))

    def field_ref(self, screen: str) -> str:
        """The ref of the editor field in a compact screen listing."""
        row = re.search(rf"^\s*(e\d+) {self.field.removeprefix('//')}\b", screen, re.MULTILINE)
        assert row, screen
        return row.group(1)


DESKTOPS = {
    "Linux": Desktop("Linux", "mousepad", "mousepad", "//text_area", "gnome-calculator", "gnome-calculator",
                     '//button[@name="{}"]', {"clear": "C"}, "ctrl+a", "ctrl+Home", "ctrl+f"),
    "Mac": Desktop("Mac", "com.apple.TextEdit", "TextEdit", "//text_area", "com.apple.calculator", "Calculator",
                   '//button[@id="{}"]', {"clear": "AllClear", "7": "Seven", "8": "Eight", "5": "Five", "+": "Add",
                                          "=": "Equals"}, "cmd+a", "cmd+Up", "cmd+f"),
    "Windows": Desktop("Windows", "notepad.exe", "notepad", "//web_area", "calc.exe", "win32calc",
                       '//button[@id="{}"]', {"clear": "81", "7": "137", "8": "138", "5": "135", "+": "93", "=": "121"},
                       "ctrl+a", "ctrl+Home", "ctrl+f"),
}


# TextEdit restores the unsaved documents of its last run; with persistence off it shows only what the suite types.
_NO_RESTORE = ["-ApplePersistenceIgnoreState", "YES"]
_QUOTED_AUTOMATION = re.compile(r"""(["'])automationName\1:\s*(["'])Xa11y\2""")
_YAML_AUTOMATION = re.compile(r"^([ \t]*)automationName: Xa11y[ \t]*$", re.MULTILINE)


def fresh_start(platform: str = PLATFORM) -> dict:
    """The capabilities that start a Mac app without the documents of its last run."""
    return {"appArguments": _NO_RESTORE} if platform == "Mac" else {}


def started_fresh(text: str) -> str:
    """A documented config or call, with the capabilities of ``fresh_start`` after its automationName."""
    if PLATFORM != "Mac":
        return text
    text, quoted = _QUOTED_AUTOMATION.subn(
        lambda m: f"{m[0]}, {m[1]}appArguments{m[1]}: [{', '.join(m[2] + a + m[2] for a in _NO_RESTORE)}]", text)
    text, block = _YAML_AUTOMATION.subn(lambda m: f"{m[0]}\n{m[1]}appArguments: {json.dumps(_NO_RESTORE)}", text)
    assert quoted + block, f"No automationName to start the app fresh after in:\n{text}"
    return text


def driver_config(url: str, app: str, platform: str = PLATFORM) -> dict:
    """The config of a desktop project, as config.yaml holds it."""
    return {
        "driver_sources": [{"appium": {"enabled": True, "url": url, "capabilities": {
            "platformName": platform, "automationName": "Xa11y", "app": app, **fresh_start(platform)}}}],
        "elements_sources": [{"appium_find_element": {"enabled": True}}, {"appium_page_source": {"enabled": True}},
                             {"appium_screenshot": {"enabled": True}}],
    }


def write_project(folder: Path, config: dict, files: dict) -> str:
    """A project folder with config.yaml and the given files."""
    files = {"config.yaml": yaml.safe_dump(config, sort_keys=False), **files}
    for name, content in files.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return str(folder)


def run_optics(*args: str, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
    """The optics console script of this environment: optics.exe on Windows."""
    assert OPTICS, f"No optics console script in {SCRIPTS}"
    return subprocess.run([OPTICS, *args], capture_output=True, text=True, timeout=600, cwd=cwd, check=False)  # nosec B603


def eventually(check, timeout: float = 30) -> None:
    """Wait until the check holds: the Windows driver types raw keys and returns before the app has them all."""
    deadline = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < deadline, "the state did not come within the timeout"
        time.sleep(0.2)


def junit(folder: Path) -> str:
    return (folder / "execution_output" / "junit_output.xml").read_text(encoding="utf-8")


def runs(url: str, app: str) -> bool:
    """Whether the app runs: the driver attaches to it by name, or finds no app by that name."""
    caps = {"platformName": PLATFORM, "appium:automationName": "Xa11y", "appium:appName": app,
            "appium:launchTimeout": 500}
    body = json.dumps({"capabilities": {"alwaysMatch": caps}}).encode()
    request = urllib.request.Request(f"{url}/session", body, {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # nosec
            sid = json.load(response)["value"]["sessionId"]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            value = json.loads(raw or b"{}").get("value") or {}
        except ValueError:
            value = {"body": raw[:300].decode("utf-8", "replace")}
        if value.get("error") == "session not created" and "No element matched selector: application" in value.get(
                "message", ""):
            return False
        raise AssertionError(f"Attaching to {app} failed with HTTP {e.code}: {value}") from e
    urllib.request.urlopen(urllib.request.Request(f"{url}/session/{sid}", method="DELETE"), timeout=30).close()  # nosec
    return True


def scroll_position(page_source: str) -> float:
    """The value of the shown vertical scroll bar."""
    tree = etree.fromstring(page_source.encode())
    bar = next(b for b in tree.iter("scroll_bar") if float(b.get("height", 0)) > float(b.get("width", 0)))
    return float(bar.get("value"))


def centre(page_source: str, xpath: str) -> tuple[int, int]:
    """The screen point at the centre of the first element the xpath finds."""
    element = etree.fromstring(page_source.encode()).xpath(xpath)[0]
    x, y, width, height = (float(element.get(key)) for key in ("x", "y", "width", "height"))
    return round(x + width / 2), round(y + height / 2)
