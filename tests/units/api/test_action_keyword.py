import pytest
from unittest.mock import MagicMock, patch
import tempfile
import numpy as np
from optics_framework.common.error import OpticsError, Code

from optics_framework.api.action_keyword import ActionKeyword
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
    """press_element param coercion and located-value dispatch.

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

    @pytest.mark.parametrize("index_str, index_int", [("0", 0), ("1", 1), ("5", 5)])
    def test_string_index_is_coerced_to_int_for_locate(self, action_keyword, index_str, index_int):
        with self._mock_locate(action_keyword, (100, 150)) as mock_locate:
            action_keyword.press_element("button", index=index_str)
        mock_locate.assert_called_once_with("button", index=index_int)
        assert isinstance(mock_locate.call_args.kwargs["index"], int)

    def test_default_index_is_zero(self, action_keyword):
        with self._mock_locate(action_keyword, (100, 100)) as mock_locate:
            action_keyword.press_element("button")
        mock_locate.assert_called_once_with("button", index=0)

    def test_coordinate_result_presses_coordinates(self, action_keyword, mock_dependencies):
        with self._mock_locate(action_keyword, (100, 150)):
            action_keyword.press_element("button")
        mock_dependencies['driver'].press_coordinates.assert_called_once_with(100, 150, None)

    def test_element_result_presses_element_with_repeat(self, action_keyword, mock_dependencies):
        element_handle = MagicMock()
        with self._mock_locate(action_keyword, element_handle):
            action_keyword.press_element("//button", repeat="3")
        mock_dependencies['driver'].press_element.assert_called_once_with(element_handle, 3, None)

    def test_offset_adjusts_coordinates(self, action_keyword, mock_dependencies):
        with self._mock_locate(action_keyword, (100, 150)):
            action_keyword.press_element("button", offset_x="10", offset_y="20")
        mock_dependencies['driver'].press_coordinates.assert_called_once_with(110, 170, None)

    def test_event_name_forwarded_to_driver(self, action_keyword, mock_dependencies):
        with self._mock_locate(action_keyword, (120, 180)):
            action_keyword.press_element("button", event_name="test_event")
        mock_dependencies['driver'].press_coordinates.assert_called_once_with(120, 180, "test_event")

    def test_aoi_params_passed_to_locate(self, action_keyword, mock_dependencies):
        with self._mock_locate(action_keyword, (200, 250)) as mock_locate:
            action_keyword.press_element(
                "button", index="1", aoi_x="10", aoi_y="20", aoi_width="50", aoi_height="60"
            )
        mock_locate.assert_called_once_with("button", 10.0, 20.0, 50.0, 60.0, index=1)
        mock_dependencies['driver'].press_coordinates.assert_called_once_with(200, 250, None)

    def test_no_located_result_raises_element_not_found(self, action_keyword):
        with patch.object(action_keyword.strategy_manager, 'locate', return_value=[]):
            with pytest.raises(OpticsError) as exc_info:
                action_keyword.press_element("ghost_button")
        assert exc_info.value.code in (Code.E0201, Code.X0201)


class TestScreenshotFailureFallback:
    """Tests for behavior when screenshot capture fails (e.g. secure/protected pages)."""

    @patch('optics_framework.common.utils.save_screenshot')
    def test_with_self_healing_proceeds_when_screenshot_raises(
        self, mock_save_screenshot, action_keyword, mock_dependencies
    ):
        """Action still executes when capture_screenshot raises (e.g. INTERNAL_SERVER_ERROR)."""
        mock_locate_result = LocateResult((100, 150), MagicMock())

        with patch.object(
            action_keyword.strategy_manager, 'capture_screenshot',
            side_effect=OpticsError(Code.E0303, message="INTERNAL_SERVER_ERROR")
        ):
            with patch.object(action_keyword.strategy_manager, 'locate', return_value=[mock_locate_result]):
                action_keyword.press_element("button")

        mock_dependencies['driver'].press_coordinates.assert_called_once_with(100, 150, None)

    @patch('optics_framework.common.utils.save_screenshot')
    def test_with_self_healing_skips_save_when_screenshot_raises(
        self, mock_save_screenshot, action_keyword, mock_dependencies
    ):
        """utils.save_screenshot is not called when capture_screenshot raises."""
        mock_locate_result = LocateResult((100, 150), MagicMock())

        with patch.object(
            action_keyword.strategy_manager, 'capture_screenshot',
            side_effect=OpticsError(Code.E0303, message="INTERNAL_SERVER_ERROR")
        ):
            with patch.object(action_keyword.strategy_manager, 'locate', return_value=[mock_locate_result]):
                action_keyword.press_element("button")

        mock_save_screenshot.assert_not_called()

    @patch('optics_framework.common.utils.annotate_aoi_region')
    @patch('optics_framework.common.utils.save_screenshot')
    def test_with_self_healing_skips_aoi_save_when_screenshot_raises(
        self, mock_save_screenshot, mock_annotate_aoi, action_keyword, mock_dependencies
    ):
        """AOI annotation and save are skipped when capture_screenshot raises."""
        mock_locate_result = LocateResult((100, 150), MagicMock())

        with patch.object(
            action_keyword.strategy_manager, 'capture_screenshot',
            side_effect=OpticsError(Code.E0303, message="INTERNAL_SERVER_ERROR")
        ):
            with patch.object(action_keyword.strategy_manager, 'locate', return_value=[mock_locate_result]):
                action_keyword.press_element(
                    "button", aoi_x="10", aoi_y="10", aoi_width="50", aoi_height="50"
                )

        mock_annotate_aoi.assert_not_called()
        mock_save_screenshot.assert_not_called()

    @patch('optics_framework.common.utils.save_screenshot')
    def test_direct_method_proceeds_when_screenshot_raises(
        self, mock_save_screenshot, action_keyword, mock_dependencies
    ):
        """Direct methods (not via with_self_healing) still execute when capture_screenshot raises."""
        with patch.object(
            action_keyword.strategy_manager, 'capture_screenshot',
            side_effect=OpticsError(Code.E0303, message="INTERNAL_SERVER_ERROR")
        ):
            action_keyword.press_by_percentage("50", "50")

        mock_dependencies['driver'].press_percentage_coordinates.assert_called_once_with(50.0, 50.0, 1, None)
        mock_save_screenshot.assert_not_called()


class TestSelectDropdownOption:
    """select_dropdown_option must open the dropdown then select the option (not a no-op)."""

    def test_opens_dropdown_then_selects_option(self, action_keyword):
        with patch.object(action_keyword, "press_element") as mock_press, \
             patch.object(action_keyword.strategy_manager, "get_interactive_elements", return_value=[{"text": "India"}]):
            action_keyword.select_dropdown_option("Country", "India", event_name="evt")

        assert mock_press.call_count == 2
        # First press opens the dropdown, second selects the option — order matters.
        assert mock_press.call_args_list[0].args[0] == "Country"
        assert mock_press.call_args_list[1].args[0] == "India"
        # event_name is threaded through to both presses.
        assert all(c.kwargs.get("event_name") == "evt" for c in mock_press.call_args_list)

    def test_missing_target_raises_instead_of_silent_pass(self, action_keyword):
        # The old stub returned None (silent PASS); now a not-found target must surface.
        with patch.object(
            action_keyword, "press_element",
            side_effect=OpticsError(Code.X0201, message="element not found"),
        ):
            with pytest.raises(OpticsError):
                action_keyword.select_dropdown_option("Country", "India")


class TestSwipeUntilElementAppears:
    """Tests for OpticsError(E0201) handling in swipe_until_element_appears."""

    @patch('optics_framework.api.action_keyword.time.sleep', return_value=None)
    @patch('optics_framework.api.action_keyword.time.time')
    def test_e0201_continues_loop_until_found(self, mock_time, mock_sleep, action_keyword):
        """E0201 is caught, loop continues, element found on second call."""
        mock_time.side_effect = [0, 0, 3, 6, 9]
        call_count = 0

        def assert_presence_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OpticsError(Code.E0201, message="Element not found")
            return True

        with patch.object(action_keyword.verifier, 'assert_presence', side_effect=assert_presence_side_effect):
            action_keyword.swipe_until_element_appears("element", "down", "10")

        assert call_count == 2
        action_keyword.driver.swipe_percentage.assert_called_once_with(10, 50, "down", 25, None)

    @patch('optics_framework.api.action_keyword.time.sleep', return_value=None)
    @patch('optics_framework.api.action_keyword.time.time')
    def test_non_e0201_is_reraised(self, mock_time, mock_sleep, action_keyword):
        """Non-E0201 OpticsError is re-raised immediately."""
        mock_time.side_effect = [0, 0]

        with patch.object(
            action_keyword.verifier, 'assert_presence',
            side_effect=OpticsError(Code.E0303, message="Screenshot failed")
        ):
            with pytest.raises(OpticsError) as exc_info:
                action_keyword.swipe_until_element_appears("element", "down", "10")

        assert exc_info.value.code == Code.E0303
        action_keyword.driver.swipe_percentage.assert_not_called()

    @patch('optics_framework.api.action_keyword.time.sleep', return_value=None)
    @patch('optics_framework.api.action_keyword.time.time')
    def test_element_found_stops_loop(self, mock_time, mock_sleep, action_keyword):
        """Element found on first call, no swipe performed."""
        mock_time.side_effect = [0, 0]

        with patch.object(action_keyword.verifier, 'assert_presence', return_value=True):
            action_keyword.swipe_until_element_appears("element", "down", "10")

        action_keyword.driver.swipe_percentage.assert_not_called()

    @patch('optics_framework.api.action_keyword.time.sleep', return_value=None)
    @patch('optics_framework.api.action_keyword.time.time')
    def test_timeout_stops_loop(self, mock_time, mock_sleep, action_keyword):
        """Element never found, loop exits after timeout and raises."""
        mock_time.side_effect = [0, 0, 3, 6, 9, 12]

        with patch.object(
            action_keyword.verifier, 'assert_presence',
            side_effect=OpticsError(Code.E0201, message="Element not found")
        ):
            with pytest.raises(OpticsError) as exc_info:
                action_keyword.swipe_until_element_appears("element", "down", "10")

        assert exc_info.value.code == Code.E0201
        assert action_keyword.driver.swipe_percentage.call_count == 4
