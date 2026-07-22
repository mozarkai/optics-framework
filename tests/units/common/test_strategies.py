"""Unit tests for TEXT_ONLY prefix feature and strategy selection."""
import numpy as np
import pytest
from unittest.mock import MagicMock, patch

from optics_framework.common import utils
from optics_framework.common.strategies import (
    StrategyManager,
    TextDetectionStrategy,
    LocateResult,
)
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface


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


# --- StrategyManager.assert_visibility() vs assert_presence() (shared _assert helper) ---


class TestStrategyManagerAssertVisibility:
    """assert_visibility must call assert_elements_visible, not assert_elements, per strategy."""

    def test_assert_visibility_calls_assert_elements_visible(self, strategy_manager):
        seen_method_names = []

        def capture_and_succeed(strategy, elements, timeout, rule, method_name="assert_elements"):
            seen_method_names.append(method_name)
            return (True, "ts", None)

        with patch.object(strategy_manager, "_try_assert_with_strategy", side_effect=capture_and_succeed):
            result, timestamp, _ = strategy_manager.assert_visibility(["Submit"], "Text", timeout=3, rule="any")

        assert result is True
        assert timestamp == "ts"
        assert seen_method_names == ["assert_elements_visible"]

    def test_assert_presence_still_uses_assert_elements(self, strategy_manager):
        """The DRY refactor must not regress assert_presence's existing behavior."""
        seen_method_names = []

        def capture_and_succeed(strategy, elements, timeout, rule, method_name="assert_elements"):
            seen_method_names.append(method_name)
            return (True, "ts", None)

        with patch.object(strategy_manager, "_try_assert_with_strategy", side_effect=capture_and_succeed):
            result, _, _ = strategy_manager.assert_presence(["Submit"], "Text", timeout=3, rule="any")

        assert result is True
        assert seen_method_names == ["assert_elements"]

    def test_assert_elements_visible_default_falls_back_to_assert_elements(self, strategy_manager):
        """Strategies that don't override assert_elements_visible (e.g. vision-based ones)
        default to assert_elements, since anything they find is inherently on-screen."""
        td_strategy = next(
            s for s in strategy_manager.locator_strategies if isinstance(s, TextDetectionStrategy)
        )
        with patch.object(TextDetectionStrategy, "assert_elements", return_value=(True, "ts", None)) as mock_presence:
            result, timestamp, _ = td_strategy.assert_elements_visible(["Submit"], timeout=1, rule="any")
        assert result is True
        assert timestamp == "ts"
        mock_presence.assert_called_once()


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
        with pytest.raises(Exception):
            manager.assert_visibility(["//*[@id=\"offscreen\"]"], "XPath", timeout=1, rule="any")
