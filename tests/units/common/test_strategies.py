"""Unit tests for the element-location layer (common/strategies.py).

Covers the TEXT_ONLY prefix + strategy selection, the real strategy classes driven
through the public ``locate`` / ``assert_presence`` API (XPath, Text, Image
detection), the not-found (E0201) and invalid-rule (E0205) contracts, the
assert_presence time-allocation math, and the native screenshot-bytes fast path.
"""
import base64
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
from selenium.common.exceptions import WebDriverException

from optics_framework.common import utils
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.strategies import (
    StrategyManager,
    TextDetectionStrategy,
    LocateResult,
)
from optics_framework.engines.elementsources.appium_screenshot import AppiumScreenshot
from optics_framework.engines.elementsources.selenium_screenshot import SeleniumScreenshot


# --- parse_text_only_prefix and determine_element_type (utils) ---


class TestParseTextOnlyPrefix:
    """Tests for utils.parse_text_only_prefix()."""

    def test_no_prefix_returns_element_and_false(self):
        assert utils.parse_text_only_prefix("Submit") == ("Submit", False)
        assert utils.parse_text_only_prefix("Login") == ("Login", False)
        assert utils.parse_text_only_prefix("//div") == ("//div", False)

    def test_text_only_prefix_strips_and_returns_true(self):
        assert utils.parse_text_only_prefix("TEXT_ONLY:Submit") == ("Submit", True)
        assert utils.parse_text_only_prefix("TEXT_ONLY:Login") == ("Login", True)

    def test_text_only_prefix_case_insensitive(self):
        assert utils.parse_text_only_prefix("text_only:Foo") == ("Foo", True)
        assert utils.parse_text_only_prefix("Text_Only: Bar") == ("Bar", True)

    def test_text_only_prefix_strips_leading_space_after_colon(self):
        assert utils.parse_text_only_prefix("TEXT_ONLY: Submit") == ("Submit", True)
        assert utils.parse_text_only_prefix("TEXT_ONLY:  Login") == ("Login", True)


class TestDetermineElementTypeWithTextOnly:
    """Tests for determine_element_type() when TEXT_ONLY: prefix is used."""

    def test_text_only_submit_classified_as_text(self):
        assert utils.determine_element_type("TEXT_ONLY:Submit") == "Text"

    def test_text_only_login_classified_as_text(self):
        assert utils.determine_element_type("TEXT_ONLY:Login") == "Text"

    def test_text_only_case_insensitive(self):
        assert utils.determine_element_type("text_only:foo") == "Text"


# --- StrategyManager locate() with TEXT_ONLY ---


@pytest.fixture
def mock_element_source():
    """Element source with locate() and capture() for Text and TextDetection strategies."""
    source = MagicMock()
    source.locate.return_value = None
    # Real numpy array so TextDetectionStrategy.locate() can call utils.annotate(screenshot.copy(), [bbox])
    source.capture.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
    return source


@pytest.fixture
def mock_text_detection():
    """Text detection that find_element returns coords (for TextDetectionStrategy)."""
    td = MagicMock()
    td.find_element.return_value = (True, (50, 50), ((0, 0), (100, 20)))
    return td


@pytest.fixture
def strategy_manager(mock_element_source, mock_text_detection):
    """StrategyManager with one mock element source and text_detection (no image_detection)."""
    fallback = InstanceFallback([mock_element_source])
    return StrategyManager(
        element_source=fallback,
        text_detection=mock_text_detection,
        image_detection=None,
    )


def _make_dummy_locate_result(strategy):
    """Return a LocateResult so locate() yields and does not raise OpticsError."""
    return LocateResult((0, 0), strategy, annotated_frame=None)


class TestStrategyManagerLocateTextOnly:
    """TEXT_ONLY: element should skip TextElementStrategy and only use TextDetectionStrategy."""

    def test_locate_text_only_skips_text_element_strategy(self, strategy_manager):
        """When element is TEXT_ONLY:foo, _try_strategy_locate is never called with TextElementStrategy."""
        tried_strategies = []
        real_try = strategy_manager._try_strategy_locate  # save before patch to avoid recursion

        def record_and_return_success_on_text_detection(strategy, element, *args, **kwargs):
            name = type(strategy).__name__
            tried_strategies.append(name)
            if name == "TextDetectionStrategy":
                return _make_dummy_locate_result(strategy)
            return real_try(strategy, element, *args, **kwargs)

        with patch.object(strategy_manager, "_try_strategy_locate", side_effect=record_and_return_success_on_text_detection):
            results = list(strategy_manager.locate("TEXT_ONLY:foo"))
            assert len(results) == 1
            assert results[0].strategy.__class__.__name__ == "TextDetectionStrategy"
            assert "TextElementStrategy" not in tried_strategies
            assert "TextDetectionStrategy" in tried_strategies

    def test_locate_text_only_passes_stripped_element_to_strategy(self, strategy_manager):
        """TEXT_ONLY:Submit should pass 'Submit' (not 'TEXT_ONLY:Submit') to the strategy."""
        captured_elements = []  # record every element passed (first successful call is what we care about)

        def capture_element_and_succeed(strategy, element, *args, **kwargs):
            captured_elements.append(element)
            return _make_dummy_locate_result(strategy)

        with patch.object(strategy_manager, "_try_strategy_locate", side_effect=capture_element_and_succeed):
            list(strategy_manager.locate("TEXT_ONLY:Submit"))
            assert "Submit" in captured_elements
            assert "TEXT_ONLY:Submit" not in captured_elements

    def test_locate_without_prefix_tries_both_text_strategies(self, strategy_manager):
        """Without TEXT_ONLY:, both TextElementStrategy and TextDetectionStrategy can be tried."""
        tried_strategies = []
        real_try = strategy_manager._try_strategy_locate  # save before patch to avoid recursion

        def record_and_return_success_on_text_detection(strategy, element, *args, **kwargs):
            name = type(strategy).__name__
            tried_strategies.append(name)
            if name == "TextDetectionStrategy":
                return _make_dummy_locate_result(strategy)
            return real_try(strategy, element, *args, **kwargs)

        with patch.object(strategy_manager, "_try_strategy_locate", side_effect=record_and_return_success_on_text_detection):
            results = list(strategy_manager.locate("Submit"))
            assert len(results) == 1
            assert "TextDetectionStrategy" in tried_strategies


# --- StrategyManager assert_presence() with TEXT_ONLY ---


class TestStrategyManagerAssertPresenceTextOnly:
    """assert_presence with TEXT_ONLY elements uses effective_elements and excludes TextElementStrategy."""

    def test_assert_presence_text_only_excludes_text_element_strategy(self, strategy_manager):
        """When any element has TEXT_ONLY:, only TextDetectionStrategy is used for the group."""
        tried_strategies = []

        original_try = strategy_manager._try_assert_with_strategy

        def record_and_try(strategy, elements, *args, **kwargs):
            tried_strategies.append(type(strategy).__name__)
            return original_try(strategy, elements, *args, **kwargs)

        # TextDetectionStrategy.assert_elements returns (True, timestamp, frame) for success
        with patch.object(strategy_manager, "_try_assert_with_strategy", side_effect=record_and_try):
            with patch.object(TextDetectionStrategy, "assert_elements", return_value=(True, None, None)):
                strategy_manager.assert_presence(["TEXT_ONLY:Login", "TEXT_ONLY:Submit"], "Text", timeout=1, rule="any")
            assert "TextElementStrategy" not in tried_strategies
            assert "TextDetectionStrategy" in tried_strategies

    def test_assert_presence_text_only_passes_stripped_elements(self, strategy_manager):
        """Elements with TEXT_ONLY: prefix are passed to strategies stripped."""
        seen_elements = []

        def capture_and_succeed(strategy, elements, *args, **kwargs):
            nonlocal seen_elements
            seen_elements = elements
            return (True, None, None)

        with patch.object(strategy_manager, "_try_assert_with_strategy", side_effect=capture_and_succeed):
            strategy_manager.assert_presence(
                ["Submit", "TEXT_ONLY:Login"],
                "Text",
                timeout=1,
                rule="any",
            )
            assert seen_elements == ["Submit", "Login"]
            assert "Submit" in seen_elements
            assert "Login" in seen_elements
            assert "TEXT_ONLY:Login" not in seen_elements


# --- capture_screenshot_bytes (native fast path + numpy fallback) ---


def _sm_with_source(source):
    """StrategyManager wrapping a single element source (screenshot path only)."""
    return StrategyManager(
        element_source=InstanceFallback([source]),
        text_detection=None,
        image_detection=None,
    )


class TestCaptureScreenshotBytes:
    """StrategyManager.capture_screenshot_bytes() prefers the native path, else encodes numpy."""

    def test_prefers_native_bytes(self):
        source = MagicMock()
        source.capture_screenshot_bytes.return_value = b"\x89PNG-native"
        sm = _sm_with_source(source)
        assert sm.capture_screenshot_bytes() == b"\x89PNG-native"
        source.capture.assert_not_called()  # native path => no numpy capture/encode

    def test_raises_optics_error_when_all_sources_fail(self):
        source = MagicMock()
        source.capture_screenshot_bytes.side_effect = RuntimeError("hub timeout")
        sm = _sm_with_source(source)
        with pytest.raises(OpticsError):
            sm.capture_screenshot_bytes()

    def test_interface_default_encodes_numpy_via_capture(self):
        """ElementSourceInterface.capture_screenshot_bytes() default encodes capture() result."""

        class _MinimalSource(ElementSourceInterface):
            def capture(self) -> np.ndarray:
                return np.full((8, 8, 3), 255, dtype=np.uint8)
            def locate(self, element, index=None): raise NotImplementedError
            def assert_elements(self, elements, timeout=30, rule='any'): raise NotImplementedError
            def get_interactive_elements(self, filter_config=None): raise NotImplementedError

        source = _MinimalSource()
        data = source.capture_screenshot_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes from cv2.imencode


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
    """Skip the inter-retry sleep so retry tests stay fast."""
    monkeypatch.setattr(time, "sleep", lambda *_a, **_k: None)


class TestAppiumScreenshotBytes:
    """AppiumScreenshot.capture_screenshot_bytes() decodes base64 with a single retry."""

    def test_returns_decoded_base64(self):
        png = b"\x89PNG\r\n\x1a\nDATA"
        drv = _FakeWD([base64.b64encode(png).decode("ascii")])
        assert AppiumScreenshot(driver=drv).capture_screenshot_bytes() == png
        assert drv.calls == 1

    def test_retries_then_succeeds(self, _no_backoff):
        png = b"\x89PNGok"
        drv = _FakeWD(["not-valid-base64!!!", base64.b64encode(png).decode("ascii")])
        assert AppiumScreenshot(driver=drv).capture_screenshot_bytes() == png
        assert drv.calls == 2

    def test_raises_after_exhausted_retries(self, _no_backoff):
        drv = _FakeWD([WebDriverException("boom"), WebDriverException("boom")])
        with pytest.raises(RuntimeError):
            AppiumScreenshot(driver=drv).capture_screenshot_bytes()
        assert drv.calls == 2

    def test_numpy_path_reuses_bytes(self, _no_backoff):
        """capture_screenshot_as_numpy delegates to the bytes path (shared retry)."""
        # A real 2x2 PNG so cv2.imdecode succeeds; corrupt first to exercise the shared retry.
        ok_png = cv2.imencode(".png", np.full((2, 2, 3), 255, np.uint8))[1].tobytes()
        drv = _FakeWD(["bad!!!", base64.b64encode(ok_png).decode("ascii")])
        img = AppiumScreenshot(driver=drv).capture_screenshot_as_numpy()
        assert img.shape == (2, 2, 3)
        assert drv.calls == 2


class TestSeleniumScreenshotBytes:
    """SeleniumScreenshot mirrors the Appium native base64 path."""

    def test_returns_decoded_base64(self):
        png = b"\x89PNG\r\n\x1a\nDATA"
        drv = _FakeWD([base64.b64encode(png).decode("ascii")])
        assert SeleniumScreenshot(driver=drv).capture_screenshot_bytes() == png

    def test_retries_then_succeeds(self, _no_backoff):
        png = b"\x89PNGok"
        drv = _FakeWD(["bad!!!", base64.b64encode(png).decode("ascii")])
        assert SeleniumScreenshot(driver=drv).capture_screenshot_bytes() == png
        assert drv.calls == 2


class TestPlaywrightScreenshotBytes:
    """PlaywrightScreenshot returns page.screenshot()'s PNG bytes verbatim (no decode)."""

    def test_returns_page_screenshot_bytes(self, monkeypatch):
        pytest.importorskip("playwright")
        import optics_framework.engines.elementsources.playwright_screenshot as pw
        monkeypatch.setattr(pw, "run_async", lambda coro: coro)
        png = b"\x89PNG\r\n\x1a\nPLAYWRIGHT"
        page = MagicMock()
        page.screenshot.return_value = png
        src = pw.PlaywrightScreenshot(driver=SimpleNamespace(page=page))
        assert src.capture_screenshot_bytes() == png


# --- Real strategy classes driven through the public locate() API ---


def _sm(source, *, text_detection=None, image_detection=None):
    return StrategyManager(
        element_source=InstanceFallback([source]),
        text_detection=text_detection,
        image_detection=image_detection,
    )


class TestLocateRealStrategies:
    """Drive the actual strategy ladder (no patching of private methods)."""

    def test_xpath_yields_element_source_handle(self):
        handle = object()
        source = MagicMock()
        source.locate.return_value = handle
        results = list(_sm(source).locate("//div[@id='x']"))
        assert any(r.value is handle for r in results)
        assert any(type(r.strategy).__name__ == "XPathStrategy" for r in results)

    def test_text_element_yields_from_element_source(self):
        handle = object()
        source = MagicMock()
        source.locate.return_value = handle
        results = list(_sm(source).locate("Submit"))
        assert any(type(r.strategy).__name__ == "TextElementStrategy" for r in results)

    def test_locate_raises_e0201_when_no_strategy_yields(self):
        source = MagicMock()
        source.locate.return_value = None  # element source finds nothing
        with pytest.raises(OpticsError) as exc_info:
            list(_sm(source).locate("Submit"))
        assert exc_info.value.code == Code.E0201


class TestImageDetectionStrategy:
    """ImageDetectionStrategy locates an image element via image_detection."""

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

    def test_no_match_contributes_nothing(self):
        source = MagicMock()
        source.capture.return_value = np.zeros((100, 100, 3), dtype=np.uint8)
        image_detection = MagicMock()
        image_detection.find_element.return_value = None
        with pytest.raises(OpticsError) as exc_info:
            list(_sm(source, image_detection=image_detection).locate("button.png"))
        assert exc_info.value.code == Code.E0201


class TestAssertPresenceContract:
    """assert_presence input validation."""

    @pytest.mark.parametrize("rule", ["maybe", "none", ""])
    def test_invalid_rule_raises_e0205(self, rule):
        with pytest.raises(OpticsError) as exc_info:
            _sm(MagicMock()).assert_presence(["Submit"], "Text", timeout=1, rule=rule)
        assert exc_info.value.code == Code.E0205


class TestAllocTimeForStrategy:
    """_alloc_time_for_strategy splits the remaining budget across strategies."""

    def test_even_division_rounds_up(self, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        alloc, remaining, n = _sm(MagicMock())._alloc_time_for_strategy(1010.0, 0, [1, 2, 3])
        assert alloc == 4  # ceil(10 / 3)
        assert n == 3

    def test_no_time_left_returns_none(self, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        # deadline already passed
        assert _sm(MagicMock())._alloc_time_for_strategy(999.0, 0, [1, 2]) is None

    def test_last_strategy_gets_remainder_even_if_sub_second(self, monkeypatch):
        monkeypatch.setattr(time, "time", lambda: 1000.0)
        # 0.4s left, last strategy (idx 1 of 2): alloc rounds to 0 but the last one still runs.
        result = _sm(MagicMock())._alloc_time_for_strategy(1000.4, 1, [1, 2])
        assert result is not None
        assert result[0] == 0
