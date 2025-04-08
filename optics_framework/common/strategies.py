from abc import ABC, abstractmethod
import inspect
from typing import List, Union, Tuple, Generator, Set
from optics_framework.common.base_factory import InstanceFallback  # Updated import
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils
from optics_framework.common.logging_config import internal_logger


class LocatorStrategy(ABC):
    """Abstract base class for element location strategies."""

    @property
    @abstractmethod
    def element_source(self) -> ElementSourceInterface:
        """Returns the element source this strategy operates on."""
        pass

    @abstractmethod
    def locate(self, element: str) -> Union[object, Tuple[int, int]]:
        """Locates an element and returns either an element object or coordinates (x, y).

        :param element: The element identifier (e.g., XPath, text, image path).
        :return: Either an element object or a tuple of (x, y) coordinates.
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
        return self.text_detection.locate(screenshot, element)

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
        return self.image_detection.locate(screenshot, element)

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Image" and LocatorStrategy._is_method_implemented(element_source, "capture")


class StrategyFactory:
    """Factory for creating locator strategies."""

    def __init__(self, text_detection, image_detection):
        self.text_detection = text_detection
        self.image_detection = image_detection
        self._strategy_registry = [
            (XPathStrategy, "XPath", {}),
            (TextElementStrategy, "Text", {}),
            (TextDetectionStrategy, "Text", {
             "text_detection": self.text_detection}),
            (ImageDetectionStrategy, "Image", {
             "image_detection": self.image_detection}),
        ]

    def create_strategies(self, element_source: ElementSourceInterface) -> List[LocatorStrategy]:
        """Creates strategies compatible with the given element source.

        :param element_source: The source to build strategies for.
        :return: List of compatible strategy instances.
        """
        strategies = []
        for strategy_cls, element_type, extra_args in self._strategy_registry:
            if strategy_cls.supports(element_type, element_source):
                strategies.append(strategy_cls(element_source, **extra_args))
        return strategies


class LocateResult:
    """Wrapper for location results from a strategy."""

    def __init__(self, value: Union[object, Tuple[int, int]], strategy: LocatorStrategy):
        self.value = value
        self.strategy = strategy
        self.is_coordinates = isinstance(value, tuple)


class StrategyManager:
    """Manages multiple locator strategies for element location."""

    def __init__(self, element_source: ElementSourceInterface, text_detection, image_detection):
        self.element_source = element_source
        self.factory = StrategyFactory(text_detection, image_detection)
        self.strategies = self._build_strategies()
        internal_logger.debug(
            f"Built strategies: {[s.__class__.__name__ for s in self.strategies]}")

    def _build_strategies(self) -> Set[LocatorStrategy]:
        """Builds a set of all strategies from the element source.

        :return: Set of strategy instances.
        :raises ValueError: If no strategies are available.
        """
        all_strategies: Set[LocatorStrategy] = set()
        if isinstance(self.element_source, InstanceFallback):  # Updated to InstanceFallback
            for instance in self.element_source.instances:
                all_strategies.update(self.factory.create_strategies(instance))
        else:
            all_strategies.update(
                self.factory.create_strategies(self.element_source))

        if not all_strategies:
            raise ValueError(
                "No strategies available for the given element source")
        return all_strategies

    def locate(self, element: str) -> Generator[LocateResult, None, None]:
        """Yields applicable strategies' results in order of attempt.

        :param element: The element identifier to locate.
        :yields: LocateResult objects with location data and strategy used.
        """
        element_type = utils.determine_element_type(element)
        applicable_strategies = {
            s for s in self.strategies if s.supports(element_type, s.element_source)}

        for strategy in applicable_strategies:
            try:
                result = strategy.locate(element)
                if result:
                    yield LocateResult(result, strategy)
            except Exception as e:
                internal_logger.error(
                    f"Strategy {strategy.__class__.__name__} failed: {e}")
