from abc import ABC, abstractmethod
from dataclasses import dataclass
import inspect
import time
from typing import List, NamedTuple, Union, Tuple, Generator, Set, Optional, Any
import cv2
import numpy as np
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common import utils
from optics_framework.common.logging_config import internal_logger, execution_logger
from optics_framework.common.execution_tracer import execution_tracer
from optics_framework.engines.vision_models.base_methods import match_and_annotate
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.session_liveness import raise_if_session_dead

# Constants
TEXT_DETECTION_NOT_AVAILABLE_MSG = "Text detection is not available."
OCR_NOT_TRIED_MSG = " OCR was not tried: no text_detection engine is configured."


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
    def check_elements(
        self, elements: list[str], visible: bool, frame: np.ndarray | None
    ) -> tuple[list[bool], np.ndarray | None]:
        """One look per element, no waiting; returns the hits and an annotated frame when the strategy drew one."""

    def can_assert(self, element: str) -> bool:
        """Whether this strategy can look ``element`` up; vision strategies always can."""
        return True

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

    def _annotated_screen(self, elements: list[str]) -> np.ndarray | None:
        """The current screen with ``elements`` boxed, when a screenshot can be taken."""
        frame = None
        strategy_manager = getattr(self, '_strategy_manager', None)
        if strategy_manager is not None:
            try:
                frame = strategy_manager.capture_screenshot()
            except Exception as e:
                internal_logger.exception("Failed to capture screenshot for an assertion: %s", e)
                frame = None
        bboxes = []
        if hasattr(self.element_source, 'get_element_bboxes'):
            bboxes = [
                b for b in self.element_source.get_element_bboxes(elements)
                if b is not None
            ]
        if frame is not None:
            if bboxes:
                # Element bboxes are in the driver's window coordinate space;
                # scale them to the screenshot's pixel space before drawing
                # (no-op when the two resolutions already match).
                bboxes = utils.scale_bboxes_for_screenshot(bboxes, self.element_source, frame)
                return utils.annotate(frame.copy(), bboxes)
            return frame.copy()
        return None


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

    def can_assert(self, element: str) -> bool:
        return self.element_source.can_locate(element)

    def check_elements(
        self, elements: list[str], visible: bool, frame: np.ndarray | None
    ) -> tuple[list[bool], np.ndarray | None]:
        return self.element_source.check_elements(elements, visible), None

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

    def can_assert(self, element: str) -> bool:
        return self.element_source.can_locate(element)

    def check_elements(
        self, elements: list[str], visible: bool, frame: np.ndarray | None
    ) -> tuple[list[bool], np.ndarray | None]:
        return self.element_source.check_elements(elements, visible), None

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

    def can_assert(self, element: str) -> bool:
        # The Verifier groups css= with text; OCR must not search the screen for a selector.
        return utils.determine_element_type(element) != "CSS"

    def check_elements(
        self, elements: list[str], visible: bool, frame: np.ndarray | None
    ) -> tuple[list[bool], np.ndarray | None]:
        if self.text_detection is None:
            raise OpticsError(Code.E0201, message=TEXT_DETECTION_NOT_AVAILABLE_MSG)
        found = dict.fromkeys(elements, False)
        _, ocr_results = self.text_detection.detect_text(frame.copy())
        annotated_frame = match_and_annotate(ocr_results, elements, found, frame.copy())
        return [found[element] for element in elements], annotated_frame

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Text" and LocatorStrategy._is_method_implemented(element_source, "capture")


def _is_unconfigured_ocr(strategy: LocatorStrategy) -> bool:
    return isinstance(strategy, TextDetectionStrategy) and strategy.text_detection is None


class ImageDetectionStrategy(LocatorStrategy):
    """Strategy for locating image elements using image detection."""

    def __init__(self, element_source: ElementSourceInterface, image_detection, strategy_manager):
        self._element_source = element_source
        self.image_detection = image_detection
        self.strategy_manager = strategy_manager

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

    def check_elements(
        self, elements: list[str], visible: bool, frame: np.ndarray | None
    ) -> tuple[list[bool], np.ndarray | None]:
        hits = []
        annotated_frame = None
        for element in elements:
            try:
                found, marked = self.image_detection.assert_elements(frame.copy(), [element], "any")
            except RuntimeError:  # TemplateMatch reports a miss by raising
                found, marked = False, None
            hits.append(bool(found))
            if found and marked is not None:
                annotated_frame = marked
        return hits, annotated_frame

    @staticmethod
    def supports(element_type: str, element_source: ElementSourceInterface) -> bool:
        return element_type == "Image" and LocatorStrategy._is_method_implemented(element_source, "capture")


_ASSERT_POLL_INTERVAL_S = 0.5
_VISION_INTERVAL_S = 2.0
_SAME_SCREEN_TOLERANCE = 8


def _is_vision(strategy: LocatorStrategy) -> bool:
    return isinstance(strategy, (TextDetectionStrategy, ImageDetectionStrategy))


def _same_screen(frame: np.ndarray, previous: np.ndarray | None) -> bool:
    return (
        previous is not None
        and frame.shape == previous.shape
        and int(cv2.absdiff(frame, previous).max()) <= _SAME_SCREEN_TOLERANCE
    )


@dataclass
class _AssertState:
    elements: list[str]
    found: dict[str, bool]
    strategies: list[LocatorStrategy]
    visible: bool
    rule: str
    deadline: float
    first_round: bool = True
    last_vision: float | None = None
    last_exception: Exception | None = None
    vision_due: bool = True
    frame: np.ndarray | None = None
    frame_ts: str | None = None
    checked_frame: np.ndarray | None = None


class PagesourceStrategy:
    def __init__(self, element_source: ElementSourceInterface):
        self.element_source: ElementSourceInterface = element_source

    def capture_pagesource(self) -> Optional[Tuple[str, str]]:
        """
        Capture pagesource and timestamp from the element source.
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

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None, compact: bool = False) -> List[dict]:
        """Retrieve interactive elements from the element source.

        ``compact`` is forwarded only to sources whose signature declares it. An
        out-of-tree source built against the earlier
        ``get_interactive_elements(filter_config)`` contract keeps working; it just
        cannot answer the compact projection, so it returns its full list.
        """

        if self._source_accepts_compact():
            elements_dict = self.element_source.get_interactive_elements(filter_config, compact=compact)
        else:
            elements_dict = self.element_source.get_interactive_elements(filter_config)
        if elements_dict is not None:
            return elements_dict
        raise NotImplementedError("Interactive elements retrieval failed.")

    def _source_accepts_compact(self) -> bool:
        """Whether the source's extraction method accepts the ``compact`` keyword."""
        method = getattr(self.element_source, "get_interactive_elements", None)
        if method is None:
            return False
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            return False
        return "compact" in parameters or any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )

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

    def _within_aoi(
        self, strategy: "LocatorStrategy", value: Any,
        aoi_x: float, aoi_y: float, aoi_width: float, aoi_height: float,
    ) -> bool:
        """True if a located WebElement's centre lies inside the AOI rectangle.

        Filters accessibility/XPath matches (which return a WebElement and ignore the
        AOI, unlike ``locate_with_aoi`` OCR/image strategies), comparing the centre as a
        window percentage against the AOI. Fails open when bbox/window is unknown.

        Centre-point test, so it can disagree with an OCR/image crop at the AOI edge.
        """
        element_source = getattr(strategy, "element_source", None)
        if element_source is None or not hasattr(element_source, "get_bbox_for_element"):
            return True
        try:
            bbox = element_source.get_bbox_for_element(value)
        except Exception:  # noqa: BLE001 - best-effort; fall back to not filtering
            return True
        window = utils._window_size_from_source(element_source)
        if bbox is None or window is None:
            return True
        (x1, y1), (x2, y2) = bbox
        win_w, win_h = window
        if win_w <= 0 or win_h <= 0:
            return True
        cx = (x1 + x2) / 2 / win_w * 100
        cy = (y1 + y2) / 2 / win_h * 100
        inside = aoi_x <= cx <= aoi_x + aoi_width and aoi_y <= cy <= aoi_y + aoi_height
        if not inside:
            internal_logger.debug(
                f"Element centre ({cx:.1f}%, {cy:.1f}%) outside AOI "
                f"[{aoi_x},{aoi_y},{aoi_width},{aoi_height}]; skipping match."
            )
        return inside

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
        """Run one strategy; return LocateResult on success, None on skip; raise on failure."""
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
            if use_aoi and locate_with_aoi is None and not isinstance(value, tuple):
                # Strategy can't scope to the AOI itself (accessibility/XPath) — enforce
                # it here so an out-of-AOI match falls through to an AOI-capable strategy.
                if not self._within_aoi(strategy, value, aoi_x, aoi_y, aoi_width, aoi_height):
                    execution_tracer.log_attempt(strategy, element, "fail", error="match outside AOI")
                    return None
            execution_tracer.log_attempt(strategy, element, "success")
            return LocateResult(value, strategy, annotated_frame=annotated_frame)
        except Exception as e:
            execution_tracer.log_attempt(strategy, element, "fail", error=str(e))
            internal_logger.debug(f"Strategy {strategy.__class__.__name__} failed: {e}")
            raise

    def locate(self, element: str, aoi_x=None, aoi_y=None, aoi_width=None, aoi_height=None, index: int = 0) -> Generator[LocateResult, None, None]:
        effective_element, text_only = utils.parse_text_only_prefix(element)
        element_type = utils.determine_element_type(element)
        internal_logger.info(f"Locating element: {element} of type: {element_type}...")
        use_aoi = self._validate_aoi(aoi_x, aoi_y, aoi_width, aoi_height)

        yielded = False
        last_error: Exception | None = None
        for strategy in self.locator_strategies:
            if text_only and type(strategy).__name__ == "TextElementStrategy":
                continue
            execution_logger.debug(f"Trying strategy: {type(strategy).__name__} for element: {effective_element}")
            try:
                locate_result = self._try_strategy_locate(
                    strategy, effective_element, element_type, use_aoi, aoi_x, aoi_y, aoi_width, aoi_height, index
                )
            except Exception as e:
                raise_if_session_dead(e)
                # A missing OCR engine explains nothing when a native lookup also ran.
                if text_only or not _is_unconfigured_ocr(strategy):
                    last_error = e
                continue
            if locate_result:
                yielded = True
                yield locate_result
        if not yielded:
            hint = self._ocr_not_tried_hint(element_type, text_only)
            raise OpticsError(Code.E0201, message=f"Element '{element}' not found.{hint}", cause=last_error)

    def _ocr_not_tried_hint(self, element_type: str, text_only: bool) -> str:
        skipped = not text_only and any(
            _is_unconfigured_ocr(s) and s.supports(element_type, s.element_source) for s in self.locator_strategies
        )
        return OCR_NOT_TRIED_MSG if skipped else ""

    def assert_presence(self, elements: list, element_type: str, timeout: int = 30, rule: str = 'any'):
        """Assert elements are present -- possibly off-screen, anywhere in the page/DOM."""
        return self._assert(elements, element_type, timeout, rule, method_name="assert_elements")

    def assert_visibility(self, elements: list, element_type: str, timeout: int = 30, rule: str = 'any'):
        """Assert elements are actually rendered/visible on screen right now, not merely present."""
        return self._assert(elements, element_type, timeout, rule, method_name="assert_elements_visible")

    def _assert(self, elements: list, element_type: str, timeout: int, rule: str, method_name: str):
        """Poll every applicable strategy once per round until the rule is met or ``timeout`` runs out."""
        self._validate_rule(rule)
        rule = rule.lower()
        execution_logger.info(
            f"Asserting ({method_name}) elements: {elements} with rule: {rule} and timeout: {timeout}s")

        parsed = [utils.parse_text_only_prefix(el) for el in elements]
        effective_elements = [element for element, _ in parsed]
        text_only = any(flag for _, flag in parsed)
        strategies = [
            s for s in self._assertion_strategies(element_type, method_name, text_only)
            if any(s.can_assert(e) for e in effective_elements)
        ]
        covered = [e for e in effective_elements if any(s.can_assert(e) for s in strategies)]
        if not covered or (rule == "all" and len(covered) < len(effective_elements)):
            raise OpticsError(Code.E0201, message=f"No strategies found for elements: {elements} with rule '{rule}'.")
        native = [s for s in strategies if not _is_unconfigured_ocr(s)]
        ocr_skipped = not text_only and 0 < len(native) < len(strategies)
        if ocr_skipped:
            # OCR with no engine fails at once, and its error would hide the native lookups' miss.
            strategies = native

        state = _AssertState(
            elements, dict.fromkeys(effective_elements, False), strategies,
            visible=method_name == "assert_elements_visible", rule=rule, deadline=time.monotonic() + timeout,
        )
        while state.strategies:
            round_start = time.monotonic()
            result = self._assert_round(state)
            if result is not None:
                return result
            state.first_round = False
            now = time.monotonic()
            if now >= state.deadline:
                break
            time.sleep(min(max(0.0, round_start + _ASSERT_POLL_INTERVAL_S - now), state.deadline - now))

        for strategy in state.strategies:
            execution_tracer.log_attempt(strategy, str(elements), "fail", error="Elements not found.")
        hint = OCR_NOT_TRIED_MSG if ocr_skipped else ""
        raise OpticsError(
            Code.E0201, message=f"{elements} not found based on rule '{rule}'.{hint}", cause=state.last_exception
        )

    def _assertion_strategies(self, element_type: str, method_name: str, text_only: bool) -> list[LocatorStrategy]:
        """Locator strategies in config order, then one vision strategy per kind: they all read the same frame."""
        usable = [
            s for s in self.locator_strategies
            if self._can_strategy_assert_elements(s, element_type, method_name)
            and not (text_only and isinstance(s, TextElementStrategy))
        ]
        vision: dict[type, LocatorStrategy] = {}
        for strategy in usable:
            if _is_vision(strategy):
                vision.setdefault(type(strategy), strategy)
        return [s for s in usable if not _is_vision(s)] + list(vision.values())

    def _assert_round(self, state: _AssertState) -> tuple[bool, str, np.ndarray | None] | None:
        """Give each strategy one look at the elements still pending; the result once the rule is met."""
        state.frame = None
        state.vision_due = self._vision_due(state)
        checked = False
        for strategy in state.strategies.copy():
            pending = self._due_pending(strategy, state)
            if not pending:
                continue
            if checked and not state.first_round and time.monotonic() >= state.deadline:
                return None
            vision = _is_vision(strategy)
            frame = self._round_frame(state) if vision else None
            if vision and frame is None:
                continue
            checked = True
            result = self._check_with(strategy, pending, frame, state)
            if result is not None:
                return result
        return None

    @staticmethod
    def _due_pending(strategy: LocatorStrategy, state: _AssertState) -> list[str]:
        if _is_vision(strategy) and not state.vision_due:
            return []
        return [e for e, hit in state.found.items() if not hit and strategy.can_assert(e)]

    @staticmethod
    def _vision_due(state: _AssertState) -> bool:
        """Vision waits out its interval only while a cheaper strategy still looks for every pending element."""
        if state.last_vision is None or time.monotonic() - state.last_vision >= _VISION_INTERVAL_S:
            return True
        cheap = [s for s in state.strategies if not _is_vision(s)]
        return any(not any(s.can_assert(e) for s in cheap) for e, hit in state.found.items() if not hit)

    def _round_frame(self, state: _AssertState) -> np.ndarray | None:
        """The screenshot all vision strategies share this round; None when it cannot be taken or was already read."""
        if state.frame is None:
            started, timestamp = time.monotonic(), utils.get_timestamp()
            frame = self._vision_frame()
            if frame is None or _same_screen(frame, state.checked_frame):
                state.vision_due = False
                if frame is not None:
                    state.last_vision = started
                return None
            state.frame = state.checked_frame = frame
            state.last_vision, state.frame_ts = started, timestamp
        return state.frame

    def _vision_frame(self) -> np.ndarray | None:
        try:
            return self.capture_screenshot()
        except OpticsError as e:
            if e.code == Code.E0106:
                raise
            return None

    def _check_with(
        self, strategy: LocatorStrategy, pending: list[str], frame: np.ndarray | None, state: _AssertState
    ) -> tuple[bool, str, np.ndarray | None] | None:
        try:
            hits, annotated_frame = strategy.check_elements(pending, state.visible, frame)
        except Exception as e:
            raise_if_session_dead(e)
            execution_tracer.log_attempt(strategy, str(state.elements), "fail", error=str(e))
            state.last_exception = e
            state.strategies.remove(strategy)
            return None
        timestamp = state.frame_ts if frame is not None else utils.get_timestamp()
        hit_elements = [element for element, hit in zip(pending, hits) if hit]
        for element in hit_elements:
            state.found[element] = True
        if not (any if state.rule == "any" else all)(state.found.values()):
            return None
        execution_tracer.log_attempt(strategy, str(state.elements), "success")
        if annotated_frame is None:
            annotated_frame = strategy._annotated_screen(hit_elements)
        return True, timestamp, annotated_frame

    def _validate_rule(self, rule: str):
        """Validate the rule parameter."""
        rule = rule.lower()
        if rule not in ("any", "all"):
            raise OpticsError(Code.E0205, message="Invalid rule. Use 'any' or 'all'.")

    def _can_strategy_assert_elements(self, strategy, element_type: str, method_name: str = "assert_elements") -> bool:
        """Check if strategy can assert elements for the given element type."""
        if not strategy.supports(element_type, strategy.element_source):
            return False
        if method_name == "assert_elements_visible" and isinstance(strategy, (XPathStrategy, TextElementStrategy)):
            return LocatorStrategy._is_method_implemented(strategy.element_source, "assert_elements_visible")
        return True

    def capture_screenshot(self) -> Optional[np.ndarray]:
        """Capture a screenshot using the available strategies."""
        execution_logger.debug("Capturing screenshot using available strategies.")
        last_error: Exception | None = None
        for strategy in self.screenshot_strategies:
            try:
                img = strategy.capture()
                execution_tracer.log_attempt(strategy, "screenshot", "success")
                return img
            except Exception as e:
                execution_tracer.log_attempt(strategy, "screenshot", "fail", error=str(e))
                raise_if_session_dead(e)
                last_error = e
        internal_logger.debug("No screenshot captured.")
        raise OpticsError(Code.E0303, message="No screenshot captured using available strategies.", cause=last_error)

    def capture_screenshot_bytes(self) -> bytes:
        """Return the current screen as encoded PNG bytes.

        Delegates to :meth:`ElementSourceInterface.capture_screenshot_bytes` on each
        strategy's source. Backends with a native encoded path (Appium, Playwright,
        Selenium) override that method for efficiency; others encode the numpy frame
        via the default base-class implementation.
        """
        last_error: Exception | None = None
        for strategy in self.screenshot_strategies:
            source = strategy.element_source
            try:
                data = source.capture_screenshot_bytes()
                if data:
                    execution_tracer.log_attempt(strategy, "screenshot_bytes", "success")
                    return data
            except Exception as e:
                execution_tracer.log_attempt(strategy, "screenshot_bytes", "fail", error=str(e))
                internal_logger.debug(
                    "Screenshot bytes via %s failed: %s", source.__class__.__name__, e)
                raise_if_session_dead(e)
                last_error = e
        raise OpticsError(
            Code.E0303, message="No screenshot bytes captured using available strategies.", cause=last_error
        )

    def capture_pagesource(self) -> Optional[Tuple[str, str]]:
        last_error: Exception | None = None
        for strategy in self.pagesource_strategies:
            try:
                return strategy.capture_pagesource()
            except Exception as e:
                internal_logger.debug(
                    f"Pagesource capture failed with {strategy.__class__.__name__}: {e}")
                raise_if_session_dead(e)
                last_error = e
        internal_logger.debug("No pagesource captured.")
        raise OpticsError(Code.E0403, message="No pagesource captured using available strategies.", cause=last_error)

    @staticmethod
    def _can_strategy_get_interactive_elements(strategy: "PagesourceStrategy") -> bool:
        """Whether this strategy's source implements interactive-element extraction.

        A source belongs in ``pagesource_strategies`` if it can return a page source,
        which is a weaker capability than extracting elements from one: AppiumFindElement
        supports the former and unconditionally raises on the latter. Screening those out
        up front keeps the extraction path from paying for -- and logging -- a failure
        that is knowable from the source itself, rather than discovering it per call.
        """
        return LocatorStrategy._is_method_implemented(
            strategy.element_source, "get_interactive_elements"
        )

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None, compact: bool = False) -> List[dict]:
        """Retrieve interactive elements from the element source."""
        applicable_strategies = [
            s for s in self.pagesource_strategies
            if self._can_strategy_get_interactive_elements(s)
        ]
        last_error: Exception | None = None
        for strategy in applicable_strategies:
            try:
                return strategy.get_interactive_elements(filter_config, compact=compact)
            except Exception as e:
                internal_logger.debug(
                    f"Failed to retrieve interactive elements with {strategy.__class__.__name__}: {e}")
                raise_if_session_dead(e)
                last_error = e
        internal_logger.debug("No interactive elements retrieved.")
        raise OpticsError(
            Code.E0202, message="No interactive elements retrieved using available strategies.", cause=last_error
        )
