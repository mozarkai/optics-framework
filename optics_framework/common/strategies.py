from abc import ABC, abstractmethod
import inspect
from typing import List, Union, Tuple, Generator, Set, Optional
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils
from optics_framework.common.logging_config import internal_logger
import numpy as np


class LocatorStrategy(ABC):
    """Abstract base class for element location strategies."""

    @property
    @abstractmethod
    def element_source(self) -> ElementSourceInterface:
        pass

    @abstractmethod
    def locate(self, element: str) -> Union[object, Tuple[int, int]]:
        """Locates an element and returns either an element object or coordinates (x, y).

        :param element: The element identifier (e.g., XPath, text, image path).
        :return: Either an element object or a tuple of (x, y) coordinates.
        """
        pass

    @abstractmethod
    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Union[bool, None]:
        """Asserts the presence of elements using the locator strategy.

        :param elements: List of element identifiers (e.g., XPath, text, image path).
        :param timeout: Maximum time to wait in seconds (default: 30).
        :param rule: 'any' or 'all' to specify if any or all elements must be present (default: 'any').
        :return: True if the assertion is successful, False otherwise.
        """
        pass

    @staticmethod
    @abstractmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        """Determines if this strategy supports the given element type and source.

        :param element_type: The type of element (e.g., 'XPath', 'Text', 'Image').
        :param element_source: The source to check compatibility with.
        :return: True if supported, False otherwise.
        """
        pass

    @staticmethod
    def _is_method_implemented(element_source: ElementSourceInterface, method_name: str) -> bool:
        """Checks if the method is implemented and not a stub.

        :param element_source: The source to inspect.
        :param method_name: The name of the method to check.
        :return: True if implemented, False if abstract or a stub.
        """
        method = getattr(element_source, method_name)
        if inspect.isabstract(method):
            return False
        try:
            source = inspect.getsource(method)
            return "raise NotImplementedError" not in source
        except (OSError, TypeError):
            return True


class XPathStrategy(LocatorStrategy):
    """Strategy for locating elements via XPath."""

    def __init__(self, element_source: ElementSourceInterface):
        self._element_source = element_source

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str) -> Union[object, Tuple[int, int]]:
        return self.element_source.locate(element)

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Union[bool, None]:
        return self.element_source.assert_elements(elements, timeout, rule)

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "XPath" and LocatorStrategy._is_method_implemented(element_source, "locate")


class TextElementStrategy(LocatorStrategy):
    """Strategy for locating text elements directly via the element source."""

    def __init__(self, element_source: ElementSourceInterface):
        self._element_source = element_source

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str) -> Union[object, Tuple[int, int]]:
        return self.element_source.locate(element)

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Union[bool, None]:
        return self.element_source.assert_elements(elements, timeout, rule)

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Text" and LocatorStrategy._is_method_implemented(element_source, "locate")

class TextDetectionStrategy(LocatorStrategy):
    """Strategy for locating text elements using text detection."""

    def __init__(self, element_source: ElementSourceInterface, text_detection):
        self._element_source = element_source
        self.text_detection = text_detection

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str) -> Union[object, Tuple[int, int]]:
        screenshot = self.element_source.capture()
        _, coor, _ = self.text_detection.find_element(screenshot, element)
        return coor

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Union[bool, None]:
        screenshot = self.element_source.capture()
        result = self.text_detection.assert_elements(screenshot, elements, timeout, rule)
        return result
    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Text" and LocatorStrategy._is_method_implemented(element_source, "capture")


class ImageDetectionStrategy(LocatorStrategy):
    """Strategy for locating image elements using image detection."""

    def __init__(self, element_source: ElementSourceInterface, image_detection):
        self._element_source = element_source
        self.image_detection = image_detection

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str) -> Union[object, Tuple[int, int]]:
        screenshot = self.element_source.capture()
        _, centre, _ = self.image_detection.find_element(screenshot, element)
        return centre

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Union[bool, None]:
        screenshot = self.element_source.capture()
        result = self.image_detection.assert_elements(screenshot, elements, timeout, rule)
        return result

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Image" and LocatorStrategy._is_method_implemented(element_source, "capture")


class ScreenshotStrategy:
    def __init__(self, element_source: ElementSourceInterface):
        self.element_source = element_source

    def capture(self) -> Optional[np.ndarray]:
        screenshot = self.element_source.capture()
        if screenshot is not None and not utils.is_black_screen(screenshot):
            return screenshot
        raise ValueError("Invalid screenshot captured")

    @staticmethod
    def supports(element_source: ElementSourceInterface) -> bool:
        return LocatorStrategy._is_method_implemented(element_source, "capture")

class StrategyFactory:
    """Factory for creating locator strategies with priority ordering."""
    def __init__(self, text_detection, image_detection):
        self.text_detection = text_detection
        self.image_detection = image_detection
        self._registry = [
            (XPathStrategy, "XPath", {}, 1),
            (TextElementStrategy, "Text", {}, 2),
            (TextDetectionStrategy, "Text", {"text_detection": self.text_detection}, 3),
            (ImageDetectionStrategy, "Image", {"image_detection": self.image_detection}, 4),
        ]

    def create_strategies(self, element_source: ElementSourceInterface) -> List[LocatorStrategy]:
        strategies = [
            (cls(element_source, **args), priority)
            for cls, etype, args, priority in self._registry
            if cls.supports(etype, element_source)
        ]
        strategies.sort(key=lambda x: x[1])  # Sort by priority value
        return [strategy for strategy, _ in strategies]

class ScreenshotFactory:
    def __init__(self):
        self._registry = [(ScreenshotStrategy, {})]

    def create_strategies(self, element_source: ElementSourceInterface) -> List[ScreenshotStrategy]:
        return [cls(element_source, **args) for cls, args in self._registry if cls.supports(element_source)]


class LocateResult:
    """Wrapper for location results from a strategy."""

    def __init__(self, value: Union[object, Tuple[int, int]], strategy: LocatorStrategy):
        self.value = value
        self.strategy = strategy
        self.is_coordinates = isinstance(value, tuple)


class StrategyManager:
    def __init__(self, element_source: ElementSourceInterface, text_detection, image_detection):
        self.element_source = element_source
        self.locator_factory = StrategyFactory(text_detection, image_detection)
        self.screenshot_factory = ScreenshotFactory()
        self.locator_strategies = self._build_locator_strategies()
        self.screenshot_strategies = self._build_screenshot_strategies()

    def _build_locator_strategies(self) -> List[LocatorStrategy]:
        strategies = []
        if isinstance(self.element_source, InstanceFallback):
            for instance in self.element_source.instances:
                strategies.extend(
                    self.locator_factory.create_strategies(instance))
        else:
            strategies.extend(
                self.locator_factory.create_strategies(self.element_source))
        return strategies


    def _build_screenshot_strategies(self) -> Set[ScreenshotStrategy]:
        strategies = set()
        if isinstance(self.element_source, InstanceFallback):
            for instance in self.element_source.instances:
                strategies.update(
                    self.screenshot_factory.create_strategies(instance))
        else:
            strategies.update(
                self.screenshot_factory.create_strategies(self.element_source))
        return strategies

    def locate(self, element: str) -> Generator[LocateResult, None, None]:
        element_type = utils.determine_element_type(element)
        for strategy in self.locator_strategies:
            if strategy.supports(element_type, strategy.element_source):
                try:
                    result = strategy.locate(element)
                    if result:
                        yield LocateResult(result, strategy)
                except Exception as e:
                    internal_logger.error(
                        f"Strategy {strategy.__class__.__name__} failed: {e}")

    def assert_presence(self, elements: list, element_type: str, timeout: int = 30, rule: str = 'any'):
        """Asserts the presence of an element using the locator strategies.

        :param elements: The element identifier (e.g., XPath, text, image path).
        :param timeout: Maximum time to wait in seconds (default: 30).
        :param rule: 'any' or 'all' to specify if any or all elements must be present (default: 'any').
        :return: True if the assertion is successful, False otherwise.
        """
        for strategy in self.locator_strategies:
            if hasattr(strategy, 'assert_elements') and strategy.supports(element_type, strategy.element_source):
                try:
                    result = strategy.assert_elements(elements, timeout, rule)
                    if result:
                        return result
                except Exception as e:
                    internal_logger.error(
                        f"Strategy {strategy.__class__.__name__} failed: {e}")

        return False

    def capture_screenshot(self) -> Optional[np.ndarray]:
        for strategy in self.screenshot_strategies:
            try:
                return strategy.capture()
            except Exception as e:
                internal_logger.debug(
                    f"Screenshot failed with {strategy.__class__.__name__}: {e}")
        internal_logger.error("No screenshot captured.")
        return None
