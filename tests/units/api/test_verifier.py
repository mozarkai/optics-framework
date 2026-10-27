"""Unit tests for Verifier.assert_visibility / assert_presence (shared _assert_common helper)."""
from unittest.mock import MagicMock
import pytest

from optics_framework.api.verifier import Verifier
from optics_framework.common.error import Code, OpticsError
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


class TestCssLocators:
    def test_a_css_locator_is_asserted_instead_of_skipped(self, verifier):
        verifier.strategy_manager.assert_presence = MagicMock(return_value=(False, None, None))

        with pytest.raises(AssertionError):
            verifier.assert_presence("#missing", timeout_str="1")

        verifier.strategy_manager.assert_presence.assert_called_once_with(["#missing"], "Text", 1, "any")


class TestIsElementState:
    @pytest.fixture
    def located(self, verifier):
        element = object()
        verifier.strategy_manager.locate = MagicMock(return_value=iter([MagicMock(value=element)]))
        return element

    @pytest.mark.parametrize("state, check, value", [
        ("enabled", "is_element_enabled", True),
        ("disabled", "is_element_enabled", False),
        ("checked", "is_element_checked", True),
        ("unchecked", "is_element_checked", False),
    ])
    def test_the_driver_decides_the_state(self, verifier, located, state, check, value):
        getattr(verifier.driver, check).return_value = value

        verifier.is_element("css=#x", state, 1)

        getattr(verifier.driver, check).assert_called_once_with(located)

    @pytest.mark.parametrize("state, check, value", [
        ("enabled", "is_element_enabled", False),
        ("disabled", "is_element_enabled", True),
        ("checked", "is_element_checked", False),
        ("unchecked", "is_element_checked", True),
    ])
    def test_the_opposite_state_fails(self, verifier, located, state, check, value):
        getattr(verifier.driver, check).return_value = value

        with pytest.raises(OpticsError, match=f"expected {state}"):
            verifier.is_element("css=#x", state, 1)


class TestAssertText:
    @pytest.fixture(autouse=True)
    def located(self, verifier, monkeypatch):
        monkeypatch.setattr(Verifier, "_TEXT_POLL_INTERVAL_S", 0)
        verifier.strategy_manager.assert_presence = MagicMock()
        element = object()
        verifier.strategy_manager.locate = MagicMock(side_effect=lambda _: iter([MagicMock(value=element)]))
        return element

    def test_passes_once_the_text_matches(self, verifier, located):
        verifier.driver.get_text_element.side_effect = ["Loading", " DELHI "]

        verifier.assert_text("css=#state", "DELHI", timeout="5")

        verifier.driver.get_text_element.assert_called_with(located)

    def test_a_wrong_text_fails_with_both_values(self, verifier):
        verifier.driver.get_text_element.return_value = "MUMBAI"

        with pytest.raises(OpticsError, match="shows 'MUMBAI' but expected 'DELHI'"):
            verifier.assert_text("css=#state", "DELHI", timeout="0")

    def test_a_missing_element_is_element_not_found(self, verifier):
        verifier.strategy_manager.locate = MagicMock(side_effect=OpticsError(Code.E0201, message="nope"))

        with pytest.raises(OpticsError) as raised:
            verifier.assert_text("css=#state", "DELHI", timeout="0")

        assert raised.value.code == Code.E0201

    def test_an_unsupported_platform_fails_at_once(self, verifier):
        verifier.driver.get_text_element.side_effect = OpticsError(Code.E0105, message="not on a TV")

        with pytest.raises(OpticsError) as raised:
            verifier.assert_text("css=#state", "DELHI", timeout="30")

        assert raised.value.code == Code.E0105

    def test_an_element_found_only_by_vision_fails_at_once(self, verifier):
        verifier.strategy_manager.locate = MagicMock(return_value=iter([MagicMock(value=(10, 20))]))

        with pytest.raises(OpticsError, match="found only by OCR or image"):
            verifier.assert_text("DELHI", "DELHI", timeout="30")

    def test_a_secret_is_compared_without_its_prefix(self, verifier):
        verifier.driver.get_text_element.return_value = "4321"

        verifier.assert_text("css=#pin", "@:4321", timeout="0")

    def test_a_secret_stays_out_of_the_error(self, verifier):
        verifier.driver.get_text_element.return_value = "4320"

        with pytest.raises(OpticsError) as raised:
            verifier.assert_text("css=#pin", "@:4321", timeout="0")

        assert "4321" not in str(raised.value)
        assert "4320" not in str(raised.value)
