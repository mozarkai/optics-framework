from unittest.mock import MagicMock
from optics_framework.api.app_management import AppManagement
from optics_framework.common.optics_builder import OpticsBuilder


def test_launch_app(mock_driver):
    class MockOpticsBuilder(OpticsBuilder):
        def get_driver(self):
            return mock_driver

    mock_event_sdk = MagicMock()
    app_management = AppManagement(MockOpticsBuilder(mock_event_sdk))

    # Call the method
    app_management.launch_app("launch")

    # Verify the driver was called correctly
    mock_driver.launch_app.assert_called_once_with(
        app_identifier="launch",
        app_activity=None,
        event_name=None
    )
