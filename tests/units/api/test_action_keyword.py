import pytest
from unittest.mock import MagicMock, patch
import tempfile
import numpy as np

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

        # Add dummy event_sdk attribute
        self.event_sdk = MagicMock()

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
