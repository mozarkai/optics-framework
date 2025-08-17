import pytest
from optics_framework.common.driver_interface import DriverInterface

class ConcreteTestDriver(DriverInterface):
    def clear_text(self, event_name=None):
        # Stub for testing
        pass
    def clear_text_element(self, element=None, event_name=None):
        # Stub for testing
        pass
    def enter_text(self, text=None, event_name=None):
        # Stub for testing
        pass
    def enter_text_element(self, element=None, text=None, event_name=None):
        # Stub for testing
        pass
    def enter_text_using_keyboard(self, text=None, event_name=None):
        # Stub for testing
        pass
    def force_terminate_app(self, app_name=None, event_name=None):
        # Stub for testing
        pass
    def get_app_version(self, app_name=None, event_name=None) -> str:
        # Stub for testing
        return ""
    def get_text_element(self, element=None, event_name=None) -> str:
        # Stub for testing
        return ""
    def launch_other_app(self, app_name=None, event_name=None):
        # Stub for testing
        pass
    def press_coordinates(self, coor_x=None, coor_y=None, event_name=None):
        # Stub for testing
        pass
    def press_element(self, element=None, repeat=None, event_name=None):
        # Stub for testing
        pass
    def press_keycode(self, keycode=None, event_name=None):
        # Stub for testing
        pass
    def press_percentage_coordinates(self, percentage_x=None, percentage_y=None, repeat=None, event_name=None):
        # Stub for testing
        pass
    def scroll(self, direction=None, duration=None, event_name=None):
        # Stub for testing
        pass
    def swipe(self, x_coor=None, y_coor=None, direction=None, swipe_length=None, event_name=None):
        # Stub for testing
        pass
    def swipe_element(self, element=None, direction=None, swipe_length=None, event_name=None):
        # Stub for testing
        pass
    def swipe_percentage(self, x_percentage=None, y_percentage=None, direction=None, swipe_percentage=None, event_name=None):
        # Stub for testing
        pass
    def terminate(self, event_name=None):
        # Stub for testing
        pass
    def launch_app(self, app_identifier=None, app_activity=None, event_name=None):
        # Simulate launching app, raise ValueError for test if event_name is empty
        if not event_name:
            raise ValueError("Event name cannot be empty.")
        # No return value to match base interface
        pass


def test_launch_app():
    driver = ConcreteTestDriver()
    # Should not raise error for valid event_name
    driver.launch_app(event_name="test_event")


def test_invalid_event():
    driver = ConcreteTestDriver()
    with pytest.raises(ValueError, match="Event name cannot be empty."):
        driver.launch_app("")
