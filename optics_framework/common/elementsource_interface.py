from abc import ABC, abstractmethod
from typing import Any, Optional, List, Tuple
import numpy

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
    def capture(self) -> numpy.ndarray:
        """
        Capture the current screen state.

        :return: None
        :rtype: None
        """
        pass

    def capture_screenshot_bytes(self) -> bytes:
        """
        Return the current screen as already-encoded image bytes (e.g. PNG/JPEG).

        Optional fast-path companion to :meth:`capture`. A backend that can return the
        device's native encoded screenshot should override this; callers then skip the
        decode-to-numpy and re-encode round-trip. Otherwise they fall back to encoding
        :meth:`capture`'s frame.

        :raises NotImplementedError: if this element source has no native encoded path.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support capture_screenshot_bytes"
        )

    @abstractmethod
    def locate(self, element: Any, index: int | None = None) -> tuple:
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

    def get_element_bboxes(
        self, elements: list
    ) -> List[Optional[Tuple[Tuple[int, int], Tuple[int, int]]]]:
        """
        Return bounding boxes for each element in pixel coordinates.

        :param elements: List of element identifiers (e.g., XPath, text).
        :return: For each element, ((x1,y1), (x2,y2)) or None if not available.
        """
        return [None] * len(elements)

    def get_bbox_for_element(
        self, element: Any
    ) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """
        Return bounding box for an already-located element in screenshot coordinates.

        :param element: An already-located element (WebElement, Playwright handle, etc.).
        :return: ((x1,y1), (x2,y2)) or None if not available.
        """
        return None

    def get_page_source(self) -> Tuple[str, str]:
        """
        Get the page source and timestamp of the current screen.
        Optional: raise NotImplementedError if this element source does not support it.

        :return: Tuple of (page_source, timestamp).
        :rtype: Tuple[str, str]
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} does not support get_page_source"
        )

    @abstractmethod
    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> list:
        """
        Retrieve a list of interactive elements on the current screen.

        :param filter_config: Optional list of filter types (e.g., ["buttons", "inputs"]).
        :type filter_config: Optional[List[str]]
        :return: A list of interactive elements (e.g., buttons, links).
        :rtype: list
        """
        pass
