from abc import ABC, abstractmethod
from typing import Optional

class DriverInterface(ABC):
    """
    Abstract base class for application drivers.

    This interface enforces the implementation of essential methods
    for interacting with applications.
    """

    @abstractmethod
    def launch_app(
        self,
        app_identifier: str | None = None,
        app_activity: str | None = None,
        event_name: str | None = None,
    ) -> Optional[str]:
        """
        Launch an application.

        :param event_name: The event triggering the app launch.
        :type event_name: str
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: The session ID of the launched application, if available.
        :rtype: Optional[str]
        """
        pass

    @abstractmethod
    def launch_other_app(self, app_name: str, event_name: str | None) -> None:
        """
        Launch an application.

        :param event_name: The event triggering the app launch.
        :type event_name: str
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def get_app_version(self) -> str:
        """
        Get the version of the application.

        :return: The version of the application.
        :rtype: str
        """
        pass

    @abstractmethod
    def press_coordinates(self, coor_x: int, coor_y: int, event_name: Optional[str] = None) -> None:
        """
        Press an element by absolute coordinates.
        :param coor_x: X coordinate of the press.
        :param coor_y: Y coordinate of the press.
        :param repeat: Number of times to repeat the press.
        :param event_name: The event triggering the press.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def press_element(self, element: str, repeat: int, event_name: Optional[str] = None) -> None:
        """
        Press an element using Appium.
        :param element: The element to be pressed.
        :param event_name: The event triggering the press.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def press_percentage_coordinates(self, percentage_x: float, percentage_y: float, repeat: int, event_name: Optional[str] = None) -> None:
        """
        Press an element by percentage coordinates.
        :param percentage_x: X coordinate of the press.
        :param percentage_y: Y coordinate of the press.
        :param repeat: Number of times to repeat the press.
        :param event_name: The event triggering the press.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def enter_text(self, text: str, event_name: Optional[str] = None) -> None:
        """
        Enter text into an element.
        :param element: The element to receive the text.
        :param text: The text to be entered.
        :param event_name: The event triggering the text entry.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def press_keycode(self, keycode: str, event_name: Optional[str] = None) -> None:
        """
        Press a key code.
        :param keycode: The key code to be pressed.
        :param event_name: The event triggering the key press.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def enter_text_element(self, element: str, text: str, event_name: Optional[str] = None) -> None:
        """
        Enter text into an element.
        :param element: The element to receive the text.
        :param text: The text to be entered.
        :param event_name: The event triggering the text entry.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def enter_text_using_keyboard(self, text: str, event_name: Optional[str] = None) -> None:
        """
        Enter text using the keyboard.
        :param text: The text to be entered.
        :param event_name: The event triggering the text entry.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def clear_text(self, event_name: Optional[str] = None) -> None:
        """
        Clear text from an element.
        :param element: The element to receive the text.
        :param event_name: The event triggering the text clearing.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def clear_text_element(self, element: str, event_name: Optional[str] = None) -> None:
        """
        Clear text from an element.
        :param element: The element to receive the text.
        :param event_name: The event triggering the text clearing.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def swipe(self, x_coor: int, y_coor: int, direction: str, swipe_length: int, event_name: Optional[str] = None) -> None:
        """
        Swipe in a specified direction.
        :param x_coor: The starting x coordinate of the swipe.
        :param y_coor: The starting y coordinate of the swipe.
        :param direction: The direction of the swipe.
        :param swipe_percentage: The percentage of the screen to swipe.
        :param event_name: The event triggering the swipe.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def swipe_percentage(self, x_percentage: float, y_percentage: float, direction: str, swipe_percentage: float, event_name: Optional[str] = None) -> None:
        """
        Swipe in a specified direction by percentage.
        :param x_percentage: The starting x coordinate of the swipe as a percentage of the screen width.
        :param y_percentage: The starting y coordinate of the swipe as a percentage of the screen height.
        :param direction: The direction of the swipe.
        :param swipe_percentage: The percentage of the screen to swipe.
        :param event_name: The event triggering the swipe.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def swipe_element(self, element: str, direction: str, swipe_length: int, event_name: Optional[str] = None) -> None:
        """
        Swipe an element in a specified direction.
        :param element: The element to be swiped.
        :param direction: The direction of the swipe.
        :param duration: The duration of the swipe.
        :param event_name: The event triggering the swipe.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def scroll(self, direction: str, duration: int, event_name: Optional[str] = None) -> None:
        """
        Scroll in a specified direction.
        :param direction: The direction of the scroll.
        :param duration: The duration of the scroll.
        :param event_name: The event triggering the scroll.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def get_text_element(self, element: str) -> str:
        """
        Get the text of an element.
        :param element: The element to get the text from.
        :return: The text of the element.
        :rtype: str
        """
        pass

    @abstractmethod
    def force_terminate_app(self, app_name: str, event_name: Optional[str] = None) -> None:
        """
        Forcefully terminates the specified application.
        :param app_name: The name of the application to terminate.
        :param event_name: The event triggering the forced termination, if any.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def terminate(self) -> None:
        """
        Terminate the application.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def get_driver_session_id(self) -> Optional[str]:
        """
        Return the underlying Appium session ID if supported by the driver.

        For non-Appium drivers, raise NotImplementedError.
        :return: The Appium session ID if available, otherwise None.
        :rtype: Optional[str]
        """
        pass
