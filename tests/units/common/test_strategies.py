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
        seen_elements = None

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
            assert seen_elements is not None
            assert "Submit" in seen_elements
            assert "Login" in seen_elements
            assert "TEXT_ONLY:Login" not in seen_elements
