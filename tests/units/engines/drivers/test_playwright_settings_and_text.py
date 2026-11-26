"""Playwright settings that stop a launch, and what reaches the page's keyboard and fields."""
import pytest

pytest.importorskip("playwright")

from optics_framework.common.error import Code, OpticsError  # noqa: E402
from optics_framework.common.utils import SpecialKey  # noqa: E402
from optics_framework.engines.drivers import playwright as pw_driver  # noqa: E402
from optics_framework.engines.drivers.playwright import Playwright  # noqa: E402


class PlaywrightProcess:
    """The Playwright process: it starts and stops, and launches a browser of any type that closes when asked."""

    def __init__(self):
        self.log = []

    async def start(self):
        self.log.append("start")
        return self

    async def stop(self):
        self.log.append("stop")

    def __getattr__(self, browser_type):
        process = self

        class Browser:
            async def close(self):
                process.log.append("close")

        class BrowserType:
            async def launch(self, **options):
                process.log.append(f"launch {browser_type}")
                return Browser()

        return BrowserType()


class Page:
    """A page whose keyboard and fields record what reaches them; every locator is a text field."""

    def __init__(self):
        self.log = []
        self.keyboard = self

    async def press(self, key):
        self.log.append(("press", key))

    async def type(self, text):
        self.log.append(("type", text))

    def locator(self, selector):
        page = self

        class Field:
            async def evaluate(self, script, arg=None):
                return True

            async def fill(self, text):
                page.log.append(("fill", selector, text))

        return Field()


@pytest.fixture
def process(monkeypatch):
    process = PlaywrightProcess()
    monkeypatch.setattr(pw_driver, "async_playwright", lambda: process)
    return process


def on_page():
    driver = Playwright({})
    driver.page = Page()
    return driver


def test_a_missing_storage_state_file_fails_the_launch_and_closes_the_browser(process, tmp_path):
    driver = Playwright({"capabilities": {"browser": "firefox", "storage_state": str(tmp_path / "missing.json")}})
    with pytest.raises(OpticsError) as caught:
        driver.launch_app()
    assert caught.value.code == Code.E0501
    assert process.log == ["start", "launch firefox", "close", "stop"]


def test_storage_state_is_refused_with_the_browsers_own_context(process):
    driver = Playwright({"url": "http://127.0.0.1:9222",
                         "capabilities": {"context": "existing", "storage_state": "auth.json"}})
    with pytest.raises(OpticsError) as caught:
        driver.launch_app()
    assert caught.value.code == Code.E0501
    assert process.log == []


def test_special_keys_are_pressed_not_typed():
    driver = on_page()
    driver.enter_text_using_keyboard(SpecialKey.ENTER)
    assert driver.page.log == [("press", "Enter")]


def test_secret_prefix_is_not_typed():
    driver = on_page()
    driver.enter_text_element("#password", "@:secret")
    assert driver.page.log == [("fill", "#password", "secret")]
