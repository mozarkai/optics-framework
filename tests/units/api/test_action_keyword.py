import pytest
from unittest.mock import MagicMock, patch
import tempfile
import numpy as np
from optics_framework.common.error import OpticsError, Code

from optics_framework.api import ActionKeyword
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.strategies import LocateResult


class MockOpticsBuilder(OpticsBuilder):
    """Mock builder for ActionKeyword testing."""

    def __init__(self, mock_driver, mock_element_source, mock_text_detection=None, mock_image_detection=None):
        self.mock_driver = mock_driver
        self.mock_element_source = mock_element_source
        self.mock_text_detection = mock_text_detection
        self.mock_image_detection = mock_image_detection
        self.temp_dir = tempfile.mkdtemp()

        # Mock session config
        self.session_config = MagicMock()
        self.session_config.execution_output_path = self.temp_dir

        # Mock session (Verifier reads builder.session directly)
        self.session = MagicMock()

    def get_driver(self):
        return self.mock_driver

    def get_element_source(self):
        return self.mock_element_source

    def get_text_detection(self):
        return self.mock_text_detection

    def get_image_detection(self):
        return self.mock_image_detection

    @property
    def event_sdk(self):
        return MagicMock()


@pytest.fixture
def mock_dependencies():
    """Fixture providing all mocked dependencies for ActionKeyword."""
    mock_driver = MagicMock()
    mock_element_source = MagicMock()
    mock_text_detection = MagicMock()
    mock_image_detection = MagicMock()

    # Mock element_source to return screenshot data
    mock_element_source.capture.return_value = MagicMock()

    return {
        'driver': mock_driver,
        'element_source': mock_element_source,
        'text_detection': mock_text_detection,
        'image_detection': mock_image_detection
    }


@pytest.fixture
def action_keyword(mock_dependencies):
    """Fixture providing ActionKeyword instance with mocked dependencies."""
    builder = MockOpticsBuilder(
        mock_dependencies['driver'],
        mock_dependencies['element_source'],
        mock_dependencies['text_detection'],
        mock_dependencies['image_detection']
    )
    action_kw = ActionKeyword(builder)

    # Mock the capture_screenshot method to avoid screenshot strategy issues
    mock_screenshot = np.zeros((100, 100, 3), dtype=np.uint8)  # Mock screenshot array
    with patch.object(action_kw.strategy_manager, 'capture_screenshot', return_value=mock_screenshot):
        yield action_kw


class TestPressElementWithIndex:
    """press_element param coercion and locate-result dispatch.

    ``locate`` is mocked to return a LocateResult, so ``determine_element_type``
    (which only runs *inside* the real locate) is never reached — no need to patch
    it. A class-level fixture stubs the disk-writing save_screenshot.
    """

    @pytest.fixture(autouse=True)
    def _no_disk_writes(self):
        with patch('optics_framework.common.utils.save_screenshot'):
            yield

    def _mock_locate(self, action_keyword, value):
        return patch.object(
            action_keyword.strategy_manager, 'locate', return_value=[LocateResult(value, MagicMock())]
        )

    def test_element_result_presses_element_with_repeat(self, action_keyword, mock_dependencies):
        element_handle = MagicMock()
        with self._mock_locate(action_keyword, element_handle):
            action_keyword.press_element("//button", repeat="3")
        mock_dependencies['driver'].press_element.assert_called_once_with(element_handle, 3, event_name=None)

    def test_event_name_forwarded_to_driver(self, action_keyword, mock_dependencies):
        with self._mock_locate(action_keyword, (120, 180)):
            action_keyword.press_element("button", event_name="test_event")
        mock_dependencies['driver'].press_coordinates.assert_called_once_with(120, 180, event_name="test_event")


class TestScreenshotFailureFallback:
    """Tests for behavior when screenshot capture fails (e.g. secure/protected pages)."""

    @patch('optics_framework.common.utils.save_screenshot')
    def test_direct_method_proceeds_when_screenshot_raises(
        self, mock_save_screenshot, action_keyword, mock_dependencies
    ):
        """Direct methods (no locate step) still execute when capture_screenshot raises."""
        with patch.object(
            action_keyword.strategy_manager, 'capture_screenshot',
            side_effect=OpticsError(Code.E0303, message="INTERNAL_SERVER_ERROR")
        ):
            action_keyword.press_by_percentage("50", "50")

        mock_dependencies['driver'].press_percentage_coordinates.assert_called_once_with(50.0, 50.0, 1, event_name=None)
        mock_save_screenshot.assert_not_called()


class TestSelectDropdownOption:
    """select_dropdown_option must open the dropdown then select the option (not a no-op)."""

    def test_pagesource_unavailable_falls_back_to_direct_press(self, action_keyword, mock_dependencies):
        """When page source / interactive elements aren't available at all, skip validation."""
        with patch.object(
            action_keyword.strategy_manager, "get_interactive_elements",
            side_effect=OpticsError(Code.E0202, message="no strategies"),
        ), patch.object(
            action_keyword.verifier, "assert_visibility",
            side_effect=OpticsError(Code.E0201, message="not found"),
        ), patch.object(action_keyword, "press_element") as mock_press:
            action_keyword.select_dropdown_option("Country", "India", event_name="evt")

        assert mock_press.call_count == 2
        assert mock_press.call_args_list[0].args[0] == "Country"
        assert mock_press.call_args_list[1].args[0] == "India"
        mock_dependencies['driver'].swipe.assert_not_called()

    def test_scroll_exhausted_raises_with_available_options(self, action_keyword, mock_dependencies):
        """Stops scrolling once the list stops changing, and reports what was visible."""
        container = {
            "text": "list", "xpath": "container1",
            "bounds": {"x1": 0, "y1": 0, "x2": 100, "y2": 200},
            "extra": {"class": "android.widget.ListView"},
        }
        baseline = []
        after_open = [
            container,
            {"text": "Only Option", "xpath": "opt1", "bounds": {"x1": 0, "y1": 0, "x2": 100, "y2": 50}, "extra": {}},
        ]

        with patch.object(
            action_keyword.strategy_manager, "get_interactive_elements",
            side_effect=[baseline, after_open, after_open],
        ), patch.object(
            action_keyword.strategy_manager, "capture_pagesource",
            side_effect=[("<xml>same</xml>", "t1"), ("<xml>same</xml>", "t2")],
        ), patch.object(
            action_keyword.verifier, "assert_visibility",
            side_effect=OpticsError(Code.E0201, message="not found"),
        ), patch.object(action_keyword, "press_element") as mock_press, \
                patch("optics_framework.api.action_keyword.time.sleep", return_value=None):
            with pytest.raises(OpticsError) as exc_info:
                action_keyword.select_dropdown_option("Country", "Missing Option", timeout="30")

        assert exc_info.value.code == Code.E0201
        assert "Only Option" in exc_info.value.message
        # Bailed out after the list stopped changing, not after burning the full timeout.
        assert mock_dependencies['driver'].swipe.call_count == 1
        # Only the trigger press happened; the missing option was never pressed.
        assert mock_press.call_count == 1


class TestSwipeUntilElementAppears:
    """Tests for OpticsError(E0201) handling in swipe_until_element_appears."""

    @patch('optics_framework.api.action_keyword.time')
    def test_element_found_stops_loop(self, mock_time, action_keyword):
        """Element found on first call, no swipe performed."""
        mock_time.time.side_effect = [0, 0]

        with patch.object(action_keyword.verifier, 'assert_presence', return_value=True):
            action_keyword.swipe_until_element_appears("element", "down", "10")

        action_keyword.driver.swipe_percentage.assert_not_called()

    @patch('optics_framework.api.action_keyword.time')
    def test_timeout_stops_loop(self, mock_time, action_keyword):
        """Element never found, loop exits after timeout and raises."""
        mock_time.time.side_effect = [0, 0, 3, 6, 9, 12]

        with patch.object(
            action_keyword.verifier, 'assert_presence',
            side_effect=OpticsError(Code.E0201, message="Element not found")
        ):
            with pytest.raises(OpticsError) as exc_info:
                action_keyword.swipe_until_element_appears("element", "down", "10")

        assert exc_info.value.code == Code.E0201
        assert action_keyword.driver.swipe_percentage.call_count == 4


class TestDeprecatedAliases:
    """Deprecated keywords stay callable and delegate to press_element."""

    @pytest.fixture(autouse=True)
    def _no_disk_writes(self):
        with patch('optics_framework.common.utils.save_screenshot'):
            yield

    @pytest.mark.parametrize("alias", ["press_checkbox", "press_radio_button"])
    def test_alias_delegates_to_press_element_with_deprecation_log(self, action_keyword, alias):
        with patch.object(action_keyword, 'press_element') as mock_press, patch(
            'optics_framework.api.action_keyword.internal_logger'
        ) as mock_logger:
            getattr(action_keyword, alias)("box", aoi_x="10", event_name="evt")

        mock_press.assert_called_once_with(
            "box", aoi_x="10", aoi_y="0", aoi_width="100",
            aoi_height="100", event_name="evt", index="0",
        )
        assert any(
            "deprecated" in str(call.args[0]).lower()
            for call in mock_logger.warning.call_args_list
        )


class TestLocateHonorsPositionalParams:
    """AOI/index must be honored no matter how the keyword was called.

    The MCP/serve dispatch invokes keywords with every parameter positional
    (it rebuilds a flat positional list from the signature), so locate-tuning
    values that arrive in positional slots must still reach the locator.
    Regression for the pre-refactor behavior where only kwargs were read.
    """

    @pytest.fixture(autouse=True)
    def _no_disk_writes(self):
        with patch('optics_framework.common.utils.save_screenshot'):
            yield

    def test_kwarg_aoi_and_index_reach_locate(self, action_keyword):
        locate_result = LocateResult((7, 7), MagicMock())
        with patch.object(
            action_keyword.strategy_manager, 'locate', return_value=[locate_result]
        ) as mock_locate:
            action_keyword.enter_text("field", "hello", aoi_x="10", index="3")

        mock_locate.assert_called_once_with("field", 10.0, 0.0, 100.0, 100.0, index=3)
