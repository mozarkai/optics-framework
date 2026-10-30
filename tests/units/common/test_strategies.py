"""Unit tests for the element-location layer (common/strategies.py).

Covers the TEXT_ONLY prefix + strategy selection, the real strategy classes driven
through the public ``locate`` / ``assert_presence`` API (XPath, Text, Image
detection), the not-found (E0201) and invalid-rule (E0205) contracts, the
interleaved assert_presence polling, and the native screenshot-bytes fast path.
"""
import base64
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

from optics_framework.common import utils
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.strategies import (
    LocatorStrategy,
    PagesourceStrategy,
    StrategyManager,
    TextDetectionStrategy,
    TextElementStrategy,
    LocateResult,
)
from optics_framework.engines.elementsources.appium_screenshot import AppiumScreenshot
from optics_framework.engines.elementsources.selenium_screenshot import SeleniumScreenshot

import optics_framework.engines.drivers.appium  # noqa: F401


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
    # Real, non-black numpy array: TextDetectionStrategy annotates it, and assertions reject a black screen.
    source.capture.return_value = np.full((100, 100, 3), 255, dtype=np.uint8)
    # Explicit assignment: MagicMock reserves bare `assert_*` attribute access for its own
    # assertion methods (raises AttributeError instead of auto-vivifying), so this must be
    # set directly for hasattr()/_is_method_implemented() to see it as implemented.
    source.assert_elements_visible = MagicMock()
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

    @pytest.mark.parametrize("elements", [["TEXT_ONLY:Submit", "TEXT_ONLY:Login"], ["Submit", "TEXT_ONLY:Login"]])
    def test_text_only_reaches_ocr_stripped_and_skips_text_element_strategy(self, strategy_manager, elements):
        seen = []

        def record(elements, visible, frame):
            seen.append(elements)
            return [True, True], None

        with patch.object(TextElementStrategy, "check_elements") as text_element, \
                patch.object(TextDetectionStrategy, "check_elements", side_effect=record):
            strategy_manager.assert_presence(elements, "Text", timeout=1, rule="any")

        text_element.assert_not_called()
        assert seen == [["Submit", "Login"]]


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


# --- StrategyManager.assert_visibility() vs assert_presence() (shared _assert helper) ---


class TestStrategyManagerAssertVisibility:
    """assert_visibility asks sources for visibility, assert_presence for presence."""

    @pytest.mark.parametrize(("method", "visible"), [("assert_visibility", True), ("assert_presence", False)])
    def test_visible_flag_reaches_the_source_check(self, strategy_manager, method, visible):
        with patch.object(TextElementStrategy, "check_elements", return_value=([True], None)) as check:
            result, timestamp, _ = getattr(strategy_manager, method)(["Submit"], "Text", timeout=3, rule="any")

        assert result is True
        assert timestamp
        check.assert_called_once_with(["Submit"], visible, None)

    def test_text_only_visibility_reaches_text_detection(self, strategy_manager):
        with patch.object(TextDetectionStrategy, "check_elements", return_value=([True], None)) as check:
            result, _, _ = strategy_manager.assert_visibility(["TEXT_ONLY:Submit"], "Text", timeout=1, rule="any")

        assert result is True
        assert check.call_args.args[:2] == (["Submit"], True)


class _PresenceOnlySource(ElementSourceInterface):
    """Mirrors AppiumPageSource: implements assert_elements (presence) but not
    assert_elements_visible -- inherits the base's raise-NotImplementedError stub."""

    def capture(self):
        raise NotImplementedError

    def get_interactive_elements(self, filter_config=None):
        raise NotImplementedError

    def locate(self, element, index=None):
        return "located"

    def assert_elements(self, elements, timeout=30, rule='any'):
        return True


class _VisibilityAwareSource(ElementSourceInterface):
    """Mirrors AppiumFindElement: implements a real assert_elements_visible that can
    correctly disagree with assert_elements (present but off-screen)."""

    def capture(self):
        raise NotImplementedError

    def get_interactive_elements(self, filter_config=None):
        raise NotImplementedError

    def locate(self, element, index=None):
        return "located"

    def assert_elements(self, elements, timeout=30, rule='any'):
        return True

    def assert_elements_visible(self, elements, timeout=30, rule='any'):
        raise TimeoutError(f"Elements not visible: {elements}")


class TestVisibilityExcludesPresenceOnlySources:
    """Regression test for the false-positive where a presence-only source (e.g.
    AppiumPageSource) masked a correct "not visible" from a real visibility-aware
    source (e.g. AppiumFindElement), because both back an XPathStrategy and the
    presence-only one silently fell back to a presence check for "visibility"."""

    def test_presence_only_source_excluded_from_visibility_assertions(self):
        presence_only = _PresenceOnlySource()
        manager = StrategyManager(
            element_source=InstanceFallback([presence_only]),
            text_detection=None,
            image_detection=None,
        )
        xpath_strategy = next(
            s for s in manager.locator_strategies if type(s).__name__ == "XPathStrategy"
        )
        assert manager._can_strategy_assert_elements(xpath_strategy, "XPath", "assert_elements") is True
        assert manager._can_strategy_assert_elements(xpath_strategy, "XPath", "assert_elements_visible") is False

    def test_presence_only_source_does_not_mask_visibility_aware_source(self):
        """Two sources present at once: the presence-only one must not report an
        element "visible" (via presence) when the real visibility-aware source
        correctly says it isn't."""
        manager = StrategyManager(
            element_source=InstanceFallback([_PresenceOnlySource(), _VisibilityAwareSource()]),
            text_detection=None,
            image_detection=None,
        )
        with pytest.raises(OpticsError):
            manager.assert_visibility(["//*[@id=\"offscreen\"]"], "XPath", timeout=1, rule="any")


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


class TestTryStrategyLocateAoiFallthrough:
    """AOI enforcement for strategies that can't scope natively (accessibility/XPath):
    a WebElement match whose centre is outside the AOI is skipped, so locate() falls
    through to an AOI-capable strategy; one inside is returned."""

    def _stub_strategy(self, bbox):
        source = MagicMock()
        source.get_bbox_for_element.return_value = bbox
        # spec omits locate_with_aoi, so the manager takes the non-native-AOI path.
        strategy = MagicMock(spec=["supports", "locate", "element_source"])
        strategy.supports.return_value = True
        strategy.locate.return_value = MagicMock(name="webelement")  # a WebElement, not a tuple
        strategy.element_source = source
        return strategy

    def _locate(self, bbox, aoi):
        strategy = self._stub_strategy(bbox)
        with patch.object(utils, "_window_size_from_source", return_value=(100, 100)):
            return _sm(MagicMock())._try_strategy_locate(
                strategy, "//div", "XPath", True, *aoi, index=0
            )

    def test_match_inside_aoi_is_returned(self):
        # centre (50%,50%) inside AOI x25 y25 w50 h50
        result = self._locate(((40, 40), (60, 60)), (25, 25, 50, 50))
        assert result is not None and isinstance(result, LocateResult)

    def test_match_outside_aoi_is_skipped(self):
        # centre (5%,5%) outside the same AOI -> falls through (None)
        result = self._locate(((0, 0), (10, 10)), (25, 25, 50, 50))
        assert result is None


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


class _MissingTextSource(_PresenceOnlySource):
    """A native source that never finds the element, and can capture (so OCR applies)."""

    def capture(self):
        return None

    def assert_elements(self, elements, timeout=30, rule='any'):
        raise OpticsError(Code.E0201, message="Timeout reached")


class TestAssertPresenceContract:
    """assert_presence input validation."""

    @pytest.mark.parametrize("rule", ["maybe", "none", ""])
    def test_invalid_rule_raises_e0205(self, rule):
        with pytest.raises(OpticsError) as exc_info:
            _sm(MagicMock()).assert_presence(["Submit"], "Text", timeout=1, rule=rule)
        assert exc_info.value.code == Code.E0205

    def test_unconfigured_ocr_does_not_mask_the_miss(self):
        manager = _sm(_MissingTextSource())
        with pytest.raises(OpticsError) as exc_info:
            manager.assert_presence(["Submit"], "Text", timeout=0, rule="any")
        assert "OCR was not tried" in exc_info.value.message
        assert "Text detection is not available" not in str(exc_info.value.__cause__)


# --- StrategyManager._assert interleaved polling ---


class _CheckSource(ElementSourceInterface):
    """Answers single checks from ``hits``; accepts only ``accepts`` when given."""

    def __init__(self, hits=(), accepts=None, error=None):
        self.hits = set(hits)
        self.accepts = accepts
        self.error = error
        self.calls = []

    def capture(self):
        raise NotImplementedError

    def get_interactive_elements(self, filter_config=None):
        raise NotImplementedError

    def locate(self, element, index=None):
        return None

    def assert_elements(self, elements, timeout=30, rule="any"):
        raise NotImplementedError

    def can_locate(self, element):
        return self.accepts is None or element in self.accepts

    def check_elements(self, elements, visible=False):
        self.calls.append(list(elements))
        if self.error is not None:
            raise self.error
        return [element in self.hits for element in elements]


class _LegacySource(_CheckSource):
    """Only the old polling assert_elements; no check_elements override."""

    check_elements = ElementSourceInterface.check_elements

    def assert_elements(self, elements, timeout=30, rule="any"):
        start = time.time()
        while time.time() - start < timeout:
            if self.error is not None:
                raise self.error
            if any(element in self.hits for element in elements):
                return True, "ts"
        raise TimeoutError(f"not found: {elements}")


def _manager(*sources, text_detection=None):
    return StrategyManager(InstanceFallback(list(sources)), text_detection=text_detection, image_detection=None)


class TestInterleavedAssert:
    def test_a_matching_source_wins_without_waiting_out_a_missing_one(self):
        miss, hit = _CheckSource(), _CheckSource(hits={"//a"})
        start = time.monotonic()

        result, _, _ = _manager(miss, hit).assert_presence(["//a"], "XPath", timeout=30)

        assert result is True
        assert time.monotonic() - start < 1
        assert miss.calls == [["//a"]]

    def test_rule_all_latches_hits_across_sources(self):
        a = _CheckSource(hits={"//a"}, accepts={"//a"})
        b = _CheckSource(hits={"//b"}, accepts={"//b"})

        result, _, _ = _manager(a, b).assert_presence(["//a", "//b"], "XPath", timeout=5, rule="all")

        assert result is True
        assert a.calls == [["//a"]]
        assert b.calls == [["//b"]]

    def test_rule_all_with_an_unlocatable_element_fails_at_once(self):
        source = _CheckSource(accepts={"//a"})
        manager = _manager(source)

        with pytest.raises(OpticsError) as exc_info:
            manager.assert_presence(["//a", "//c"], "XPath", timeout=30, rule="all")

        assert exc_info.value.code == Code.E0201
        assert "No strategies found" in exc_info.value.message
        assert source.calls == []

    def test_legacy_source_without_check_elements_still_works(self):
        assert _LegacySource(hits={"//a"}).check_elements(["//a"]) == [True]
        assert _LegacySource().check_elements(["//a"]) == [False]
        assert _LegacySource(error=RuntimeError("not found")).check_elements(["//a"]) == [False]
        result, _, _ = _manager(_LegacySource(hits={"//a"})).assert_presence(["//a"], "XPath", timeout=5)
        assert result is True

        dead = _manager(_LegacySource(error=InvalidSessionIdException("A session is either terminated or not started")))
        with pytest.raises(OpticsError) as exc_info:
            dead.assert_presence(["//a"], "XPath", timeout=5)
        assert exc_info.value.code == Code.E0106

    def test_ocr_does_not_search_for_css_selectors(self):
        strategy = TextDetectionStrategy(MagicMock(), None, None)

        assert strategy.can_assert("css=#q") is False
        assert strategy.can_assert("Login") is True


# --- StrategyManager.get_interactive_elements() strategy screening ---


class TestGetInteractiveElementsStrategyScreening:
    """Only sources that actually implement extraction should be tried.

    A source qualifies as a pagesource strategy by returning a page source, which is a
    weaker capability than extracting elements from one -- AppiumFindElement supports
    the first and unconditionally raises on the second. Trying it anyway burned work
    and logged a traceback on every call to a hot path (mozarkai/optics-framework#455).
    """

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

    def test_unsupported_source_is_never_called(self, monkeypatch):
        capable = self._strategy(True, [{"text": "OK"}])
        incapable = self._strategy(False)
        monkeypatch.setattr(
            LocatorStrategy,
            "_is_method_implemented",
            staticmethod(lambda src, name: src is capable.element_source),
        )
        manager = _sm(MagicMock())
        manager.pagesource_strategies = [incapable, capable]

        assert manager.get_interactive_elements() == [{"text": "OK"}]
        incapable.get_interactive_elements.assert_not_called()

    def test_no_capable_strategy_raises_rather_than_returning_empty(self, monkeypatch):
        monkeypatch.setattr(
            LocatorStrategy, "_is_method_implemented", staticmethod(lambda src, name: False)
        )
        manager = _sm(MagicMock())
        manager.pagesource_strategies = [self._strategy(False)]

        with pytest.raises(OpticsError) as exc_info:
            manager.get_interactive_elements()
        assert exc_info.value.code == Code.E0202

    def test_failure_names_the_underlying_error(self, monkeypatch):
        monkeypatch.setattr(
            LocatorStrategy, "_is_method_implemented", staticmethod(lambda src, name: True)
        )
        strategy = MagicMock()
        strategy.get_interactive_elements.side_effect = RuntimeError(
            "Page.content: the page is navigating"
        )
        manager = _sm(MagicMock())
        manager.pagesource_strategies = [strategy]

        with pytest.raises(OpticsError) as exc_info:
            manager.get_interactive_elements()
        assert exc_info.value.code == Code.E0202
        assert "the page is navigating" in str(exc_info.value)

# --- PagesourceStrategy compact-forwarding compatibility ---


class _LegacyExtractionSource:
    def __init__(self):
        self.calls: list = []

    def get_interactive_elements(self, filter_config=None):
        self.calls.append(filter_config)
        return [{"text": "OK"}]


class _VarKwargsExtractionSource:
    def __init__(self):
        self.kwargs: dict = {}

    def get_interactive_elements(self, filter_config=None, **kwargs):
        self.kwargs = kwargs
        return []


class TestPagesourceStrategyCompactForwarding:
    def test_legacy_signature_is_called_without_compact(self):
        source = _LegacyExtractionSource()
        elements = PagesourceStrategy(source).get_interactive_elements(None, compact=True)
        assert elements == [{"text": "OK"}]
        assert source.calls == [None]

    def test_declared_compact_is_forwarded(self):
        source = MagicMock()
        source.get_interactive_elements.return_value = []
        PagesourceStrategy(source).get_interactive_elements(None, compact=True)
        source.get_interactive_elements.assert_called_once_with(None, compact=True)

    def test_var_keyword_signature_is_forwarded(self):
        source = _VarKwargsExtractionSource()
        PagesourceStrategy(source).get_interactive_elements(None, compact=True)
        assert source.kwargs == {"compact": True}

    def test_none_result_still_raises_not_implemented(self):
        source = _LegacyExtractionSource()
        source.get_interactive_elements = lambda filter_config=None: None
        strategy = PagesourceStrategy(source)
        with pytest.raises(NotImplementedError):
            strategy.get_interactive_elements()


class TestDeadSessionIsNotMasked:
    @staticmethod
    def _dead() -> InvalidSessionIdException:
        return InvalidSessionIdException("A session is either terminated or not started")

    def test_locate_raises_dead_session_instead_of_e0201(self):
        source = MagicMock()
        source.locate.side_effect = self._dead()
        results = _sm(source).locate("//button[@id='search']")

        with pytest.raises(OpticsError) as exc_info:
            list(results)
        assert exc_info.value.code == Code.E0106

    def test_locate_unwraps_a_source_that_already_wrapped_it_as_e0201(self):
        source = MagicMock()
        source.locate.side_effect = OpticsError(
            Code.E0201, message="Element of type XPath not found", cause=self._dead()
        )
        results = _sm(source).locate("//button[@id='search']")

        with pytest.raises(OpticsError) as exc_info:
            list(results)
        assert exc_info.value.code == Code.E0106

    def test_assert_presence_raises_dead_session_instead_of_e0201(self):
        source = MagicMock()
        source.check_elements = MagicMock(side_effect=self._dead())
        manager = _sm(source)

        with pytest.raises(OpticsError) as exc_info:
            manager.assert_presence(["text=Search"], "Text", timeout=10, rule="any")
        assert exc_info.value.code == Code.E0106
        assert source.check_elements.called

    def test_capture_pagesource_raises_dead_session_instead_of_e0403(self):
        source = MagicMock()
        source.get_page_source.side_effect = self._dead()
        manager = _sm(source)

        with pytest.raises(OpticsError) as exc_info:
            manager.capture_pagesource()
        assert exc_info.value.code == Code.E0106

    def test_capture_screenshot_raises_dead_session_instead_of_e0303(self):
        source = MagicMock()
        source.capture.side_effect = self._dead()
        manager = _sm(source)

        with pytest.raises(OpticsError) as exc_info:
            manager.capture_screenshot()
        assert exc_info.value.code == Code.E0106

    def test_capture_screenshot_bytes_raises_dead_session_instead_of_e0303(self):
        source = MagicMock()
        source.capture_screenshot_bytes.side_effect = self._dead()
        manager = _sm(source)

        with pytest.raises(OpticsError) as exc_info:
            manager.capture_screenshot_bytes()
        assert exc_info.value.code == Code.E0106

    def test_get_interactive_elements_raises_dead_session_instead_of_e0202(self, monkeypatch):
        monkeypatch.setattr(
            LocatorStrategy, "_is_method_implemented", staticmethod(lambda src, name: True)
        )
        source = MagicMock()
        source.get_interactive_elements.side_effect = self._dead()
        manager = _sm(source)

        with pytest.raises(OpticsError) as exc_info:
            manager.get_interactive_elements()
        assert exc_info.value.code == Code.E0106

    def test_e0106_is_outside_the_element_fallback_family(self):
        assert not Code.E0106.value.startswith("E02")
        assert Code.E0106 != Code.X0201

    def test_ordinary_backend_failure_still_reports_not_found(self):
        source = MagicMock()
        source.locate.side_effect = WebDriverException("stale element reference")
        results = _sm(source).locate("//button[@id='search']")

        with pytest.raises(OpticsError) as exc_info:
            list(results)
        assert exc_info.value.code == Code.E0201
