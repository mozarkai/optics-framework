import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
import tempfile
import numpy as np
from optics_framework.common.error import OpticsError, Code

from optics_framework.api.action_keyword import ActionKeyword, _find_dropdown_container
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.strategies import LocateResult

FIXTURES_DIR = Path(__file__).parent / "fixtures"

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


@pytest.fixture(scope="module")
def dropdown_pagesource():
    """Real Appium interactive-element captures (before/after opening a long dropdown),
    sourced from an Android device."""
    return json.loads((FIXTURES_DIR / "dropdown_pagesource.json").read_text())


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
    @patch('optics_framework.common.utils.save_screenshot')
    @patch('optics_framework.common.utils.determine_element_type')
    def test_press_element_with_index_str(self, mock_determine_type, mock_save_screenshot, action_keyword, mock_dependencies):
        import numpy as np
        with patch.object(action_keyword.strategy_manager, 'capture_screenshot', return_value=np.zeros((10,10,3), dtype=np.uint8)):
            """Test that press_element handles index parameter as string correctly."""
            # Setup
            mock_determine_type.return_value = "Text"
            element = "button"
            index = "1"
            expected_coordinates = (100, 150)

            # Mock StrategyManager.locate
            mock_locate_result = LocateResult(expected_coordinates, MagicMock())

            with patch.object(action_keyword.strategy_manager, 'locate') as mock_locate:
                mock_locate.return_value = [mock_locate_result]

                # Execute
                action_keyword.press_element(element, index=index)

                # Verify locate was called with correct index (should be int)
                mock_locate.assert_called_once_with(element, index=1)

                # Verify driver was called with correct coordinates
                mock_dependencies['driver'].press_coordinates.assert_called_once_with(100, 150, None)
    """Test cases for press_element method with index parameter."""

    @patch('optics_framework.common.utils.save_screenshot')
    @patch('optics_framework.common.utils.determine_element_type')
    def test_press_element_with_index_coordinates(self, mock_determine_type, mock_save_screenshot, action_keyword, mock_dependencies):
        """Test press_element with index parameter using coordinate-based location."""
        # Setup
        mock_determine_type.return_value = "Text"
        element = "test_button"
        index = "2"
        expected_coordinates = (150, 200)

        # Mock StrategyManager.locate
        mock_locate_result = LocateResult(expected_coordinates, MagicMock())

        with patch.object(action_keyword.strategy_manager, 'locate') as mock_locate:
            mock_locate.return_value = [mock_locate_result]

            # Execute
            action_keyword.press_element(element, index=index)

        # Verify locate was called with correct index (converted to int by decorator)
        mock_locate.assert_called_once_with(element, index=int(index))

        # Verify driver was called with correct coordinates
        mock_dependencies['driver'].press_coordinates.assert_called_once_with(150, 200, None)

    @patch('optics_framework.common.utils.save_screenshot')
    @patch('optics_framework.common.utils.determine_element_type')
    def test_press_element_with_index_element_object(self, mock_determine_type, mock_save_screenshot, action_keyword, mock_dependencies):
        """Test press_element with index parameter using element object location."""
        # Setup
        mock_determine_type.return_value = "XPath"
        element = "//button[@text='Submit']"
        index = "1"
        expected_element = MagicMock()

        # Mock StrategyManager.locate to return element object at specified index
        mock_locate_result = LocateResult(expected_element, MagicMock())

        with patch.object(action_keyword.strategy_manager, 'locate') as mock_locate:
            mock_locate.return_value = [mock_locate_result]

            # Execute
            action_keyword.press_element(element, index=index, repeat="3")

        # Verify locate was called with correct index (converted to int by decorator)
        mock_locate.assert_called_once_with(element, index=int(index))

        # Verify driver.press_element was called with correct element and repeat count
        mock_dependencies['driver'].press_element.assert_called_once_with(expected_element, 3, None)

    @patch('optics_framework.common.utils.save_screenshot')
    @patch('optics_framework.common.utils.determine_element_type')
    def test_press_element_with_index_and_offset(self, mock_determine_type, mock_save_screenshot, action_keyword, mock_dependencies):
        """Test press_element with index and offset parameters."""
        # Setup
        mock_determine_type.return_value = "Text"
        element = "test_element"
        index = "3"
        offset_x, offset_y = "10", "20"
        expected_coordinates = (100, 150)

        # Mock StrategyManager.locate to return coordinates
        mock_locate_result = LocateResult(expected_coordinates, MagicMock())

        with patch.object(action_keyword.strategy_manager, 'locate') as mock_locate:
            mock_locate.return_value = [mock_locate_result]

            # Execute
            action_keyword.press_element(element, index=index, offset_x=offset_x, offset_y=offset_y)

        # Verify locate was called with correct index (converted to int by decorator)
        mock_locate.assert_called_once_with(element, index=int(index))

        # Verify coordinates were adjusted by offset
        expected_x = 100 + int(offset_x)  # 110
        expected_y = 150 + int(offset_y)  # 170
        mock_dependencies['driver'].press_coordinates.assert_called_once_with(expected_x, expected_y, None)

    @patch('optics_framework.common.utils.save_screenshot')
    @patch('optics_framework.common.utils.determine_element_type')
    def test_press_element_with_index_and_aoi(self, mock_determine_type, mock_save_screenshot, action_keyword, mock_dependencies):
        """Test press_element with index parameter and AOI (Area of Interest)."""
        # Setup
        mock_determine_type.return_value = "Text"
        element = "button_in_region"
        index = "1"
        aoi_x, aoi_y, aoi_width, aoi_height = "10", "20", "50", "60"
        expected_coordinates = (200, 250)

        # Mock StrategyManager.locate to return coordinates
        mock_locate_result = LocateResult(expected_coordinates, MagicMock())

        with patch.object(action_keyword.strategy_manager, 'locate') as mock_locate:
            with patch('optics_framework.common.utils.calculate_aoi_bounds') as mock_calculate_aoi:
                mock_calculate_aoi.return_value = (10, 20, 50, 60)  # Mock validation
                mock_locate.return_value = [mock_locate_result]

                # Execute
                action_keyword.press_element(
                    element,
                    index=index,
                    aoi_x=aoi_x,
                    aoi_y=aoi_y,
                    aoi_width=aoi_width,
                    aoi_height=aoi_height
                )

            # Verify locate was called with correct parameters including index (all converted by decorator)
            mock_locate.assert_called_once_with(element, float(aoi_x), float(aoi_y), float(aoi_width), float(aoi_height), index=int(index))

            # Verify press_coordinates was called
            mock_dependencies['driver'].press_coordinates.assert_called_once_with(200, 250, None)

    @patch('optics_framework.common.utils.save_screenshot')
    @patch('optics_framework.common.utils.determine_element_type')
    def test_press_element_default_index_zero(self, mock_determine_type, mock_save_screenshot, action_keyword, mock_dependencies):
        with patch.object(action_keyword.strategy_manager, 'capture_screenshot', return_value=np.zeros((10,10,3), dtype=np.uint8)):
            """Test that press_element uses index=0 by default."""
            # Setup
            mock_determine_type.return_value = "Text"
            element = "default_button"
            expected_coordinates = (100, 100)

            # Mock StrategyManager.locate
            mock_locate_result = LocateResult(expected_coordinates, MagicMock())

            with patch.object(action_keyword.strategy_manager, 'locate') as mock_locate:
                mock_locate.return_value = [mock_locate_result]

                # Execute without specifying index
                action_keyword.press_element(element)

                # Verify locate was called with index=0 (default)
                mock_locate.assert_called_once_with(element, index=0)

                # Verify press_coordinates was called
                mock_dependencies['driver'].press_coordinates.assert_called_once_with(100, 100, None)

    @patch('optics_framework.common.utils.save_screenshot')
    @patch('optics_framework.common.utils.determine_element_type')
    def test_press_element_with_event_name(self, mock_determine_type, mock_save_screenshot, action_keyword, mock_dependencies):
        """Test press_element with index and event_name parameters."""
        # Setup
        mock_determine_type.return_value = "Text"
        element = "event_button"
        index = "2"
        event_name = "test_event"
        expected_coordinates = (120, 180)

        # Mock StrategyManager.locate
        mock_locate_result = LocateResult(expected_coordinates, MagicMock())

        with patch.object(action_keyword.strategy_manager, 'locate') as mock_locate:
            mock_locate.return_value = [mock_locate_result]

            # Execute
            action_keyword.press_element(element, index=index, event_name=event_name)

        # Verify locate was called with correct index (converted to int by decorator)
        mock_locate.assert_called_once_with(element, index=int(index))

        # Verify event_name was passed to driver
        mock_dependencies['driver'].press_coordinates.assert_called_once_with(120, 180, event_name)

    def test_press_element_with_invalid_aoi_parameters(self, action_keyword):
        """Test that press_element handles partial AOI parameters correctly."""
        # With the new default system, partial AOI parameters should work
        # This will use defaults for missing width and height (100, 100)
        action_keyword.press_element("test", index="1", aoi_x="10", aoi_y="20")

    @patch('optics_framework.common.utils.save_screenshot')
    @patch('optics_framework.common.utils.determine_element_type')
    def test_press_element_index_type_handling(self, mock_determine_type, mock_save_screenshot, action_keyword, mock_dependencies):
        """Test that press_element handles index parameter as integer correctly."""
        # Setup
        mock_determine_type.return_value = "Text"
        element = "type_test_button"
        index = "5"  # String value
        expected_coordinates = (300, 400)

        # Mock StrategyManager.locate
        mock_locate_result = LocateResult(expected_coordinates, MagicMock())

        with patch.object(action_keyword.strategy_manager, 'locate') as mock_locate:
            mock_locate.return_value = [mock_locate_result]

            # Execute
            action_keyword.press_element(element, index=index)

            # Verify locate was called with integer index
            mock_locate.assert_called_once_with(element, index=5)
            assert isinstance(mock_locate.call_args[1]['index'], int)


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

    def test_opens_dropdown_then_selects_option(self, action_keyword, mock_dependencies):
        with patch.object(action_keyword, "press_element") as mock_press, \
             patch.object(action_keyword.strategy_manager, "get_interactive_elements", return_value=[]), \
             patch.object(action_keyword.verifier, "assert_visibility", return_value=True):
            action_keyword.select_dropdown_option("Country", "India", event_name="evt")

        assert mock_press.call_count == 2
        # First press opens the dropdown, second selects the option — order matters.
        assert mock_press.call_args_list[0].args[0] == "Country"
        assert mock_press.call_args_list[1].args[0] == "India"
        # event_name is threaded through to both presses.
        assert all(c.kwargs.get("event_name") == "evt" for c in mock_press.call_args_list)
        # Option was already visible — no need to scroll the (nonexistent) dropdown list.
        mock_dependencies['driver'].swipe.assert_not_called()

    def test_missing_target_raises_instead_of_silent_pass(self, action_keyword):
        # The old stub returned None (silent PASS); now a not-found target must surface.
        with patch.object(
            action_keyword, "press_element",
            side_effect=OpticsError(Code.X0201, message="element not found"),
        ):
            with pytest.raises(OpticsError):
                action_keyword.select_dropdown_option("Country", "India")

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

    def test_no_list_like_container_raises_not_found(self, action_keyword, mock_dependencies):
        """New elements appear after opening, but none look like a scrollable list container."""
        baseline = []
        after_open = [{
            "text": "Something Else", "xpath": "opt1",
            "bounds": {"x1": 0, "y1": 0, "x2": 50, "y2": 50},
            "extra": {"class": "android.widget.TextView"},
        }]
        with patch.object(
            action_keyword.strategy_manager, "get_interactive_elements",
            side_effect=[baseline, after_open],
        ), patch.object(
            action_keyword.verifier, "assert_visibility",
            side_effect=OpticsError(Code.E0201, message="not found"),
        ), patch.object(action_keyword, "press_element") as mock_press:
            with pytest.raises(OpticsError) as exc_info:
                action_keyword.select_dropdown_option("Country", "Missing", timeout="30")

        assert exc_info.value.code == Code.E0201
        mock_dependencies['driver'].swipe.assert_not_called()
        # Only the trigger press happened; the missing option was never pressed.
        assert mock_press.call_count == 1

    def test_scrolls_and_finds_option_after_swipes(self, action_keyword, mock_dependencies):
        """Option below the fold is found after scrolling within the dropdown container."""
        container = {
            "text": "list", "xpath": "container1",
            "bounds": {"x1": 0, "y1": 100, "x2": 200, "y2": 500},
            "extra": {"class": "android.widget.RecyclerView"},
        }
        baseline = [{
            "text": "Country", "xpath": "trigger",
            "bounds": {"x1": 0, "y1": 0, "x2": 50, "y2": 50}, "extra": {},
        }]
        after_open = baseline + [
            container,
            {"text": "Apple", "xpath": "opt1", "bounds": {"x1": 0, "y1": 100, "x2": 200, "y2": 150}, "extra": {}},
            {"text": "Banana", "xpath": "opt2", "bounds": {"x1": 0, "y1": 150, "x2": 200, "y2": 200}, "extra": {}},
        ]

        with patch.object(
            action_keyword.strategy_manager, "get_interactive_elements",
            side_effect=[baseline, after_open],
        ), patch.object(
            action_keyword.strategy_manager, "capture_pagesource",
            side_effect=[("<xml>v1</xml>", "t1"), ("<xml>v2</xml>", "t2"), ("<xml>v3</xml>", "t3")],
        ), patch.object(
            action_keyword.verifier, "assert_visibility",
            side_effect=[
                OpticsError(Code.E0201, message="not found"),  # initial check, right after opening
                OpticsError(Code.E0201, message="not found"),  # after 1st swipe
                True,                                          # after 2nd swipe — found
            ],
        ), patch.object(action_keyword, "press_element") as mock_press, \
                patch("optics_framework.api.action_keyword.time.sleep", return_value=None):
            action_keyword.select_dropdown_option("Country", "Zebra", timeout="30")

        # Two scroll swipes were needed before the option showed up.
        assert mock_dependencies['driver'].swipe.call_count == 2
        for call in mock_dependencies['driver'].swipe.call_args_list:
            args = call.args
            assert args[0] == 100   # center_x = (0 + 200) // 2
            assert args[1] == 300   # center_y = (100 + 500) // 2
            assert args[2] == "up"
            assert args[3] == 200   # swipe_length = (500 - 100) * 0.5
        assert mock_press.call_args_list[0].args[0] == "Country"
        assert mock_press.call_args_list[-1].args[0] == "Zebra"

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

    def test_real_appium_capture_selects_already_visible_option(
        self, action_keyword, mock_dependencies, dropdown_pagesource,
    ):
        """Regression fixture: real before/after page-source dumps from a long dropdown.

        "Option 3" is already present in the post-open capture, so this exercises the
        fast path (no scrolling needed) while confirming real-world elements — including
        the dropdown trigger's own content-desc mutating from "▼" to "▲" between captures —
        don't confuse the lookup.
        """
        before = dropdown_pagesource["before"]

        with patch.object(
            action_keyword.strategy_manager, "get_interactive_elements",
            return_value=before,
        ), patch.object(
            action_keyword.verifier, "assert_visibility", return_value=True,
        ), patch.object(action_keyword, "press_element") as mock_press:
            action_keyword.select_dropdown_option("c22-dropdown", "Option 3", event_name="evt")

        assert mock_press.call_count == 2
        assert mock_press.call_args_list[0].args[0] == "c22-dropdown"
        assert mock_press.call_args_list[1].args[0] == "Option 3"
        mock_dependencies['driver'].swipe.assert_not_called()

    def test_real_appium_capture_identifies_scrollview_as_container(self, dropdown_pagesource):
        """The diff must pick the ScrollView, ignoring noise like the trigger's own mutation."""
        before = dropdown_pagesource["before"]
        after = dropdown_pagesource["after"]
        before_xpaths = {el["xpath"] for el in before}
        new_elements = [el for el in after if el["xpath"] not in before_xpaths]

        container = _find_dropdown_container(new_elements)

        assert container is not None
        assert container["xpath"] == '//android.widget.ScrollView[@resource-id="c22-options"]'
        assert container["bounds"] == {"x1": 48, "y1": 1257, "x2": 1032, "y2": 2217}


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
