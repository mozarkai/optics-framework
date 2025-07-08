from abc import ABC, abstractmethod


class DriverInterface(ABC):
    """
    Abstract base class for application drivers.

    This interface enforces the implementation of essential methods
    for interacting with applications.
    """

    @abstractmethod
    def launch_app(self, event_name: str | None) -> None:
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
    def press_coordinates(self, coor_x, coor_y, event_name) -> None:
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
    def press_element(self, element, repeat, event_name) -> None:
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
    def press_percentage_coordinates(self, percentage_x, percentage_y, repeat, event_name=None) -> None:
        """
        Press an element by percentage coordinates.
        :param percentage_x: X coordinate of the press.
        :param
        percentage_y: Y coordinate of the press.
        :param repeat: Number of times to repeat the press.
        :param event_name: The event triggering the press.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def enter_text(self, text, event_name) -> None:
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
    def press_keycode(self, keycode, event_name) -> None:
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
    def enter_text_element(self, element, text, event_name) -> None:
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
    def enter_text_using_keyboard(self, text, event_name) -> None:
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
    def clear_text(self, event_name) -> None:
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
    def clear_text_element(self, element, event_name) -> None:
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
    def swipe(self, x_coor, y_coor, direction, swipe_length, event_name) -> None:
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
    def swipe_percentage(self, x_percentage, y_percentage, direction, swipe_percentage, event_name) -> None:
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
    def swipe_element(self, element, direction, swipe_length, event_name) -> None:
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
    def scroll(self, direction, duration, event_name) -> None:
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
    def get_text_element(self, element) -> str:
        """
        Get the text of an element.
        :param element: The element to get the text from.
        :return: The text of the element.
        :rtype: str
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
