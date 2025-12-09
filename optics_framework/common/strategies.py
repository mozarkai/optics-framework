from abc import ABC, abstractmethod
import inspect
import time
import math
from typing import List, Union, Tuple, Generator, Set, Optional
import numpy as np
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils
from optics_framework.common.screenshot_stream import ScreenshotStream
from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common.execution_tracer import execution_tracer
from optics_framework.engines.vision_models.base_methods import match_and_annotate
from optics_framework.common.error import OpticsError, Code


class LocatorStrategy(ABC):
    """Abstract base class for element location strategies."""

    @property
    @abstractmethod
    def element_source(self) -> ElementSourceInterface:
        pass

    @abstractmethod
    def locate(self, element: str, index: int = 0) -> Union[object, Tuple[int, int]]:
        """Locates an element and returns either an element object or coordinates (x, y).

        :param element: The element identifier (e.g., XPath, text, image path).
        :param index: The index of the element if multiple matches are found.
        :return: Either an element object or a tuple of (x, y) coordinates.
        """
        pass

    @abstractmethod
    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Tuple[bool, Optional[str]]:
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
            # Check if the method is just a stub (entire method body is just raise NotImplementedError)
            lines = [line.strip() for line in source.split('\n') if line.strip() and not line.strip().startswith('#')]
            # Filter out docstrings and function definition
            body_lines = []
            in_docstring = False
            for line in lines:
                if line.startswith('def '):
                    continue
                if '"""' in line or "'''" in line:
                    in_docstring = not in_docstring
                    continue
                if not in_docstring and not line.startswith('"""') and not line.startswith("'''"):
                    body_lines.append(line)

            # If any body line raises NotImplementedError, it's not implemented
            if any("raise NotImplementedError" in line for line in body_lines):
                return False
            return True
        except (OSError, TypeError):
            return True

class XPathStrategy(LocatorStrategy):
    """Strategy for locating elements via XPath."""

    def __init__(self, element_source: ElementSourceInterface):
        self._element_source = element_source

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str, index: int = 0) -> Union[object, Tuple[int, int]]:
        return self.element_source.locate(element, index)

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Tuple[bool, Optional[str]]:
        try:
            result, timestamp = self.element_source.assert_elements(elements, timeout, rule)
            return result, timestamp, None
        except Exception:
            return False, None, None

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

    def locate(self, element: str, index: int = 0) -> Union[object, Tuple[int, int]]:
        return self.element_source.locate(element, index)

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Tuple[bool, Optional[str]]:
        try:
            found, timestamp = self.element_source.assert_elements(elements, timeout, rule)
            return found, timestamp, None # returning None since there's no annotation available
        except Exception:
            return False, None, None

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Text" and LocatorStrategy._is_method_implemented(element_source, "locate")

class TextDetectionStrategy(LocatorStrategy):
    """Strategy for locating text elements using text detection."""

    def __init__(self, element_source: ElementSourceInterface, text_detection, strategy_manager):
        self._element_source = element_source
        self.text_detection = text_detection
        self.strategy_manager = strategy_manager
        self.screenshot_timeout = 1.5

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str, index: int = 0) -> Union[object, Tuple[int, int]]:
        screenshot = self.element_source.capture()
        _, coor, _ = self.text_detection.find_element(screenshot, element, index=index)
        return coor

    def locate_with_aoi(self, element: str, aoi_x: float, aoi_y: float, aoi_width: float, aoi_height: float, index: float=0) -> Union[object, Tuple[int, int]]:
        """
        Locate text element within a specified Area of Interest (AOI).

        :param element: The text element to locate
        :param aoi_x: X percentage of AOI top-left corner (0-100)
        :param aoi_y: Y percentage of AOI top-left corner (0-100)
        :param aoi_width: Width percentage of AOI (0-100)
        :param aoi_height: Height percentage of AOI (0-100)
        :return: Coordinates relative to the full screenshot
        """
        # Capture full screenshot
        full_screenshot = self.element_source.capture()

        # Crop screenshot to AOI
        try:
            cropped_screenshot, aoi_bounds = utils.crop_screenshot_to_aoi(
                full_screenshot, aoi_x, aoi_y, aoi_width, aoi_height
            )
        except ValueError as e:
            internal_logger.error(f"AOI cropping failed for TextDetectionStrategy: {e}")
            raise OpticsError(Code.E0205, message=f"Invalid AOI parameters: {e}")

        # Find element in cropped screenshot
        _, coor, _ = self.text_detection.find_element(cropped_screenshot, element, index=index)

        if coor is None:
            return None

        # Adjust coordinates back to full screenshot
        try:
            adjusted_coor = utils.adjust_coordinates_for_aoi(coor, aoi_bounds)
            internal_logger.debug(f"Text element '{element}' found at AOI coordinates {coor}, adjusted to full screenshot coordinates {adjusted_coor}")
            return adjusted_coor
        except ValueError as e:
            internal_logger.error(f"Coordinate adjustment failed for TextDetectionStrategy: {e}")
            raise OpticsError(Code.E0205, message=f"Coordinate adjustment failed: {e}")

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Tuple[bool, Optional[str]]:
        end_time = time.time() + timeout
        found_status = dict.fromkeys(elements, False)
        result = False
        annotated_frame = None
        timestamp = None
        ss_stream = self.strategy_manager.capture_screenshot_stream(timeout=timeout)
        try:
            while time.time() < end_time:
                time.sleep(self.screenshot_timeout)  # Allow some time for screenshots to be captured
                frames = ss_stream.get_all_available_screenshots(wait_time=1)
                if not frames:
                    time.sleep(self.screenshot_timeout)
                    continue
                for frame, ts in frames:
                    current_frame = frame.copy()
                    _ , ocr_results = self.text_detection.detect_text(current_frame)
                    annotated_frame = match_and_annotate(ocr_results, elements, found_status, current_frame)

                    if (rule == "any" and any(found_status.values())) or (rule == "all" and all(found_status.values())):
                        result = True
                        timestamp = ts
                        execution_logger.info(f"Elements found: {found_status} on screenshot taken at {timestamp}")

                        break

                if result:
                    break
        finally:
            ss_stream.stop_capture()
        return result, timestamp, annotated_frame

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Text" and LocatorStrategy._is_method_implemented(element_source, "capture")


class ImageDetectionStrategy(LocatorStrategy):
    """Strategy for locating image elements using image detection."""

    def __init__(self, element_source: ElementSourceInterface, image_detection, strategy_manager):
        self._element_source = element_source
        self.image_detection = image_detection
        self.strategy_manager = strategy_manager
        self.screenshot_timeout = 1.5

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str, index: int = 0) -> Union[object, Tuple[int, int]]:
        screenshot = self.element_source.capture()
        _, centre, _ = self.image_detection.find_element(screenshot, element, index)
        return centre

    def locate_with_aoi(self, element: str, aoi_x: float, aoi_y: float, aoi_width: float, aoi_height: float) -> Union[object, Tuple[int, int]]:
        """
        Locate image element within a specified Area of Interest (AOI).

        :param element: The image element to locate
        :param aoi_x: X percentage of AOI top-left corner (0-100)
        :param aoi_y: Y percentage of AOI top-left corner (0-100)
        :param aoi_width: Width percentage of AOI (0-100)
        :param aoi_height: Height percentage of AOI (0-100)
        :return: Coordinates relative to the full screenshot
        """
        # Capture full screenshot
        full_screenshot = self.element_source.capture()

        # Crop screenshot to AOI
        try:
            cropped_screenshot, aoi_bounds = utils.crop_screenshot_to_aoi(
                full_screenshot, aoi_x, aoi_y, aoi_width, aoi_height
            )
        except ValueError as e:
            internal_logger.error(f"AOI cropping failed for ImageDetectionStrategy: {e}")
            raise OpticsError(Code.E0205, message=f"Invalid AOI parameters: {e}")

        # Find element in cropped screenshot
        _, centre, _ = self.image_detection.find_element(cropped_screenshot, element)

        if centre is None:
            return None

        # Adjust coordinates back to full screenshot
        try:
            adjusted_centre = utils.adjust_coordinates_for_aoi(centre, aoi_bounds)
            internal_logger.debug(f"Image element '{element}' found at AOI coordinates {centre}, adjusted to full screenshot coordinates {adjusted_centre}")
            return adjusted_centre
        except ValueError as e:
            internal_logger.error(f"Coordinate adjustment failed for ImageDetectionStrategy: {e}")
            raise OpticsError(Code.E0205, message=f"Coordinate adjustment failed: {e}")

    def assert_elements(self, elements: list, timeout: int = 30, rule: str = 'any') -> Tuple[bool, Optional[str]]:
        end_time = time.time() + timeout
        result = False
        ss_stream = self.strategy_manager.capture_screenshot_stream(timeout=timeout)
        annotated_frame = None
        timestamp = None
        try:
            while time.time() < end_time:
                time.sleep(self.screenshot_timeout)  # Allow some time for screenshots to be captured
                frames = ss_stream.get_all_available_screenshots(wait_time=1)
                if not frames:
                    time.sleep(self.screenshot_timeout)
                    continue
                for frame, ts in frames:
                    current_frame = frame.copy()
                    result, annotated = self.image_detection.assert_elements(current_frame, elements, rule)
                    if result:
                        timestamp = ts
                        annotated_frame = annotated  # assuming assert_elements returns the annotated image
                        execution_logger.info(f"Image elements found on screenshot taken at {timestamp}")
                        break
                if result:
                    break
        finally:
            ss_stream.stop_capture()
        return result, timestamp, annotated_frame

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Image" and LocatorStrategy._is_method_implemented(element_source, "capture")

class PagesourceStrategy:
    def __init__(self, element_source: ElementSourceInterface):
        self.element_source: ElementSourceInterface = element_source

    def capture_pagesource(self) -> Optional[str]:
        pagesource = self.element_source.get_page_source()
        if pagesource is not None:
            if isinstance(pagesource, str):
                return pagesource
            elif isinstance(pagesource, tuple) and len(pagesource) >= 1:
                # Handle tuple case - return the first element (page source)
                return str(pagesource[0])
            # If it's an ndarray, convert to string (or handle as needed)
            try:
                return str(pagesource)
            except Exception:
                raise OpticsError(Code.E0403, message="Invalid pagesource captured: not a string or convertible")
        raise OpticsError(Code.E0403, message="Invalid pagesource captured")

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> List[dict]:
        """Retrieve interactive elements from the element source."""

        elements_dict = self.element_source.get_interactive_elements(filter_config)
        if elements_dict is not None:
            return elements_dict
        raise NotImplementedError("Interactive elements retrieval failed.")

    @staticmethod
    def supports(element_source: ElementSourceInterface) -> bool:
        return LocatorStrategy._is_method_implemented(element_source, "get_page_source")


class ScreenshotStrategy:
    def __init__(self, element_source: ElementSourceInterface):
        self.element_source: ElementSourceInterface = element_source

    def capture(self) -> Optional[np.ndarray]:
        try:
            screenshot = self.element_source.capture()
        except Exception as e:
            internal_logger.error(f"Error capturing screenshot: {e}")
            raise OpticsError(Code.E0303, message=f"Error capturing screenshot: {e}")
        if screenshot is not None and not utils.is_black_screen(screenshot):
            return screenshot
        internal_logger.warning("Invalid screenshot captured: black screen detected.")
        raise OpticsError(
            Code.E0303, message="Invalid screenshot captured, black screen detected."
        )

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
    def __init__(self, element_source: InstanceFallback[ElementSourceInterface], text_detection, image_detection):
        # Defensive: always wrap in InstanceFallback if not already
        if not isinstance(element_source, InstanceFallback):
            element_source = InstanceFallback([element_source])
        self.element_source = element_source
        self.locator_factory = StrategyFactory(text_detection, image_detection, strategy_manager=self)
        self.screenshot_factory = ScreenshotFactory()
        self.pagesource_factory = PagesourceFactory()
        self.locator_strategies = self._build_locator_strategies()
        self.screenshot_strategies = self._build_screenshot_strategies()
        self.pagesource_strategies = self._build_pagesource_strategies()
        self.screenshot_stream = None

    def _build_locator_strategies(self) -> List[LocatorStrategy]:
        strategies = []
        for instance in self.element_source.instances:
            strategies.extend(self.locator_factory.create_strategies(instance))
        return strategies

    def _build_screenshot_strategies(self) -> Set[ScreenshotStrategy]:
        strategies = set()
        for instance in self.element_source.instances:
            strategies.update(self.screenshot_factory.create_strategies(instance))
        return strategies

    def _build_pagesource_strategies(self) -> Set[PagesourceStrategy]:
        strategies = set()
        for instance in self.element_source.instances:
            strategies.update(self.pagesource_factory.create_strategies(instance))
        return strategies

    def locate(self, element: str, aoi_x=None, aoi_y=None, aoi_width=None, aoi_height=None, index: int = 0) -> Generator[LocateResult, None, None]:
        element_type = utils.determine_element_type(element)
        execution_logger.info(f"Locating element: {element} of type: {element_type}...")

        # Check if AOI is specified
        aoi_params = [aoi_x, aoi_y, aoi_width, aoi_height]
        use_aoi = any(param is not None for param in aoi_params)

        if use_aoi:
            # Validate that all AOI params are provided when any is provided
            if not all(param is not None for param in aoi_params):
                raise OpticsError(Code.E0205, message="All AOI parameters (aoi_x, aoi_y, aoi_width, aoi_height) must be provided together")

            execution_logger.info(f"Using AOI: x={aoi_x}%, y={aoi_y}%, width={aoi_width}%, height={aoi_height}%")

        for strategy in self.locator_strategies:
            internal_logger.debug(f"Trying strategy: {type(strategy).__name__} for element: {element}")
            if strategy.supports(element_type, strategy.element_source):
                try:
                    # Check if strategy supports AOI by looking for aoi-aware locate method
                    if use_aoi and hasattr(strategy, 'locate_with_aoi'):
                        result = strategy.locate_with_aoi(element, aoi_x, aoi_y, aoi_width, aoi_height, index=index)
                    else:
                        result = strategy.locate(element, index=index)

                    if result:
                        execution_tracer.log_attempt(strategy, element, "success")
                        yield LocateResult(result, strategy)
                except Exception as e:
                    execution_tracer.log_attempt(strategy, element, "fail", error=str(e))
                    internal_logger.error(
                        f"Strategy {strategy.__class__.__name__} failed: {e}")
        raise OpticsError(Code.E0201, message=f"Element '{element}' not found using any strategy.")

    def assert_presence(self, elements: list, element_type: str, timeout: int = 30, rule: str = 'any'):
        self._validate_rule(rule)
        execution_logger.info(
            f"Asserting presence of elements: {elements} with rule: {rule} and timeout: {timeout}s")

        # Treat the provided timeout as a total deadline across all applicable strategies.
        deadline = time.time() + timeout
        last_exception = None

        # First collect strategies that can assert these elements so we can fairly split time
        applicable_strategies = [s for s in self.locator_strategies if self._can_strategy_assert_elements(s, element_type)]
        if not applicable_strategies:
            raise OpticsError(Code.E0201, message="No elements found.")

        remaining_total = max(0.0, deadline - time.time())

        for idx, strategy in enumerate(applicable_strategies):
            internal_logger.debug(f"Trying strategy: {type(strategy).__name__} for elements: {elements}")

            # Recompute remaining total before allocating
            remaining_total = max(0.0, deadline - time.time())
            remaining_strategies = len(applicable_strategies) - idx

            if remaining_total <= 0:
                internal_logger.debug("No remaining time left to try further strategies.")
                break

            # Fair allocation: ceil(remaining_total / remaining_strategies)
            alloc = int(math.ceil(remaining_total / remaining_strategies))
            # Ensure alloc does not exceed remaining_total (cast to int seconds)
            alloc = min(alloc, int(math.floor(remaining_total))) if remaining_total >= 1 else 0

            internal_logger.debug(f"Allocating {alloc}s to strategy {type(strategy).__name__} (remaining_total={remaining_total}s, remaining_strategies={remaining_strategies})")

            if alloc <= 0:
                # If we have less than 1s remaining, let the last strategy try with 0 to perform any immediate checks
                if idx == len(applicable_strategies) - 1:
                    alloc = 0
                else:
                    internal_logger.debug("Insufficient time to allocate to next strategies.")
                    break

            try:
                result, timestamp, annotated_frame = self._try_assert_with_strategy(strategy, elements, alloc, rule)
                if result:
                    return result, timestamp, annotated_frame
            except Exception as e:
                last_exception = e

        # If we arrive here nothing was found within the total timeout
        if last_exception:
            internal_logger.debug(f"assert_presence ended with last exception: {last_exception}")
        raise OpticsError(Code.E0201, message="No elements found.")

    def _validate_rule(self, rule: str):
        """Validate the rule parameter."""
        rule = rule.lower()
        if rule not in ("any", "all"):
            raise OpticsError(Code.E0205, message="Invalid rule. Use 'any' or 'all'.")

    def _can_strategy_assert_elements(self, strategy, element_type: str) -> bool:
        """Check if strategy can assert elements for the given element type."""
        return (hasattr(strategy, 'assert_elements') and
                strategy.supports(element_type, strategy.element_source))

    def _try_assert_with_strategy(self, strategy, elements: list, timeout: int, rule: str):
        """Try to assert elements using a specific strategy."""
        try:
            result_tuple = strategy.assert_elements(elements, timeout, rule)
            result, timestamp, annotated_frame = result_tuple

            if result:
                execution_tracer.log_attempt(strategy, str(elements), "success")
                return result, timestamp, annotated_frame
            else:
                execution_tracer.log_attempt(strategy, str(elements), "fail", error="Elements not found.")
                internal_logger.debug(
                    f"Strategy {strategy.__class__.__name__} did not find elements: {elements}")
                return False, None, None
        except Exception as e:
            execution_tracer.log_attempt(strategy, str(elements), "fail", error=str(e))
            return False, None, None


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
        raise OpticsError(Code.E0303, message="No screenshot captured using available strategies.")

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
            raise OpticsError(Code.E0301, message="No active screenshot stream to stop.")

    def capture_pagesource(self) -> Optional[str]:
        for strategy in self.pagesource_strategies:
            try:
                return strategy.capture_pagesource()
            except Exception as e:
                internal_logger.debug(
                    f"Pagesource capture failed with {strategy.__class__.__name__}: {e}")
        internal_logger.error("No pagesource captured.")
        raise OpticsError(Code.E0403, message="No pagesource captured using available strategies.")

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> List[dict]:
        """Retrieve interactive elements from the element source."""
        for strategy in self.pagesource_strategies:
            try:
                return strategy.get_interactive_elements(filter_config)
            except Exception as e:
                internal_logger.debug(
                    f"Failed to retrieve interactive elements with {strategy.__class__.__name__}: {e}")
        internal_logger.error("No interactive elements retrieved.")
        raise OpticsError(Code.E0202, message="No interactive elements retrieved using available strategies.")
