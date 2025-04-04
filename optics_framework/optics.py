import os
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.config_handler import ConfigHandler
from optics_framework.api.app_management import AppManagement
from optics_framework.api.action_keyword import ActionKeyword
from optics_framework.api.verifier import Verifier


class OpticsInstance:
    """
    A class that provides an interface to interact with the optics framework,
    encapsulating application management, action keywords, and verification.
    """

    def __init__(self, app_management, action_keyword, verifier):
        self.app_management = app_management
        self.action_keyword = action_keyword
        self.verifier = verifier

    ### AppManagement Methods ###
    def launch_app(self, event_name=None):
        """Launch the application, optionally with an event name."""
        return self.app_management.launch_app(event_name)

    def start_other_app(self, package_name, event_name=None):
        """Launch another application by package name."""
        return self.app_management.start_other_app(package_name, event_name)

    def close_and_terminate_app(self, package_name, event_name=None):
        """Close and terminate an application by package name."""
        return self.app_management.close_and_terminate_app(package_name, event_name)

    def force_terminate_app(self, event_name=None):
        """Forcefully terminate the current application."""
        return self.app_management.force_terminate_app(event_name)

    ### ActionKeyword Methods ###
    def press_element(self, element, time=0.3, repeat=1, offset_x=0, offset_y=0, event_name=None):
        """Press an element with specified parameters."""
        return self.action_keyword.press_element(element, time, repeat, offset_x, offset_y, event_name)

    def press_by_percentage(self, percent_x, percent_y, repeat=1, time=0.3, event_name=None):
        """Press at a percentage of the screen's width and height."""
        return self.action_keyword.press_by_percentage(percent_x, percent_y, repeat, time, event_name)

    def press_by_coordinates(self, coor_x, coor_y, time=0.5, repeat=1, event_name=None):
        """Press at specific coordinates on the screen."""
        return self.action_keyword.press_by_coordinates(coor_x, coor_y, time, repeat, event_name)

    def press_element_with_index(self, element, index, time=0.3, repeat=1, event_name=None):
        """Press an element at a specific index among matching elements."""
        return self.action_keyword.press_element_with_index(element, index, time, repeat, event_name)

    def press_checkbox(self, element, state=True, event_name=None):
        """Toggle a checkbox to the specified state."""
        return self.action_keyword.press_checkbox(element, state, event_name)

    def press_radio_button(self, element, event_name=None):
        """Select a radio button."""
        return self.action_keyword.press_radio_button(element, event_name)

    def select_dropdown_option(self, element, option, event_name=None):
        """Select an option from a dropdown by value or text."""
        return self.action_keyword.select_dropdown_option(element, option, event_name)

    def swipe(self, coor_x, coor_y, direction='right', swipe_length=50, event_name=None):
        """Perform a swipe gesture from coordinates in a direction."""
        return self.action_keyword.swipe(coor_x, coor_y, direction, swipe_length, event_name)

    def swipe_until_element_appears(self, element, direction='down', max_attempts=5, event_name=None):
        """Swipe until an element appears or max attempts are reached."""
        return self.action_keyword.swipe_until_element_appears(element, direction, max_attempts, event_name)

    def swipe_from_element(self, element, direction='right', swipe_length=50, event_name=None):
        """Swipe starting from an element in a direction."""
        return self.action_keyword.swipe_from_element(element, direction, swipe_length, event_name)

    def scroll(self, direction='down', distance=100, event_name=None):
        """Perform a scroll gesture in a direction."""
        return self.action_keyword.scroll(direction, distance, event_name)

    def scroll_until_element_appears(self, element, direction='down', max_attempts=5, event_name=None):
        """Scroll until an element appears or max attempts are reached."""
        return self.action_keyword.scroll_until_element_appears(element, direction, max_attempts, event_name)

    def scroll_from_element(self, element, direction='down', distance=100, event_name=None):
        """Scroll starting from an element in a direction."""
        return self.action_keyword.scroll_from_element(element, direction, distance, event_name)

    def enter_text(self, element, text, event_name=None):
        """Enter text into an element."""
        return self.action_keyword.enter_text(element, text, event_name)

    def enter_text_using_keyboard(self, text, event_name=None):
        """Enter text using the virtual keyboard."""
        return self.action_keyword.enter_text_using_keyboard(text, event_name)

    def number(self, element, number_value, event_name=None):
        """Input a numeric value into an element."""
        return self.action_keyword.number(element, number_value, event_name)

    def clear_element_text(self, element, event_name=None):
        """Clear text from an element."""
        return self.action_keyword.clear_element_text(element, event_name)

    ### Verifier Methods ###
    def assert_presence(self, elements, timeout=30, rule='any', event_name=None):
        """Assert the presence of elements with specified conditions."""
        return self.verifier.assert_presence(elements, timeout, rule, event_name)

    def validate_element(self, element, timeout=10, rule="all", event_name=None):
        """Validate properties of an element within a timeout."""
        return self.verifier.validate_element(element, timeout, rule, event_name)

    def validate_screen_strict(self, screen, timeout=10, rule="all", event_name=None):
        """Strictly validate the entire screen against expected conditions."""
        return self.verifier.validate_screen_strict(screen, timeout, rule, event_name)

    def is_element(self, element, element_state, timeout=10, event_name=None):
        """Check if an element is in a specific state (e.g., visible, enabled)."""
        return self.verifier.is_element(element, element_state, timeout, event_name)

    def assert_equality(self, output, expression, event_name=None):
        """Assert that an output matches an expected expression."""
        return self.verifier.assert_equality(output, expression, event_name)

def setup(yaml_path: str | None = None, **kwargs):
    """
    Initialize the optics framework with specified drivers and sources, optionally using a YAML config file.

    Args:
        yaml_path (str, optional): Path to a YAML configuration file. If provided, sets the project path and loads the config.
        **kwargs: Additional configuration parameters (e.g., appium_param, app_param) to override or supplement the YAML config.

    Returns:
        OpticsInstance: An instance to interact with the framework.
    """
    config_handler = ConfigHandler.get_instance()
    if yaml_path:
        # Set the project path to the directory containing the YAML file and load it
        project_path = os.path.dirname(os.path.abspath(yaml_path))
        config_handler.set_project(project_path)
        config_handler.load()
    # Access the current config (loaded from YAML or defaults)
    config = config_handler.config

    # Update config with additional kwargs
    config.update(kwargs)

    driver = config.get("driver_sources")
    element_source = config.get("elements_sources")
    image = config.get("image_detection")
    text = config.get("text_detection")

    if driver is None or element_source is None or image is None or text is None:
        internal_logger.error(
            "No driver, element source, or image source found in the configuration.")
        return None  # Return None to indicate failure

    app_management = AppManagement(driver)
    action_keyword = ActionKeyword(driver, element_source, image, text)
    verifier = Verifier(driver, element_source, image, text)
    return OpticsInstance(app_management, action_keyword, verifier)
