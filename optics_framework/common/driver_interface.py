from abc import ABC, abstractmethod
from typing import Optional, Any, Dict

class DriverInterface(ABC):
    """
    Abstract base class for application drivers.

    This interface enforces the implementation of essential methods
    for interacting with applications.
    """

    #: Session-migration capability gate (stateless design §6). A driver that
    #: keeps its backend session alive server-side and can attach a fresh
    #: client to it declares True and implements ``get_reattach_params()``,
    #: ``reattach()`` and a non-terminating ``detach()``. Drivers that hold
    #: in-process state (subprocess, BLE/USB handle, in-memory object) keep
    #: the default: their live sessions are sticky and never migrated.
    supports_session_migration: bool = False

    def get_reattach_params(self) -> Dict[str, Any]:
        """
        Return driver-defined parameters another instance needs to reattach to
        the live backend session (e.g. ``reattach_handle``, ``endpoint``,
        ``capabilities``, ``device_id``).

        Only meaningful when ``supports_session_migration`` is True.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support session migration"
        )

    def reattach(self, reattach_params: Dict[str, Any], strict: bool = True) -> None:
        """
        Attach this driver to an existing backend session described by
        ``reattach_params`` instead of launching a fresh one.

        :param reattach_params: The dict produced by ``get_reattach_params()``.
        :param strict: When True (the reconstruction path), a failed reattach
            must raise — never silently fall back to creating a new session.
            The lenient mode is reserved for genuine first-launch flows.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support session migration"
        )

    def detach(self) -> None:
        """
        Drop the local client handle while keeping the backend session alive.

        Conservative default for drivers that cannot separate the two:
        detaching terminates, which is the correct behavior for a sticky
        (non-migratable) session.
        """
        self.terminate()

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
        :param swipe_length: The length of the swipe in pixels.
        :param event_name: The event triggering the swipe.
        :raises NotImplementedError: If the method is not implemented in a subclass.
        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def swipe_percentage(self, x_percentage: int, y_percentage: int, direction: str, swipe_length_percentage: int, event_name: Optional[str] = None) -> None:
        """
        Swipe in a specified direction by percentage.
        :param x_percentage: The starting x coordinate of the swipe as a percentage of the screen width (0-100).
        :param y_percentage: The starting y coordinate of the swipe as a percentage of the screen height (0-100).
        :param direction: The direction of the swipe.
        :param swipe_length_percentage: The percentage of the screen to swipe (0-100).
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

    @abstractmethod
    def execute_script(self, script: str, *args, event_name: Optional[str] = None) -> Any:
        """
        Execute JavaScript/script in the current context.

        :param script: The JavaScript code or script command to execute.
        :type script: str
        :param *args: Optional arguments to pass to the script.
        :param event_name: The event triggering the script execution, if any.
        :type event_name: Optional[str]
        :return: The result of the script execution.
        :rtype: Any
        :raises NotImplementedError: If the method is not implemented in a subclass.
        """
        pass
