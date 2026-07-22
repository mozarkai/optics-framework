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

        with pytest.raises(AssertionError):
            verifier.assert_visibility("Option 47", timeout_str="3", rule="any", fail=True)

    def test_assert_visibility_false_with_fail_false_returns_false(self, verifier):
        verifier.strategy_manager.assert_visibility = MagicMock(return_value=(False, None, None))

        result = verifier.assert_visibility("Option 47", timeout_str="3", rule="any", fail=False)

        assert result is False
