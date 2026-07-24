"""Unit tests for Verifier.assert_visibility / assert_presence (shared _assert_common helper)."""
from unittest.mock import MagicMock
import pytest

from optics_framework.api.verifier import Verifier
from optics_framework.common.optics_builder import OpticsBuilder


class MockOpticsBuilder(OpticsBuilder):
    def __init__(self):
        self.mock_driver = MagicMock()
        self.mock_element_source = MagicMock()
        self.session_config = MagicMock()
        self.session_config.execution_output_path = "/tmp"
        self.session_config.save_captures = False
        self.session = MagicMock()

    def get_driver(self):
        return self.mock_driver

    def get_element_source(self):
        return self.mock_element_source

    def get_text_detection(self):
        return None

    def get_image_detection(self):
        return None

    @property
    def event_sdk(self):
        return MagicMock()


@pytest.fixture
def verifier():
    return Verifier(MockOpticsBuilder())


class TestAssertVisibilityVsPresence:
    """assert_visibility must route to StrategyManager.assert_visibility, not assert_presence."""

    def test_assert_visibility_calls_strategy_manager_assert_visibility(self, verifier):
        verifier.strategy_manager.assert_visibility = MagicMock(return_value=(True, "ts", None))
        verifier.strategy_manager.assert_presence = MagicMock(return_value=(True, "ts", None))

        result = verifier.assert_visibility("Option 47", timeout_str="3", rule="any")

        assert result is True
        verifier.strategy_manager.assert_visibility.assert_called_once()
        verifier.strategy_manager.assert_presence.assert_not_called()

    def test_assert_presence_still_calls_strategy_manager_assert_presence(self, verifier):
        """The DRY refactor must not regress assert_presence's existing routing."""
        verifier.strategy_manager.assert_visibility = MagicMock(return_value=(True, "ts", None))
        verifier.strategy_manager.assert_presence = MagicMock(return_value=(True, "ts", None))

        result = verifier.assert_presence("Option 47", timeout_str="3", rule="any")

        assert result is True
        verifier.strategy_manager.assert_presence.assert_called_once()
        verifier.strategy_manager.assert_visibility.assert_not_called()

    def test_assert_visibility_false_with_fail_true_raises(self, verifier):
        verifier.strategy_manager.assert_visibility = MagicMock(return_value=(False, None, None))

        with pytest.raises(AssertionError, match="Visibility assertion failed"):
            verifier.assert_visibility("Option 47", timeout_str="3", rule="any", fail=True)

    def test_assert_presence_false_with_fail_true_raises_presence_message(self, verifier):
        """Regression test: the error message must reflect which assertion actually ran,
        not always say "Presence" regardless of assert_presence vs assert_visibility."""
        verifier.strategy_manager.assert_presence = MagicMock(return_value=(False, None, None))

        with pytest.raises(AssertionError, match="Presence assertion failed"):
            verifier.assert_presence("Option 47", timeout_str="3", rule="any", fail=True)

    def test_assert_visibility_false_with_fail_false_returns_false(self, verifier):
        verifier.strategy_manager.assert_visibility = MagicMock(return_value=(False, None, None))

        result = verifier.assert_visibility("Option 47", timeout_str="3", rule="any", fail=False)

        assert result is False


class TestAssertCommonElementGrouping:
    """Real CSV workflow: elements can be pipe-separated and mix types (Text/XPath/Image),
    each type group triggering its own StrategyManager call."""

    def test_pipe_separated_multi_type_elements_each_trigger_own_call(self, verifier):
        calls = []

        def fake_assert_visibility(elements, element_type, timeout, rule):
            calls.append((element_type, elements))
            return (True, "ts", None)

        verifier.strategy_manager.assert_visibility = MagicMock(side_effect=fake_assert_visibility)

        elements = "button.png|//android.widget.Button[@text='Submit']|Cancel"
        result = verifier.assert_visibility(elements, timeout_str="3", rule="any")

        assert result is True
        assert verifier.strategy_manager.assert_visibility.call_count == 3
        calls_by_type = dict(calls)
        assert calls_by_type["Image"] == ["button.png"]
        assert calls_by_type["XPath"] == ["//android.widget.Button[@text='Submit']"]
        assert calls_by_type["Text"] == ["Cancel"]

    def test_text_only_prefixed_element_is_grouped_and_forwarded_as_text(self, verifier):
        """TEXT_ONLY: forces vision-based text search -- StrategyManager itself strips the
        prefix and uses it to exclude TextElementStrategy, so it must reach StrategyManager
        with the prefix intact rather than being stripped or dropped at the Verifier layer."""
        verifier.strategy_manager.assert_visibility = MagicMock(return_value=(True, "ts", None))

        result = verifier.assert_visibility("TEXT_ONLY:Submit", timeout_str="3", rule="any")

        assert result is True
        verifier.strategy_manager.assert_visibility.assert_called_once_with(
            ["TEXT_ONLY:Submit"], "Text", 3, "any"
        )
