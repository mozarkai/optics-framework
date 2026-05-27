from abc import ABC, abstractmethod
import inspect
import time
import math
from typing import List, NamedTuple, Union, Tuple, Generator, Set, Optional, Any
import numpy as np
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils
from optics_framework.common.screenshot_stream import ScreenshotStream
from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common.execution_tracer import execution_tracer
from optics_framework.engines.vision_models.base_methods import match_and_annotate
from optics_framework.common.error import OpticsError, Code

# Constants
TEXT_DETECTION_NOT_AVAILABLE_MSG = "Text detection is not available."


class LocateValueWithFrame(NamedTuple):
    """Result from vision strategies: coordinates (or value) plus optional annotated image."""
    value: Union[Tuple[int, int], object]
    annotated_frame: Optional[Any]


class LocatorStrategy(ABC):
    """Abstract base class for element location strategies."""

    @property
    @abstractmethod
    def element_source(self) -> ElementSourceInterface:
        pass

    @abstractmethod
    def locate(
        self, element: str, index: int = 0
    ) -> Optional[Union[object, Tuple[int, int], LocateValueWithFrame]]:
        """Locates an element and returns either an element object or coordinates (x, y).

        :param element: The element identifier (e.g., XPath, text, image path).
        :param index: The index of the element if multiple matches are found.
        :return: Either an element object, a tuple of (x, y) coordinates,
            a LocateValueWithFrame (vision strategies), or None if not found.
        """
        pass

    @abstractmethod
    def assert_elements(
        self, elements: list, timeout: int = 30, rule: str = 'any'
    ) -> Tuple[bool, Optional[str], Optional[Any]]:
        """Returns (result, timestamp, annotated_frame). Timestamp and annotated_frame may be None."""
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

    def _assert_elements_locator_style(
        self, elements: list, timeout: int = 30, rule: str = 'any'
    ) -> Tuple[bool, Optional[str], Optional[Any]]:
        """Shared implementation for locator strategies that delegate to element_source.assert_elements and optionally attach a screenshot."""
        try:
            self.element_source.assert_elements(elements, timeout, rule)
            timestamp = utils.get_timestamp()
            frame = None
            strategy_manager = getattr(self, '_strategy_manager', None)
            if strategy_manager is not None:
                try:
                    frame = strategy_manager.capture_screenshot()
                except Exception as e:
                    internal_logger.exception("Failed to capture screenshot for assert_elements: %s", e)
                    frame = None
            bboxes = []
            if hasattr(self.element_source, 'get_element_bboxes'):
                bboxes = [
                    b for b in self.element_source.get_element_bboxes(elements)
                    if b is not None
                ]
            if frame is not None:
                if bboxes:
                    annotated_frame = utils.annotate(frame.copy(), bboxes)
                    return True, timestamp, annotated_frame
                return True, timestamp, frame.copy()
            return True, timestamp, None
        except Exception:
            return False, None, None


class XPathStrategy(LocatorStrategy):
    """Strategy for locating elements via XPath."""

    def __init__(self, element_source: ElementSourceInterface, strategy_manager=None):
        self._element_source = element_source
        self._strategy_manager = strategy_manager

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str, index: int = 0) -> Optional[Union[object, Tuple[int, int]]]:
        return self.element_source.locate(element, index)

    def assert_elements(
        self, elements: list, timeout: int = 30, rule: str = 'any'
    ) -> Tuple[bool, Optional[str], Optional[Any]]:
        return self._assert_elements_locator_style(elements, timeout, rule)

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "XPath" and LocatorStrategy._is_method_implemented(element_source, "locate")


class TextElementStrategy(LocatorStrategy):
    """Strategy for locating text elements directly via the element source."""

    def __init__(self, element_source: ElementSourceInterface, strategy_manager=None):
        self._element_source = element_source
        self._strategy_manager = strategy_manager

    @property
    def element_source(self) -> ElementSourceInterface:
        return self._element_source

    def locate(self, element: str, index: int = 0) -> Optional[Union[object, Tuple[int, int]]]:
        return self.element_source.locate(element, index)

    def assert_elements(
        self, elements: list, timeout: int = 30, rule: str = 'any'
    ) -> Tuple[bool, Optional[str], Optional[Any]]:
        return self._assert_elements_locator_style(elements, timeout, rule)

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        # Supports Text, CSS and Class types since element sources handle CSS and Class selectors too
        return element_type in ("Text", "CSS", "Class") and LocatorStrategy._is_method_implemented(element_source, "locate")

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

    def locate(self, element: str, index: int = 0) -> Optional[LocateValueWithFrame]:
        if self.text_detection is None:
            raise OpticsError(Code.E0201, message=TEXT_DETECTION_NOT_AVAILABLE_MSG)
        screenshot = self.element_source.capture()
        found = self.text_detection.find_element(screenshot, element, index=index)
        if found is None:
            return None
        _, coor, bbox = found
        if coor is None or (
            isinstance(coor, tuple) and len(coor) == 2 and coor[0] is None and coor[1] is None
        ):
            return None
        annotated_frame = None
        if bbox is not None and len(bbox) == 2 and bbox[0] is not None and bbox[1] is not None:
            annotated_frame = utils.annotate(screenshot.copy(), [bbox])
        return LocateValueWithFrame(coor, annotated_frame)

    def locate_with_aoi(self, element: str, aoi_x: float, aoi_y: float, aoi_width: float, aoi_height: float, index: float = 0) -> Optional[LocateValueWithFrame]:
        """
        Locate text element within a specified Area of Interest (AOI).

        :param element: The text element to locate
        :param aoi_x: X percentage of AOI top-left corner (0-100)
        :param aoi_y: Y percentage of AOI top-left corner (0-100)
        :param aoi_width: Width percentage of AOI (0-100)
        :param aoi_height: Height percentage of AOI (0-100)
        :return: Coordinates relative to the full screenshot
        """
        if self.text_detection is None:
            raise OpticsError(Code.E0201, message=TEXT_DETECTION_NOT_AVAILABLE_MSG)
        # Capture full screenshot
        full_screenshot = self.element_source.capture()

        # Crop screenshot to AOI
        try:
            cropped_screenshot, aoi_bounds = utils.crop_screenshot_to_aoi(
                full_screenshot, aoi_x, aoi_y, aoi_width, aoi_height
            )
        except ValueError as e:
            internal_logger.debug(f"AOI cropping failed for TextDetectionStrategy: {e}")
            raise OpticsError(Code.E0205, message=f"Invalid AOI parameters: {e}")

        # Find element in cropped screenshot
        found = self.text_detection.find_element(cropped_screenshot, element, index=index)
        if found is None:
            return None
        _, coor, bbox = found

        if coor is None or (
            isinstance(coor, tuple) and len(coor) == 2 and coor[0] is None and coor[1] is None
        ):
            return None

        # Adjust coordinates back to full screenshot
        try:
            adjusted_coor = utils.adjust_coordinates_for_aoi(coor, aoi_bounds)
            internal_logger.debug(f"Text element '{element}' found at AOI coordinates {coor}, adjusted to full screenshot coordinates {adjusted_coor}")
            annotated_frame = None
            if bbox is not None and len(bbox) == 2 and bbox[0] is not None and bbox[1] is not None:
                adjusted_tl = utils.adjust_coordinates_for_aoi(bbox[0], aoi_bounds)
                adjusted_br = utils.adjust_coordinates_for_aoi(bbox[1], aoi_bounds)
                annotated_frame = utils.annotate(full_screenshot.copy(), [(adjusted_tl, adjusted_br)])
            return LocateValueWithFrame(adjusted_coor, annotated_frame)
        except ValueError as e:
            internal_logger.debug(f"Coordinate adjustment failed for TextDetectionStrategy: {e}")
            raise OpticsError(Code.E0205, message=f"Coordinate adjustment failed: {e}")

    def assert_elements(
        self, elements: list, timeout: int = 30, rule: str = 'any'
    ) -> Tuple[bool, Optional[str], Optional[Any]]:
        if self.text_detection is None:
            raise OpticsError(Code.E0201, message=TEXT_DETECTION_NOT_AVAILABLE_MSG)
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

    def locate(self, element: str, index: int = 0) -> Optional[LocateValueWithFrame]:
        screenshot = self.element_source.capture()
        found = self.image_detection.find_element(screenshot, element, index)
        if found is None:
            return None
        _, centre, bbox = found
        if centre is None:
            return None
        annotated_frame = None
        if bbox is not None and len(bbox) == 2 and bbox[0] is not None and bbox[1] is not None:
            annotated_frame = utils.annotate(screenshot.copy(), [bbox])
        return LocateValueWithFrame(centre, annotated_frame)

    def locate_with_aoi(self, element: str, aoi_x: float, aoi_y: float, aoi_width: float, aoi_height: float, index: int = 0) -> Optional[LocateValueWithFrame]:
        """
        Locate image element within a specified Area of Interest (AOI).

        :param element: The image element to locate
        :param aoi_x: X percentage of AOI top-left corner (0-100)
        :param aoi_y: Y percentage of AOI top-left corner (0-100)
        :param aoi_width: Width percentage of AOI (0-100)
        :param aoi_height: Height percentage of AOI (0-100)
        :param index: Zero-based index when multiple matches exist (default 0)
        :return: Coordinates relative to the full screenshot, or (coords, annotated_frame)
        """
        # Capture full screenshot
        full_screenshot = self.element_source.capture()

        # Crop screenshot to AOI
        try:
            cropped_screenshot, aoi_bounds = utils.crop_screenshot_to_aoi(
                full_screenshot, aoi_x, aoi_y, aoi_width, aoi_height
            )
        except ValueError as e:
            internal_logger.debug(f"AOI cropping failed for ImageDetectionStrategy: {e}")
            raise OpticsError(Code.E0205, message=f"Invalid AOI parameters: {e}")

        # Find element in cropped screenshot
        found = self.image_detection.find_element(cropped_screenshot, element, index)
        if found is None:
            return None
        _, centre, bbox = found

        if centre is None:
            return None

        # Adjust coordinates back to full screenshot
        try:
            adjusted_centre = utils.adjust_coordinates_for_aoi(centre, aoi_bounds)
            internal_logger.debug(f"Image element '{element}' found at AOI coordinates {centre}, adjusted to full screenshot coordinates {adjusted_centre}")
            annotated_frame = None
            if bbox is not None and len(bbox) == 2 and bbox[0] is not None and bbox[1] is not None:
                adjusted_tl = utils.adjust_coordinates_for_aoi(bbox[0], aoi_bounds)
                adjusted_br = utils.adjust_coordinates_for_aoi(bbox[1], aoi_bounds)
                annotated_frame = utils.annotate(full_screenshot.copy(), [(adjusted_tl, adjusted_br)])
            return LocateValueWithFrame(adjusted_centre, annotated_frame)
        except ValueError as e:
            internal_logger.debug(f"Coordinate adjustment failed for ImageDetectionStrategy: {e}")
            raise OpticsError(Code.E0205, message=f"Coordinate adjustment failed: {e}")

    def assert_elements(
        self, elements: list, timeout: int = 30, rule: str = 'any'
    ) -> Tuple[bool, Optional[str], Optional[Any]]:
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

    def capture_pagesource(self) -> Optional[Tuple[str, str]]:
        """
        Capture page source and timestamp from the element source.
        Returns:
            Optional[Tuple[str, str]]: (page_source, timestamp) or None
        """
        try:
            result = self.element_source.get_page_source()
            if result is None:
                raise OpticsError(Code.E0403, message="Invalid pagesource captured")
            page_source, timestamp = result
            return (str(page_source), str(timestamp))
        except OpticsError:
            raise
        except Exception as e:
            raise OpticsError(Code.E0403, message=f"Invalid pagesource captured: {e}") from e

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
            internal_logger.debug(f"Error capturing screenshot: {e}")
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
            (XPathStrategy, "XPath", {"strategy_manager": self.strategy_manager}, 1),
            (TextElementStrategy, "Text", {"strategy_manager": self.strategy_manager}, 2),
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

    def __init__(
        self,
        value: Union[object, Tuple[int, int]],
        strategy: LocatorStrategy,
        annotated_frame: Optional[Any] = None,
    ):
        self.value = value
        self.strategy = strategy
        self.annotated_frame = annotated_frame
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

    def _validate_aoi(
        self, aoi_x: Optional[float], aoi_y: Optional[float],
        aoi_width: Optional[float], aoi_height: Optional[float]
    ) -> bool:
        """Validate AOI params; if any is set, all must be set. Return True if using AOI."""
        aoi_params = [aoi_x, aoi_y, aoi_width, aoi_height]
        use_aoi = any(p is not None for p in aoi_params)
        if use_aoi and not all(p is not None for p in aoi_params):
            raise OpticsError(
                Code.E0205,
                message="All AOI parameters (aoi_x, aoi_y, aoi_width, aoi_height) must be provided together",
            )
        if use_aoi:
            execution_logger.info(f"Using AOI: x={aoi_x}%, y={aoi_y}%, width={aoi_width}%, height={aoi_height}%")
        return use_aoi

    def _try_strategy_locate(
        self,
        strategy: "LocatorStrategy",
        element: str,
        element_type: str,
        use_aoi: bool,
        aoi_x: Optional[float],
        aoi_y: Optional[float],
        aoi_width: Optional[float],
        aoi_height: Optional[float],
        index: int,
    ) -> Optional[LocateResult]:
        """Run one strategy; return LocateResult on success, None on skip/fail."""
        if not strategy.supports(element_type, strategy.element_source):
            return None
        try:
            locate_with_aoi = getattr(strategy, "locate_with_aoi", None)
            if use_aoi and locate_with_aoi is not None:
                result = locate_with_aoi(element, aoi_x, aoi_y, aoi_width, aoi_height, index=index)
            else:
                result = strategy.locate(element, index=index)
            if result is None:
                return None
            if isinstance(result, LocateValueWithFrame):
                value, annotated_frame = result.value, result.annotated_frame
            elif isinstance(result, tuple) and len(result) == 2 and (result[1] is None or hasattr(result[1], "shape")):
                value, annotated_frame = result[0], result[1]
            else:
                value, annotated_frame = result, None
            execution_tracer.log_attempt(strategy, element, "success")
            return LocateResult(value, strategy, annotated_frame=annotated_frame)
        except Exception as e:
            execution_tracer.log_attempt(strategy, element, "fail", error=str(e))
            internal_logger.debug(f"Strategy {strategy.__class__.__name__} failed: {e}")
        return None

    def locate(self, element: str, aoi_x=None, aoi_y=None, aoi_width=None, aoi_height=None, index: int = 0) -> Generator[LocateResult, None, None]:
        effective_element, text_only = utils.parse_text_only_prefix(element)
        element_type = utils.determine_element_type(element)
        internal_logger.info(f"Locating element: {element} of type: {element_type}...")
        use_aoi = self._validate_aoi(aoi_x, aoi_y, aoi_width, aoi_height)

        yielded = False
        for strategy in self.locator_strategies:
            if text_only and type(strategy).__name__ == "TextElementStrategy":
                continue
            execution_logger.debug(f"Trying strategy: {type(strategy).__name__} for element: {effective_element}")
            locate_result = self._try_strategy_locate(
                strategy, effective_element, element_type, use_aoi, aoi_x, aoi_y, aoi_width, aoi_height, index
            )
            if locate_result:
                yielded = True
                yield locate_result
        if not yielded:
            raise OpticsError(Code.E0201, message=f"Element '{element}' not found.")

    def _alloc_time_for_strategy(
        self, deadline: float, idx: int, applicable_strategies: List[Any]
    ) -> Optional[Tuple[int, float, int]]:
        """Compute seconds to allocate for this strategy. Returns (alloc, remaining_total, remaining_strategies) or None to break."""
        remaining_total = max(0.0, deadline - time.time())
        remaining_strategies = len(applicable_strategies) - idx
        if remaining_total <= 0:
            internal_logger.debug("No remaining time left to try further strategies.")
            return None
        alloc = int(math.ceil(remaining_total / remaining_strategies))
        alloc = min(alloc, int(math.floor(remaining_total)))
        if alloc <= 0:
            if idx == len(applicable_strategies) - 1:
                return (0, remaining_total, remaining_strategies)
            internal_logger.debug("Insufficient time to allocate to next strategies.")
            return None
        return (alloc, remaining_total, remaining_strategies)

    def assert_presence(self, elements: list, element_type: str, timeout: int = 30, rule: str = 'any'):
        self._validate_rule(rule)
        execution_logger.info(
            f"Asserting presence of elements: {elements} with rule: {rule} and timeout: {timeout}s")

        effective_elements = [utils.parse_text_only_prefix(el)[0] for el in elements]
        has_text_only = any(utils.parse_text_only_prefix(el)[1] for el in elements)

        deadline = time.time() + timeout
        last_exception = None
        applicable_strategies = [
            s for s in self.locator_strategies
            if self._can_strategy_assert_elements(s, element_type)
        ]
        if has_text_only:
            applicable_strategies = [
                s for s in applicable_strategies
                if type(s).__name__ != "TextElementStrategy"
            ]
        if not applicable_strategies:
            raise OpticsError(Code.E0201, message=f"No strategies found for elements: {elements} with rule '{rule}'.")

        for idx, strategy in enumerate(applicable_strategies):
            internal_logger.debug(f"Trying strategy: {type(strategy).__name__} for elements: {elements}")
            alloc_result = self._alloc_time_for_strategy(deadline, idx, applicable_strategies)
            if alloc_result is None:
                break
            alloc, remaining_total, remaining_strategies = alloc_result
            internal_logger.debug(
                f"Allocating {alloc}s to strategy {type(strategy).__name__} "
                f"(remaining_total={remaining_total}s, remaining_strategies={remaining_strategies})"
            )
            try:
                result, timestamp, annotated_frame = self._try_assert_with_strategy(
                    strategy, effective_elements, alloc, rule
                )
                if result:
                    return result, timestamp, annotated_frame
            except Exception as e:
                last_exception = e

        if last_exception:
            internal_logger.debug(f"assert_presence ended with last exception: {last_exception}")
        raise OpticsError(Code.E0201, message=f"{elements} not found based on rule '{rule}'.")

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
        """Try to assert elements using a specific strategy.

        Strategies are required to return (result, timestamp, annotated_frame).
        """
        try:
            result, timestamp, annotated_frame = strategy.assert_elements(elements, timeout, rule)

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
        execution_logger.debug("Capturing screenshot using available strategies.")
        for strategy in self.screenshot_strategies:
            try:
                img = strategy.capture()
                execution_tracer.log_attempt(strategy, "screenshot", "success")
                return img
            except Exception as e:
                execution_tracer.log_attempt(strategy, "screenshot", "fail", error=str(e))
        internal_logger.debug("No screenshot captured.")
        raise OpticsError(Code.E0303, message="No screenshot captured using available strategies.")

    def capture_screenshot_stream(self, timeout: int = 30):
        """Capture a screenshot stream using the available strategies."""
        execution_logger.debug("Starting screenshot stream with available strategies.")
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
            execution_logger.debug("Screenshot stream stopped successfully.")
            self.screenshot_stream = None
        else:
            execution_logger.warning("No active screenshot stream to stop.")
            raise OpticsError(Code.E0301, message="No active screenshot stream to stop.")

    def capture_pagesource(self) -> Optional[Tuple[str, str]]:
        for strategy in self.pagesource_strategies:
            try:
                return strategy.capture_pagesource()
            except Exception as e:
                internal_logger.debug(
                    f"Pagesource capture failed with {strategy.__class__.__name__}: {e}")
        internal_logger.debug("No pagesource captured.")
        raise OpticsError(Code.E0403, message="No pagesource captured using available strategies.")

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> List[dict]:
        """Retrieve interactive elements from the element source."""
        for strategy in self.pagesource_strategies:
            try:
                return strategy.get_interactive_elements(filter_config)
            except Exception as e:
                internal_logger.debug(
                    f"Failed to retrieve interactive elements with {strategy.__class__.__name__}: {e}")
        internal_logger.debug("No interactive elements retrieved.")
        raise OpticsError(Code.E0202, message="No interactive elements retrieved using available strategies.")
