import base64
from types import SimpleNamespace

import pytest
from selenium.common.exceptions import WebDriverException

from optics_framework.common import utils
from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.elementsources.appium_screenshot import AppiumScreenshot
from tests.units.fakes import FakeTime

PNG = b"\x89PNG"


class Hub:
    """A webdriver whose screenshot replies come from ``replies``: base64 text, or an error to raise."""

    def __init__(self, *replies):
        self.replies = list(replies)

    def get_screenshot_as_base64(self):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def clock(monkeypatch):
    clock = FakeTime()
    monkeypatch.setattr(utils, "time", clock)
    return clock


@pytest.mark.parametrize("first", [
    pytest.param(WebDriverException("timeout"), id="a driver error"),
    pytest.param("iVBOR", id="a truncated reply"),
])
def test_one_failed_reply_is_retried(clock, first):
    assert AppiumScreenshot(Hub(first, base64.b64encode(PNG).decode())).capture_screenshot_bytes() == PNG
    assert clock.slept == [0.5]


def test_two_failed_replies_raise(clock):
    source = AppiumScreenshot(Hub(WebDriverException("boom"), WebDriverException("boom")))
    with pytest.raises(RuntimeError):
        source.capture_screenshot_bytes()
    assert clock.slept == [0.5]


def test_a_tv_that_cannot_take_a_screenshot():
    tv = SimpleNamespace(can_native_screenshot=lambda: False, driver=Hub())
    source = AppiumScreenshot(tv)
    with pytest.raises(OpticsError) as caught:
        source.capture_screenshot_bytes()
    assert caught.value.code == Code.E0303
