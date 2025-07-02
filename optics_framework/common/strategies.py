from abc import ABC, abstractmethod
import inspect
from typing import List, Union, Tuple, Generator, Set, Optional
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils
from optics_framework.common.screenshot_stream import ScreenshotStream
from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common.execution_tracer import execution_tracer
from optics_framework.engines.vision_models.base_methods import match_and_annotate
import numpy as np
import time


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
        if not hasattr(element_source, method_name):
            # The method is not implemented at all
            return False
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

    def __init__(self, element_source: ElementSourceInterface, text_detection, strategy_manager):
        self._element_source = element_source
        self.text_detection = text_detection
        self.strategy_manager = strategy_manager

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str) -> Union[object, Tuple[int, int]]:
        screenshot = self.element_source.capture()
        _, coor, _ = self.text_detection.find_element(screenshot, element)
        return coor

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Tuple[bool, str]:
        end_time = time.time() + timeout
        found_status = dict.fromkeys(elements, False)
        result = False
        ss_stream = self.strategy_manager.capture_screenshot_stream(timeout=timeout)
        try:
            while time.time() < end_time:
                screenshot, timestamp = ss_stream.get_latest_screenshot(wait_time=1)
                if screenshot is None:
                    continue
                annotated_frame = screenshot.copy()
                _, ocr_results = self.text_detection.detect_text(annotated_frame)
                match_and_annotate(ocr_results, elements, found_status, annotated_frame)

                # Check rule
                if (rule == "any" and any(found_status.values())) or (rule == "all" and all(found_status.values())):
                    result = True
                    break
                else:
                    continue
                # time.sleep(0.3)
        finally:
            ss_stream.stop_capture()
        utils.save_screenshot(annotated_frame, "assert_elements_text_detection_result")
        return result, timestamp

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Text" and LocatorStrategy._is_method_implemented(element_source, "capture")


class ImageDetectionStrategy(LocatorStrategy):
    """Strategy for locating image elements using image detection."""

    def __init__(self, element_source: ElementSourceInterface, image_detection, strategy_manager):
        self._element_source = element_source
        self.image_detection = image_detection
        self.strategy_manager = strategy_manager

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str) -> Union[object, Tuple[int, int]]:
        screenshot = self.element_source.capture()
        _, centre, _ = self.image_detection.find_element(screenshot, element)
        return centre

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Tuple[bool, str]:
        end_time = time.time() + timeout
        result = False
        ss_stream = self.strategy_manager.capture_screenshot_stream(timeout=timeout)
        try:
            while time.time() < end_time:
                screenshot, timestamp = ss_stream.get_latest_screenshot(wait_time=1)
                if screenshot is None:
                    continue
                result, annotated_frame = self.image_detection.assert_elements(screenshot, elements, rule)
                if result:
                    break
        finally:
            ss_stream.stop_capture()
        if annotated_frame is not None:
            utils.save_screenshot(annotated_frame, "assert_elements_text_detection_result")
        return result, timestamp

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Image" and LocatorStrategy._is_method_implemented(element_source, "capture")

class PagesourceStrategy:
    def __init__(self, element_source: ElementSourceInterface):
        self.element_source = element_source

    def capture_pagesource(self) -> Optional[np.ndarray]:
        pagesource = self.element_source.get_page_source()
        if pagesource is not None:
            return pagesource
        raise ValueError("Invalid pagesource captured")

    def get_interactive_elements(self) -> List[dict]:
        """Retrieve interactive elements from the element source."""

        elements_dict = self.element_source.get_interactive_elements()
        if elements_dict is not None:
            return elements_dict
        raise NotImplementedError("Interactive elements retrieval failed.")

    @staticmethod
    def supports(element_source: ElementSourceInterface) -> bool:
        return LocatorStrategy._is_method_implemented(element_source, "get_page_source")


class ScreenshotStrategy:
    def __init__(self, element_source: ElementSourceInterface):
        self.element_source = element_source

    def capture(self) -> Optional[np.ndarray]:
        screenshot = self.element_source.capture()
        if screenshot is not None and not utils.is_black_screen(screenshot):
            return screenshot
        raise ValueError("Invalid screenshot captured")

    def capture_stream(self, timeout: int = 30):
        """Capture a screenshot stream from the element source."""
        if hasattr(self.element_source, "capture_stream"):
            return self.element_source.capture_stream(timeout=timeout)
        raise NotImplementedError("Element source does not support screenshot streaming.")

    @staticmethod
    def supports(element_source: ElementSourceInterface) -> bool:
        return LocatorStrategy._is_method_implemented(element_source, "capture")

class StrategyFactory:
    """Factory for creating locator strategies with priority ordering."""
    def __init__(self, text_detection, image_detection, strategy_manager):
        self.text_detection = text_detection
        self.image_detection = image_detection
        self.strategy_manager = strategy_manager
        self._registry = [
            (XPathStrategy, "XPath", {}, 1),
            (TextElementStrategy, "Text", {}, 2),
            (TextDetectionStrategy, "Text", {"text_detection": self.text_detection, "strategy_manager": self.strategy_manager}, 3),
            (ImageDetectionStrategy, "Image", {"image_detection": self.image_detection, "strategy_manager": self.strategy_manager}, 4),
        ]

    def create_strategies(self, element_source: ElementSourceInterface) -> List[LocatorStrategy]:
        strategies = [
            (cls(element_source, **args), priority)
            for cls, etype, args, priority in self._registry
            if cls.supports(etype, element_source)
        ]
        strategies.sort(key=lambda x: x[1])  # Sort by priority value
        return [strategy for strategy, _ in strategies]

class PagesourceFactory:
    def __init__(self):
        self._registry = [(PagesourceStrategy, {})]

    def create_strategies(self, element_source: ElementSourceInterface) -> List[PagesourceStrategy]:
        return [cls(element_source, **args) for cls, args in self._registry if cls.supports(element_source)]


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
        self.locator_factory = StrategyFactory(text_detection, image_detection, strategy_manager=self)
        self.screenshot_factory = ScreenshotFactory()
        self.pagesource_factory = PagesourceFactory()
        self.locator_strategies = self._build_locator_strategies()
        self.screenshot_strategies = self._build_screenshot_strategies()
        self.pagesource_strategies = self._build_pagesource_strategies()
        # for stream
        self.screenshot_stream = None

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

    def _build_pagesource_strategies(self) -> Set[PagesourceStrategy]:
        strategies = set()
        if isinstance(self.element_source, InstanceFallback):
            for instance in self.element_source.instances:
                strategies.update(
                    self.pagesource_factory.create_strategies(instance))
        else:
            strategies.update(
                self.pagesource_factory.create_strategies(self.element_source))
        return strategies

    def locate(self, element: str) -> Generator[LocateResult, None, None]:
        element_type = utils.determine_element_type(element)
        execution_logger.info(f"Locating element: {element} of type: {element_type}...")
        for strategy in self.locator_strategies:
            if strategy.supports(element_type, strategy.element_source):
                try:
                    result = strategy.locate(element)
                    if result:
                        execution_tracer.log_attempt(strategy, element, "success")
                        yield LocateResult(result, strategy)
                except Exception as e:
                    execution_tracer.log_attempt(strategy, element, "fail", error=str(e))
                    internal_logger.error(
                        f"Strategy {strategy.__class__.__name__} failed: {e}")

    def assert_presence(self, elements: list, element_type: str, timeout: int = 30, rule: str = 'any'):
        rule = rule.lower()
        if rule not in ("any", "all"):
            raise ValueError("Invalid rule. Use 'any' or 'all'.")
        execution_logger.info(
            f"Asserting presence of elements: {elements} with rule: {rule} and timeout: {timeout}s")
        for strategy in self.locator_strategies:
            if hasattr(strategy, 'assert_elements') and strategy.supports(element_type, strategy.element_source):
                try:
                    result, timestamp = strategy.assert_elements(elements, timeout, rule)
                    if result:
                        execution_tracer.log_attempt(strategy, str(elements), "success")
                        return result, timestamp
                    else:
                        execution_tracer.log_attempt(strategy, str(elements), "fail", error="Elements not found.")
                        internal_logger.debug(
                            f"Strategy {strategy.__class__.__name__} did not find elements: {elements}")
                except Exception as e:
                    execution_tracer.log_attempt(strategy, str(elements), "fail", error=str(e))
        return False, None

    def capture_screenshot(self) -> Optional[np.ndarray]:
        """Capture a screenshot using the available strategies."""
        execution_logger.info("Capturing screenshot using available strategies.")
        for strategy in self.screenshot_strategies:
            try:
                img = strategy.capture()
                execution_tracer.log_attempt(strategy, "screenshot", "success")
                return img
            except Exception as e:
                execution_tracer.log_attempt(strategy, "screenshot", "fail", error=str(e))
        internal_logger.error("No screenshot captured.")
        return None

    def capture_screenshot_stream(self, timeout: int = 30):
        """Capture a screenshot stream using the available strategies."""
        execution_logger.info("Starting screenshot stream with available strategies.")
        for strategy in self.screenshot_strategies:
            try:
                self.screenshot_stream = ScreenshotStream(strategy.capture, max_queue_size=10)
                self.screenshot_stream.start_capture(timeout, deduplication=True)
            except NotImplementedError as e:
                execution_logger.debug(
                    f"Screenshot streaming not supported by {strategy.__class__.__name__}: {e}")
            except Exception as e:
                execution_logger.error(
                    f"Screenshot streaming failed with {strategy.__class__.__name__}: {e}")
        return self.screenshot_stream

    def stop_screenshot_stream(self):
        if self.screenshot_stream:
            self.screenshot_stream.stop_capture()
            execution_logger.info("Screenshot stream stopped successfully.")
            self.screenshot_stream = None
        else:
            execution_logger.warning("No active screenshot stream to stop.")

    def capture_pagesource(self) -> Optional[str]:
        for strategy in self.pagesource_strategies:
            try:
                return strategy.capture_pagesource()
            except Exception as e:
                internal_logger.debug(
                    f"Pagesource capture failed with {strategy.__class__.__name__}: {e}")
        internal_logger.error("No pagesource captured.")
        return None

    def get_interactive_elements(self) -> List[dict]:
        """Retrieve interactive elements from the element source."""
        for strategy in self.pagesource_strategies:
            try:
                return strategy.get_interactive_elements()
            except Exception as e:
                internal_logger.debug(
                    f"Failed to retrieve interactive elements with {strategy.__class__.__name__}: {e}")
        internal_logger.error("No interactive elements retrieved.")
        return []
