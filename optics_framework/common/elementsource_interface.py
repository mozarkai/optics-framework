from abc import ABC, abstractmethod
from typing import Any


class ElementSourceInterface(ABC):
    """
    Abstract base class for element source drivers.

    This interface defines methods for capturing and interacting with screen elements
    (e.g., images, UI components) within an application or environment, implementing
    the :class:`ElementSourceInterface`.

    Implementers should handle specific element types (e.g., image bytes, templates)
    as needed.
    """

    @abstractmethod
    def capture(self) -> None:
        """
        Capture the current screen state.

        :return: None
        :rtype: None
        """
        pass

    @abstractmethod
    def locate(self, element, index=None) -> tuple:
        """
        Locate an element within the current screen state.

        :param element: The element to search for (e.g., template image, UI component).
        :type element: Any
        :return: A tuple (x, y) representing the center of the element, or None if not found.
        :rtype: Optional[Tuple[int, int]]
        """
        pass

    @abstractmethod
    def assert_elements(self, elements: Any, timeout: int = 30, rule: str = 'any') -> None:
        """
        Assert the presence of elements on the screen.

        :param elements: The elements to check for presence (e.g., list of templates).
        :type elements: Any
        :param timeout: Time in seconds to wait for elements to appear (default: 30).
        :type timeout: int
        :param rule: Assertion rule ('any' for at least one, 'all' for all; default: 'any').
        :type rule: str
        :return: None
        :rtype: None
        :raises AssertionError: If the assertion fails based on the rule.
        """
        pass

    @abstractmethod
    def get_interactive_elements(self) -> list:
        """
        Retrieve a list of interactive elements on the current screen.

        :return: A list of interactive elements (e.g., buttons, links).
        :rtype: list
        """
        pass
