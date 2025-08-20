import pytest


def test_launch_app(mock_driver):
    mock_driver.launch_app(event_name="test_event")


def test_invalid_event(mock_driver):
    with pytest.raises(ValueError, match="Event name cannot be empty."):
        mock_driver.launch_app(event_name="")
