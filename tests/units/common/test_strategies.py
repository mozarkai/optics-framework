import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from selenium.common.exceptions import InvalidSessionIdException, StaleElementReferenceException, WebDriverException

from optics_framework.common import utils
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.strategies import (
    LocatorStrategy,
    StrategyManager,
    TextDetectionStrategy,
)
from optics_framework.engines.elementsources.appium_find_element import AppiumFindElement
from optics_framework.engines.elementsources.appium_screenshot import AppiumScreenshot

import optics_framework.engines.drivers.appium  # noqa: F401


@pytest.fixture
def mock_element_source():
    source = MagicMock()
    source.locate.return_value = None
    # Real, non-black numpy array: TextDetectionStrategy annotates it, and assertions reject a black screen.
    source.capture.return_value = np.full((100, 100, 3), 255, dtype=np.uint8)
    # Explicit assignment: MagicMock reserves bare `assert_*` attribute access for its own
    # assertion methods (raises AttributeError instead of auto-vivifying), so this must be
    # set directly for hasattr()/_is_method_implemented() to see it as implemented.
    source.assert_elements_visible = MagicMock()
    return source


@pytest.fixture
def mock_text_detection():
    td = MagicMock()
    td.find_element.return_value = (True, (50, 50), ((0, 0), (100, 20)))
    return td


@pytest.fixture
def strategy_manager(mock_element_source, mock_text_detection):
    fallback = InstanceFallback([mock_element_source])
    return StrategyManager(
        element_source=fallback,
        text_detection=mock_text_detection,
        image_detection=None,
    )


def _sm_with_source(source):
    """StrategyManager wrapping a single element source (screenshot path only)."""
    return StrategyManager(
        element_source=InstanceFallback([source]),
        text_detection=None,
        image_detection=None,
    )


class TestCaptureScreenshotBytes:

    def test_raises_optics_error_when_all_sources_fail(self):
        source = MagicMock()
        source.capture_screenshot_bytes.side_effect = RuntimeError("hub timeout")
        sm = _sm_with_source(source)
        with pytest.raises(OpticsError):
            sm.capture_screenshot_bytes()


class _FakeWD:
    """Stub webdriver (no ``.driver`` attr, so _require_driver returns it as-is)."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0

    def get_screenshot_as_base64(self):
        self.calls += 1
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def _no_backoff(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)


class TestAppiumScreenshotBytes:
    def test_raises_after_exhausted_retries(self, _no_backoff):
        drv = _FakeWD([WebDriverException("boom"), WebDriverException("boom")])
        with pytest.raises(RuntimeError):
            AppiumScreenshot(driver=drv).capture_screenshot_bytes()
        assert drv.calls == 2


class TestStrategyManagerAssertVisibility:
    def test_text_only_visibility_reaches_text_detection(self, strategy_manager):
        with patch.object(TextDetectionStrategy, "check_elements", return_value=([True], None)) as check:
            result, _, _ = strategy_manager.assert_visibility(["TEXT_ONLY:Submit"], "Text", timeout=1, rule="any")

        assert result is True
        assert check.call_args.args[:2] == (["Submit"], True)


class _PresenceOnlySource(ElementSourceInterface):
    """Mirrors AppiumPageSource: presence only, no assert_elements_visible."""

    def capture(self):
        raise NotImplementedError

    def get_interactive_elements(self, filter_config=None):
        raise NotImplementedError

    def locate(self, element, index=None):
        return "located"

    def assert_elements(self, elements, timeout=30, rule='any'):
        return True


def _sm(*sources, text_detection=None, image_detection=None):
    return StrategyManager(
        element_source=InstanceFallback(list(sources)),
        text_detection=text_detection,
        image_detection=image_detection,
    )


class TestTryStrategyLocateAoiFallthrough:
    def _stub_strategy(self, bbox):
        source = MagicMock()
        source.get_bbox_for_element.return_value = bbox
        # spec omits locate_with_aoi, so the manager takes the non-native-AOI path.
        strategy = MagicMock(spec=["supports", "can_assert", "locate", "element_source"])
        strategy.supports.return_value = True
        strategy.can_assert.return_value = True
        strategy.locate.return_value = MagicMock(name="webelement")
        strategy.element_source = source
        return strategy

    def _locate(self, bbox, aoi):
        strategy = self._stub_strategy(bbox)
        with patch.object(utils, "_window_size_from_source", return_value=(100, 100)):
            return _sm(MagicMock())._try_strategy_locate(
                strategy, "//div", "XPath", True, *aoi, index=0
            )

    def test_match_outside_aoi_is_skipped(self):
        # centre (5%,5%) outside the same AOI -> falls through (None)
        result = self._locate(((0, 0), (10, 10)), (25, 25, 50, 50))
        assert result is None


class TestImageDetectionStrategy:
    def test_yields_centre_coordinates(self):
        source = MagicMock()
        source.capture.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        image_detection = MagicMock()
        image_detection.find_element.return_value = (True, (50, 60), ((0, 0), (100, 20)))
        results = list(_sm(source, image_detection=image_detection).locate("button.png"))
        assert any(
            type(r.strategy).__name__ == "ImageDetectionStrategy" and r.value == (50, 60)
            for r in results
        )


class _MissingTextSource(_PresenceOnlySource):
    """A native source that never finds the element, and can capture (so OCR applies)."""

    def capture(self):
        return None

    def assert_elements(self, elements, timeout=30, rule='any'):
        raise OpticsError(Code.E0201, message="Timeout reached")


class TestAssertPresenceContract:
    def test_unconfigured_ocr_does_not_mask_the_miss(self):
        manager = _sm(_MissingTextSource())
        with pytest.raises(OpticsError) as exc_info:
            manager.assert_presence(["Submit"], "Text", timeout=0, rule="any")
        assert (exc_info.value.code, exc_info.value.summary) == (Code.E0201, "'Submit' is not on the screen.")
        assert "OCR is off" in exc_info.value.hint


class TestGetInteractiveElementsStrategyScreening:
    @staticmethod
    def _strategy(supports_extraction: bool, elements=None):
        source = MagicMock()
        if supports_extraction:
            source.get_interactive_elements = MagicMock(return_value=elements or [])
        else:
            def _unsupported(filter_config=None, compact=False):
                raise NotImplementedError("does not support getting interactive elements")
            source.get_interactive_elements = _unsupported
        strategy = MagicMock()
        strategy.element_source = source
        strategy.get_interactive_elements = MagicMock(
            side_effect=lambda fc=None, compact=False: source.get_interactive_elements(fc, compact=compact)
        )
        return strategy

    def test_no_capable_strategy_raises_rather_than_returning_empty(self, monkeypatch):
        monkeypatch.setattr(
            LocatorStrategy, "_is_method_implemented", staticmethod(lambda src, name: False)
        )
        manager = _sm(MagicMock())
        manager.pagesource_strategies = [self._strategy(False)]
        with pytest.raises(OpticsError) as exc_info:
            manager.get_interactive_elements()
        assert exc_info.value.code == Code.E0202


def _dead() -> InvalidSessionIdException:
    return InvalidSessionIdException("A session is either terminated or not started")


def _locate(manager):
    return list(manager.locate("//button[@id='search']"))


@pytest.mark.parametrize("method, error, call, code", [
    ("locate", _dead(), _locate, Code.E0106),
    ("locate", OpticsError(Code.E0201, message="Element of type XPath not found", cause=_dead()), _locate, Code.E0106),
    ("check_elements", _dead(), lambda m: m.assert_presence(["text=Search"], "Text", timeout=10, rule="any"), Code.E0106),
    ("get_page_source", _dead(), lambda m: m.capture_pagesource(), Code.E0106),
    ("capture", _dead(), lambda m: m.capture_screenshot(), Code.E0106),
    ("capture_screenshot_bytes", _dead(), lambda m: m.capture_screenshot_bytes(), Code.E0106),
    ("get_interactive_elements", _dead(), lambda m: m.get_interactive_elements(), Code.E0106),
    ("locate", WebDriverException("stale element reference"), _locate, Code.E0201),
], ids=["locate", "locate wrapped", "assert", "page source", "screenshot", "screenshot bytes", "screen elements", "ordinary failure"])
def test_a_dead_session_is_not_masked_as_a_miss(monkeypatch, method, error, call, code):
    if method == "get_interactive_elements":
        monkeypatch.setattr(LocatorStrategy, "_is_method_implemented", staticmethod(lambda src, name: True))
    source = MagicMock()
    setattr(source, method, MagicMock(side_effect=error))
    with pytest.raises(OpticsError) as exc_info:
        call(_sm(source))
    assert exc_info.value.code == code


class _StaleAfterFind:
    """A handle the driver gives out but can no longer resolve, as xa11y does inside an AppKit table."""

    @property
    def rect(self):
        raise StaleElementReferenceException("The element is no longer in the tree")


class _TableWebDriver:
    def native_ui_platform(self):
        return None

    def find_element(self, by, value):
        return _StaleAfterFind()


class _Screen(ElementSourceInterface):
    def capture(self):
        return np.full((100, 100, 3), 255, dtype=np.uint8)

    def get_interactive_elements(self, filter_config=None):
        return []

    def locate(self, element, index=None):
        return None

    def assert_elements(self, elements, timeout=30, rule='any'):
        raise TimeoutError(elements)

    def check_elements(self, elements, visible=False):
        return [False] * len(elements)


class TestEvidenceNeverDecidesTheVerdict:
    def test_a_found_element_passes_when_its_bounds_cannot_be_read(self):
        manager = _sm(AppiumFindElement(_TableWebDriver()), _Screen())
        passed, _, frame = manager.assert_presence(["//button[@name='% CPU']"], "XPath", timeout=1, rule="any")
        assert passed is True
        assert frame is not None
