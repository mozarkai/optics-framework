from functools import wraps
import collections
import inspect
import shlex
import time
import json
from typing import Callable, Optional, Any, Tuple, List, Dict
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.strategies import StrategyManager
from optics_framework.common.base_factory import InstanceFallback
from optics_framework.common import utils
from optics_framework.common.ai_self_heal import AISelfHealHandler, HealContext, HealKeywordSpec
from optics_framework.common.error import OpticsError, Code
from .verifier import Verifier


def _parse_aoi_param(param: Any, default_value: float) -> float:
    if param is None or str(param).strip() in ('', 'None', 'none'):
        return default_value
    return float(param)


def _parse_aoi_from_kwargs(kwargs: dict) -> Tuple[float, float, float, float, int, bool]:
    aoi_x = _parse_aoi_param(kwargs.pop('aoi_x', '0'), 0)
    aoi_y = _parse_aoi_param(kwargs.pop('aoi_y', '0'), 0)
    aoi_width = _parse_aoi_param(kwargs.pop('aoi_width', '100'), 100)
    aoi_height = _parse_aoi_param(kwargs.pop('aoi_height', '100'), 100)
    index = int(kwargs.pop('index', 0))
    is_aoi_used = not (aoi_x == 0 and aoi_y == 0 and aoi_width == 100 and aoi_height == 100)
    return aoi_x, aoi_y, aoi_width, aoi_height, index, is_aoi_used


def _maybe_save_aoi_screenshot(
    screenshot_np: Any,
    aoi_x: float, aoi_y: float, aoi_width: float, aoi_height: float,
    execution_dir: str,
    func_name: str,
) -> None:
    annotated = utils.annotate_aoi_region(screenshot_np, aoi_x, aoi_y, aoi_width, aoi_height)
    utils.save_screenshot(annotated, f"{func_name}_with_aoi", output_dir=execution_dir)


def _locate_element(
    strategy_manager: StrategyManager,
    element: Any,
    aoi_x: float, aoi_y: float, aoi_width: float, aoi_height: float,
    index: int,
    is_aoi_used: bool,
):
    if is_aoi_used:
        return strategy_manager.locate(element, aoi_x, aoi_y, aoi_width, aoi_height, index=index)
    return strategy_manager.locate(element, index=index)


def _save_annotated_for_result(
    result: Any,
    screenshot_np: Any,
    execution_dir: str,
    func_name: str,
) -> None:
    annotated = getattr(result, "annotated_frame", None)
    if annotated is not None:
        utils.save_screenshot(
            annotated,
            f"{func_name}_image_detection_result",
            output_dir=execution_dir,
            time_stamp=utils.get_timestamp(),
        )
        return
    if result.is_coordinates:
        return
    element_source = getattr(result.strategy, "element_source", None)
    if element_source is None or not hasattr(element_source, "get_bbox_for_element"):
        return
    bbox = element_source.get_bbox_for_element(result.value)
    if bbox is None:
        return
    if screenshot_np is None:
        return
    # Element bbox is in the driver's window coordinate space; scale it to the
    # screenshot's pixel space before drawing (no-op when the two already match).
    bbox = utils.scale_bboxes_for_screenshot([bbox], element_source, screenshot_np)[0]
    framed = utils.annotate(screenshot_np.copy(), [bbox])
    utils.save_screenshot(
        framed,
        f"{func_name}_element_detection_result",
        output_dir=execution_dir,
        time_stamp=utils.get_timestamp(),
    )


def _try_results_until_success(
    results,
    func: Callable,
    self: Any,
    element: Any,
    args: tuple,
    kwargs: dict,
    screenshot_np: Any,
    execution_dir: str,
    func_name: str,
):
    last_exception = None
    result_count = 0
    locate_error: Optional[OpticsError] = None

    try:
        results_list = list(results)
    except OpticsError as e:
        # The locate generator raises when no strategy yields a result; treat that
        # as "no results" so self-heal below gets a chance, but keep the original
        # error as the cause instead of losing it.
        internal_logger.debug(f"Locate generator raised for '{element}' in '{func_name}': {e}")
        results_list = []
        locate_error = e

    for result in results_list:
        result_count += 1
        _save_annotated_for_result(result, screenshot_np, execution_dir, func_name)
        try:
            ret = func(self, element, located=result.value, *args, **kwargs)
        except Exception as e:
            internal_logger.error(
                f"Action '{func_name}' failed with {result.strategy.__class__.__name__}: {e}")
            last_exception = e
            continue
        # Success: record as a breadcrumb for any later AI self-heal (capture-then-return;
        # appending after a bare `return` would be unreachable).
        self._record_successful_step(func_name, element, args)
        return ret
    if result_count == 0:
        if self._ai_self_heal(element, func_name, args, kwargs, screenshot_np):
            return None
        raise OpticsError(
            Code.E0201,
            message=f"No valid strategies found for '{element}' in '{func_name}'",
            cause=locate_error,
        ) from locate_error
    if last_exception:
        if self._ai_self_heal(element, func_name, args, kwargs, screenshot_np):
            return None
        raise OpticsError(
            Code.X0201,
            message=f"All strategies failed for '{element}' in '{func_name}': {last_exception}",
            cause=last_exception,
        )
    raise OpticsError(Code.E0801, message=f"Unexpected failure: No results or exceptions for '{element}' in '{func_name}'")


# Action Executor Decorator
def with_self_healing(func: Callable) -> Callable:
    @wraps(func)
    def wrapper(self, element, *args, **kwargs):
        # Skip self-healing if 'located' is already provided (avoids double healing)
        if kwargs.get('located') is not None:
            return func(self, element, *args, **kwargs)

        screenshot_np = self._capture_screenshot_safe()
        aoi_x, aoi_y, aoi_width, aoi_height, index, is_aoi_used = _parse_aoi_from_kwargs(kwargs)
        if is_aoi_used and screenshot_np is not None:
            _maybe_save_aoi_screenshot(
                screenshot_np, aoi_x, aoi_y, aoi_width, aoi_height,
                self.execution_dir, func.__name__,
            )
        self._save_screenshot_if_available(screenshot_np, f"pre-{func.__name__}")
        results = _locate_element(
            self.strategy_manager, element,
            aoi_x, aoi_y, aoi_width, aoi_height, index, is_aoi_used,
        )
        return _try_results_until_success(
            results, func, self, element, args, kwargs,
            screenshot_np, self.execution_dir, func.__name__,
        )
    return wrapper


class _HealProviders:
    """Screenshot / page-source providers for one AI self-heal attempt.

    The first ``screenshot()`` returns the seed frame captured at failure time;
    later calls re-capture live. Both are best-effort and never raise.
    """

    def __init__(self, seed_png: bytes, strategy_manager: StrategyManager) -> None:
        self._seed_png: Optional[bytes] = seed_png
        self._strategy_manager = strategy_manager

    def screenshot(self) -> Optional[bytes]:
        if self._seed_png is not None:
            png, self._seed_png = self._seed_png, None
            return png
        try:
            return utils.encode_numpy_to_png_bytes(self._strategy_manager.capture_screenshot())
        except Exception:  # noqa: BLE001 - best-effort re-capture
            return None

    def pagesource(self) -> Optional[str]:
        try:
            ps = self._strategy_manager.capture_pagesource()
            return utils.strip_page_source(ps[0]) if ps else None
        except OpticsError:
            return None


class ActionKeyword:
    """
    High-Level API for Action Keywords

    This class provides functionality for managing action keywords related to applications,
    including pressing elements, scrolling, swiping, and text input.
    """
    SCREENSHOT_DISABLED_MSG = "Screenshot taking is disabled, not possible to locate element."
    XPAHT_NOT_SUPPORTED_MSG = "XPath is not supported for vision based search."

    def __init__(self, builder: OpticsBuilder):
        self.driver: InstanceFallback = builder.get_driver()
        self.element_source: InstanceFallback = builder.get_element_source()
        self.image_detection: Optional[InstanceFallback] = builder.get_image_detection()
        self.text_detection: Optional[InstanceFallback] = builder.get_text_detection()
        self.verifier = Verifier(builder)
        # Unwrap InstanceFallback to pass current_instance to StrategyManager

        self.strategy_manager = StrategyManager(
            self.element_source, self.text_detection, self.image_detection
        )
        self.execution_dir = builder.session_config.execution_output_path
        # AI self-heal (last-resort fallback). Inert unless explicitly toggled on AND an
        # llm_models entry is enabled. Fetching the LLM is guarded: a misconfiguration
        # (e.g. the optics-framework[llm] extra missing) must disable the feature, not
        # crash the run.
        self.ai_self_heal_enabled = bool(getattr(builder.session_config, "ai_self_heal", False))
        self._llm = None
        if self.ai_self_heal_enabled:
            try:
                self._llm = builder.get_llm()
            except Exception as e:  # noqa: BLE001 - degrade to inert, never fatal
                internal_logger.warning(
                    "AI self-heal enabled but no usable LLM (%s); self-heal disabled.", e
                )
        self._ai_healer: Optional[AISelfHealHandler] = None
        # Breadcrumbs of recently-succeeded keywords, fed to the LLM as context on heal.
        self._recent_steps: "collections.deque[Tuple[str, list]]" = collections.deque(maxlen=10)
        # Set by `_log_heal_outcome` when a heal succeeds; consumed (and cleared) by the
        # caller right after the keyword call via `_pop_last_heal_info`, so reporting
        # layers (TestRunner, LiveController) can attribute the heal to the right call
        # without it leaking onto the next, unrelated keyword.
        self._last_heal_info: Optional[Dict[str, str]] = None
        # The focused subset of keywords the self-healer is allowed to call, bound here
        # (rather than looked up by name via getattr) so a rename shows up as a static
        # attribute error instead of a silent "Unknown keyword" at runtime.
        self._heal_dispatch: Dict[str, Callable] = {
            "press_element": self.press_element,
            "enter_text": self.enter_text,
            "scroll": self.scroll,
            "swipe_by_percentage": self.swipe_by_percentage,
            "press_keycode": self.press_keycode,
            "press_by_percentage": self.press_by_percentage,
        }

    def _record_successful_step(self, func_name: str, element: Any, args: tuple) -> None:
        self._recent_steps.append((func_name, [str(element), *map(str, args)]))

    def _ai_self_heal(
        self, element: Any, func_name: str, args: tuple, kwargs: dict, screenshot_np: Any
    ) -> bool:
        """Last-resort AI recovery after every locate strategy failed. Returns True if healed.

        Fully inert (returns False, no LLM call) when the toggle is off, no LLM is enabled, or
        no screenshot is available. Never raises — on any problem it returns False so the caller
        re-raises the original element-not-found error and the param-fallback ladder continues.
        """
        if not self._ai_self_heal_ready(screenshot_np):
            return False
        try:
            seed_png = utils.encode_numpy_to_png_bytes(screenshot_np)
        except ValueError:
            return False

        providers = _HealProviders(seed_png, self.strategy_manager)
        ctx = HealContext(
            intent_keyword=func_name,
            intent_params=[str(a) for a in args],
            element=str(element),
            recent_steps=list(self._recent_steps),
            failed_strategies=sorted(
                {type(s).__name__ for s in self.strategy_manager.locator_strategies}
            ),
        )
        if self._ai_healer is None:
            self._ai_healer = AISelfHealHandler(
                self._llm,
                keyword_executor=self._heal_execute,
                keyword_catalog=self._heal_catalog,
            )
        internal_logger.info("AI self-heal: attempting recovery for '%s' on '%s'", func_name, element)
        result = self._ai_healer.heal(ctx, providers.screenshot, providers.pagesource)
        self._log_heal_outcome(result, func_name, element, args)
        return result.ok

    def _ai_self_heal_ready(self, screenshot_np: Any) -> bool:
        """True only when self-heal can run: toggle on, an LLM instance, and a screenshot."""
        if not self.ai_self_heal_enabled or self._llm is None or screenshot_np is None:
            return False
        return bool(getattr(self._llm, "instances", None))

    def _log_heal_outcome(self, result: Any, func_name: str, element: Any, args: tuple) -> None:
        """Log the heal result; on success, record the step and expose it for reporting."""
        if not result.ok:
            internal_logger.info(
                "AI self-heal: could not recover '%s': %s", func_name, result.message
            )
            return
        action = result.action
        kw = action.keyword if action else "?"
        internal_logger.info("AI self-heal: recovered '%s' via keyword '%s'", func_name, kw)
        self._record_successful_step(func_name, element, args)
        steps = result.steps_taken or [kw]
        self._last_heal_info = {
            "summary": (
                f"AI self-heal recovered '{func_name}' after {len(steps)} "
                f"step{'s' if len(steps) != 1 else ''}: {'; '.join(steps)}"
            ),
        }

    def _pop_last_heal_info(self) -> Optional[Dict[str, str]]:
        """Return and clear the self-heal info recorded for the most recently completed call.

        Callers (``TestRunner``, ``LiveController``) must call this immediately after every
        keyword invocation, self-healing or not, so a heal on one keyword never leaks onto
        the reporting for the next.
        """
        info, self._last_heal_info = self._last_heal_info, None
        return info

    # -- Self-heal keyword executor and catalog --------------------------------

    def _heal_execute(self, line: str) -> Any:
        """Keyword executor for self-heal: parse and run one keyword, returning an ExecResult-like.

        Calls the ActionKeyword methods directly but avoids re-triggering self-heal by
        passing ``located=`` for self-healing-decorated methods or calling non-decorated ones.

        Returns an object with ``.ok`` (bool) and ``.message`` (str).
        """
        from dataclasses import dataclass as _dc

        @_dc
        class _Result:
            ok: bool
            message: str = ""

        try:
            tokens = shlex.split(line, posix=True)
        except ValueError as exc:
            return _Result(ok=False, message=f"Parse error: {exc}")
        if not tokens:
            return _Result(ok=False, message="Empty keyword line")

        keyword_name = tokens[0]
        params = tokens[1:]

        method = self._heal_dispatch.get(keyword_name)
        if method is None:
            return _Result(ok=False, message=f"Unknown keyword: {keyword_name}")

        try:
            # Non-self-healing methods: call directly with params.
            # Self-healing methods (press_element, enter_text): they will go through
            # `with_self_healing` decorator, but self-heal is already running, so
            # we need to temporarily disable it to avoid recursion.
            original_enabled = self.ai_self_heal_enabled
            self.ai_self_heal_enabled = False
            try:
                method(*params)
            finally:
                self.ai_self_heal_enabled = original_enabled
            return _Result(ok=True)
        except OpticsError as exc:
            return _Result(ok=False, message=str(exc.message))
        except Exception as exc:  # noqa: BLE001 - never crash the heal loop
            return _Result(ok=False, message=str(exc))

    def _heal_catalog(self) -> List[HealKeywordSpec]:
        """Return the keyword catalog for the self-healer (focused subset with signatures)."""
        return [
            HealKeywordSpec(name=name, signature=self._heal_keyword_signature(name, method))
            for name, method in self._heal_dispatch.items()
        ]

    @staticmethod
    def _heal_keyword_signature(name: str, method: Callable) -> str:
        """Build a ghost-text signature like ``press_element <element> [repeat] [offset_x]``."""
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            return name
        parts: List[str] = [name]
        for pname, param in sig.parameters.items():
            if pname == "self":
                continue
            # Skip internal keyword-only params (located, event_name, aoi_*)
            if param.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_KEYWORD):
                continue
            if pname in ("event_name", "aoi_x", "aoi_y", "aoi_width", "aoi_height"):
                continue
            if param.default is inspect.Parameter.empty:
                parts.append(f"<{pname}>")
            else:
                parts.append(f"[{pname}]")
        return " ".join(parts)

    def _capture_screenshot_safe(self) -> Any:
        """Capture a screenshot, returning None on failure (e.g. secure/protected pages)."""
        try:
            return self.strategy_manager.capture_screenshot()
        except Exception as e:
            internal_logger.warning(f"Screenshot capture failed, continuing without it: {e}")
            return None

    def _save_screenshot_if_available(self, screenshot_np: Any, name: str) -> None:
        """Save a screenshot only if one was successfully captured."""
        if screenshot_np is not None:
            utils.save_screenshot(screenshot_np, name, output_dir=self.execution_dir)

    # Click actions
    @with_self_healing
    def press_element(
        self, element: str, repeat: str = "1", offset_x: str = "0", offset_y: str = "0", index: str = "0", aoi_x: str = "0",
        aoi_y: str = "0", aoi_width: str = "100", aoi_height: str = "100", event_name: Optional[str] = None,
        *, located: Any = None
        ) -> None:
        """
        Press a specified element.

        :param element: The element to be pressed (text, xpath or image).
        :param repeat: Number of times to repeat the press.
        :param offset_x: X offset of the press.
        :param offset_y: Y offset of the press.
        :param index: Index of the element if multiple matches are found.
        :param event_name: The event triggering the press.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        """
        if isinstance(located, tuple):
            x, y = located
            internal_logger.info(
                f"Pressing at coordinates ({x + int(offset_x)}, {y + int(offset_y)}) with offset ({offset_x}, {offset_y})")
            self.driver.press_coordinates(
                x + int(offset_x), y + int(offset_y), event_name)
        else:
            internal_logger.info(f"Pressing element '{element}'")
            self.driver.press_element(located, int(repeat), event_name)

    def press_by_percentage(self, percent_x: str, percent_y: str, repeat: str = "1", event_name: Optional[str] = None) -> None:
        """
        Press an element by percentage coordinates.

        :param percent_x: X percentage of the press (as string, will be converted to float).
        :param percent_y: Y percentage of the press (as string, will be converted to float).
        :param repeat: Number of times to repeat the press.
        :param event_name: The event triggering the press.
        """
        screenshot_np = self._capture_screenshot_safe()
        self._save_screenshot_if_available(screenshot_np, "press_by_percentage")
        self.driver.press_percentage_coordinates(
            float(percent_x), float(percent_y), int(repeat), event_name
        )

    def press_by_coordinates(self, coor_x: str, coor_y: str, repeat: str = "1", event_name: Optional[str] = None) -> None:
        """
        Press an element by absolute coordinates.

        :param coor_x: X coordinate of the press.
        :param coor_y: Y coordinate of the press.
        :param repeat: Number of times to repeat the press.
        :param event_name: The event triggering the press.
        """
        screenshot_np = self._capture_screenshot_safe()
        self._save_screenshot_if_available(screenshot_np, "press_by_coordinates")
        internal_logger.info(f'Pressing by coordinates: ({coor_x}, {coor_y})')
        self.driver.press_coordinates(int(coor_x), int(coor_y), event_name)


    def detect_and_press(self, element: str, timeout: str = "30", event_name: Optional[str] = None) -> None:
        """
        Detect and press a specified element.

        :param element: The element to be detected and pressed (Image template, OCR template, or XPath).
        :param timeout: Timeout for the detection operation.
        :param event_name: The event triggering the press.
        """
        try:
            result = self.verifier.validate_screen(
                element, timeout, rule="any")
        except Exception as e:
            internal_logger.error(f"Error in detect_and_press: {e}")
            result = False
        if result:
            internal_logger.info(f'Element {element} detected. Performing Press ... ')
            self.press_element(element, event_name=event_name)
        else:
            internal_logger.info(f'Element {element} not found. Press is not performed.')

    @DeprecationWarning
    @with_self_healing
    def press_checkbox(self, element: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                       aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Press a specified checkbox element.

        :param element: The checkbox element (Image template, OCR template, or XPath).
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the press.
        """
        self.press_element(element, aoi_x=aoi_x, aoi_y=aoi_y, aoi_width=aoi_width,
                          aoi_height=aoi_height, event_name=event_name, located=located)

    @DeprecationWarning
    @with_self_healing
    def press_radio_button(self, element: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                           aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Press a specified radio button.

        :param element: The radio button element (Image template, OCR template, or XPath).
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the press.
        """
        self.press_element(element, aoi_x=aoi_x, aoi_y=aoi_y, aoi_width=aoi_width,
                          aoi_height=aoi_height, event_name=event_name, located=located)

    def select_dropdown_option(self, element: str, option: str, event_name: Optional[str] = None) -> None:
        """
        Open a dropdown and select one of its options.

        Opens the dropdown by pressing ``element``, then validates that ``option`` is visible
        in the page source before pressing it. Raises ``OpticsError`` (``E0201``) when the
        option text is not found among the dropdown's visible items, preventing a silent
        mis-selection. Falls back to pressing without validation when page source is unavailable.

        :param element: The dropdown element (Image template, OCR template, or XPath).
        :param option: The option to select (visible label, OCR/Image template, or XPath).
        :param event_name: The event triggering the selection.
        """
        internal_logger.info(f"Selecting '{option}' from dropdown '{element}'")
        self.press_element(element, event_name=event_name)
        self._assert_option_in_dropdown(element, option)
        self.press_element(option, event_name=event_name)

    def _assert_option_in_dropdown(self, dropdown_element: str, option: str) -> None:
        """Validate that option text is present in the open dropdown via page source."""
        try:
            interactive = self.strategy_manager.get_interactive_elements()
        except (OpticsError, NotImplementedError):
            return
        option_normalized = option.strip().lower()
        available_texts = [
            el.get("text") or "" for el in interactive if isinstance(el, dict)
        ]
        if not any(option_normalized == t.strip().lower() for t in available_texts if t):
            raise OpticsError(
                Code.E0201,
                message=(
                    f"Option '{option}' not found in dropdown '{dropdown_element}'. "
                    f"Available options: {[t for t in available_texts if t]}"
                ),
            )

    # Swipe and Scroll actions
    def swipe(self, coor_x: str, coor_y: str, direction: str = 'right', swipe_length: str = "50", event_name: Optional[str] = None) -> None:
        """
        Perform a swipe action in a specified direction.

        :param coor_x: X coordinate of the swipe.
        :param coor_y: Y coordinate of the swipe.
        :param direction: The swipe direction (up, down, left, right).
        :param swipe_length: The length of the swipe.
        :param event_name: The event triggering the swipe.
        """
        screenshot_np = self._capture_screenshot_safe()
        self._save_screenshot_if_available(screenshot_np, "swipe")
        internal_logger.info(f'Swiping from ({coor_x}, {coor_y}) to the {direction} with length {swipe_length}')
        self.driver.swipe(int(coor_x), int(coor_y), direction, int(swipe_length), event_name)

    def swipe_by_percentage(self, percent_x: str, percent_y: str, direction: str = 'right', swipe_length: str = "50", event_name: Optional[str] = None) -> None:
        """
        Perform a swipe action in a specified direction by percentage.

        :param percent_x: X percentage of the swipe.
        :param percent_y: Y percentage of the swipe.
        :param direction: The swipe direction (up, down, left, right).
        :param swipe_length: The length of the swipe.
        :param event_name: The event triggering the swipe.
        """
        screenshot_np = self._capture_screenshot_safe()
        self._save_screenshot_if_available(screenshot_np, "swipe_percentage")
        internal_logger.info(f'Swiping from ({percent_x}, {percent_y}) to the {direction} with length {swipe_length}')
        self.driver.swipe_percentage(int(percent_x), int(percent_y), direction, int(swipe_length), event_name)

    @DeprecationWarning
    def swipe_seekbar_to_right_android(self, element: str, event_name: Optional[str] = None) -> None:
        """
        Swipe a seekbar to the right.

        :param element: The seekbar element (Image template, OCR template, or XPath).
        """
        screenshot_np = self._capture_screenshot_safe()
        self._save_screenshot_if_available(screenshot_np, "swipe_seekbar_to_right_android")
        internal_logger.info(f'Swiping seekbar element: {element} to the right')
        self.driver.swipe_element(element, 'right', 50, event_name)

    def swipe_until_element_appears(self, element: str, direction: str, timeout: str, event_name: Optional[str] = None) -> None:
        """
        Swipe in a specified direction until an element appears.

        :param element: The target element (Image template, OCR template, or XPath).
        :param direction: The swipe direction (up, down, left, right).
        :param timeout: Timeout until element search is performed.
        :param event_name: The event triggering the swipe.
        """
        screenshot_np = self._capture_screenshot_safe()
        self._save_screenshot_if_available(screenshot_np, "swipe_until_element_appears")
        start_time = time.time()
        found = False
        while time.time() - start_time < int(timeout):
            try:
                result = self.verifier.assert_presence(
                    element, timeout_str="3", rule="any")
                if result:
                    found = True
                    break
            except OpticsError as e:
                if e.code != Code.E0201:
                    raise
            self.driver.swipe_percentage(10, 50, direction, 25, event_name)
            time.sleep(3)
        if not found:
            raise OpticsError(Code.E0201, message=f"Element '{element}' did not appear after swiping {direction} for {timeout}s.")

    @with_self_healing
    def swipe_from_element(self, element: str, direction: str, swipe_length: str, aoi_x: str = "0", aoi_y: str = "0",
                          aoi_width: str = "100", aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Perform a swipe action starting from a specified element.

        :param element: The element to swipe from (Image template, OCR template, or XPath).
        :param direction: The swipe direction (up, down, left, right).
        :param swipe_length: The length of the swipe.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the swipe.
        """
        if isinstance(located, tuple):
            x, y = located
            internal_logger.debug(f"Swiping from coordinates ({x}, {y})")
            self.driver.swipe(x, y, direction, int(swipe_length), event_name)
        else:
            internal_logger.debug(f"Swiping from element '{element}'")
            self.driver.swipe_element(
                located, direction, int(swipe_length), event_name)

    def scroll(self, direction: str, event_name: Optional[str] = None) -> None:
        """
        Perform a scroll action in a specified direction.

        :param direction: The scroll direction (up, down, left, right).
        :param event_name: The event triggering the scroll.
        """
        screenshot_np = self._capture_screenshot_safe()
        self._save_screenshot_if_available(screenshot_np, "scroll")
        internal_logger.info(f"Scrolling {direction} with event {event_name}")
        self.driver.scroll(direction, 1000, event_name)

    def scroll_until_element_appears(self, element: str, direction: str, timeout: str, event_name: Optional[str] = None) -> None:
        """
        Scroll in a specified direction until an element appears.

        :param element: The target element (Image template, OCR template, or XPath).
        :param direction: The scroll direction (up, down, left, right).
        :param timeout: Timeout for the scroll operation.
        :param event_name: The event triggering the scroll.
        """
        screenshot_np = self._capture_screenshot_safe()
        self._save_screenshot_if_available(screenshot_np, "scroll_until_element_appears")
        start_time = time.time()
        found = False
        while time.time() - start_time < int(timeout):
            try:
                result = self.verifier.assert_presence(
                    element, timeout_str="3", rule="any")
                if result:
                    found = True
                    break
            except OpticsError as e:
                if e.code != Code.E0201:
                    raise
                # Don't hide configuration/strategy issues; scrolling won't fix these.
                if "No strategies found" in e.message or "No valid strategies found" in e.message:
                    raise
            self.driver.scroll(direction, 1000, event_name)
            time.sleep(3)
        if not found:
            raise OpticsError(Code.E0201, message=f"Element '{element}' did not appear after scrolling {direction} for {timeout}s.")

    @with_self_healing
    def scroll_from_element(self, element: str, direction: str, scroll_length: str, aoi_x: str = "0", aoi_y: str = "0",
                           aoi_width: str = "100", aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Perform a scroll action starting from a specified element.

        :param element: The element to scroll from (Image template, OCR template, or XPath).
        :param direction: The scroll direction (up, down, left, right).
        :param scroll_length: The length of the scroll.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the scroll.
        """
        if isinstance(located, tuple):
            x, y = located
            internal_logger.debug(f"Swiping from coordinates ({x}, {y})")
            self.driver.swipe(x, y, direction, int(scroll_length), event_name)
        else:
            internal_logger.debug(f"Swiping from element '{element}'")
            self.driver.swipe_element(
                located, direction, int(scroll_length), event_name)

    # Text input actions
    @with_self_healing
    def enter_text(self, element: str, text: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                   aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Enter text into a specified element.

        :param element: The target element (Image template, OCR template, or XPath).
        :param text: The text to be entered.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the input.
        """

        parsed = utils.parse_special_key(text)
        if parsed is not None:
            text = parsed

        if isinstance(located, tuple):
            x, y = located
            internal_logger.debug(f"Entering text '{text}' at coordinates ({x}, {y})")
            self.driver.press_coordinates(x, y, event_name=event_name)
            self.driver.enter_text(text, event_name)
        else:
            internal_logger.debug(f"Entering text '{text}' into element '{element}'")
            self.driver.enter_text_element(located, text, event_name)

    def enter_text_direct(self, text: str, event_name: Optional[str] = None) -> None:
        """
        Enter text using the keyboard.

        :param text: The text to be entered.
        :param event_name: The event triggering the input.
        """
        try:
            screenshot_np = self.strategy_manager.capture_screenshot()
            utils.save_screenshot(screenshot_np, "enter_text_keyboard", output_dir=self.execution_dir)
        except Exception as e:
            internal_logger.error(f"Error capturing screenshot: {e}")
        internal_logger.info(f'Entering text directly: {text}')
        self.driver.enter_text(text, event_name)

    def enter_text_using_keyboard(self, text_input: str, event_name: Optional[str] = None) -> None:
        """
        Enter text or press a special key using the keyboard.

        If the input is a string that includes angle brackets (e.g., '<enter>'),
        the text between the brackets will be interpreted as a special key name and mapped accordingly.

        :param input: The text or special key identifier to send.
        :param event_name: Optional event label for logging.
        """

        parsed = utils.parse_special_key(text_input)
        if parsed is not None:
            text_input = parsed
        try:
            screenshot_np = self.strategy_manager.capture_screenshot()
            utils.save_screenshot(screenshot_np, "enter_text_using_keyboard", output_dir=self.execution_dir)
        except Exception as e:
            internal_logger.error(f"Error capturing screenshot: {e}")
        internal_logger.info(f'Entering text using keyboard: {text_input}')
        self.driver.enter_text_using_keyboard(text_input, event_name)

    @with_self_healing
    def enter_number(self, element: str, number: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                     aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Enter a specified number into an element.

        :param element: The target element (Image template, OCR template, or XPath).
        :param number: The number to be entered.
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the input.
        """
        if isinstance(located, tuple):
            x, y = located
            internal_logger.debug(f"Entering number '{number}' at coordinates ({x}, {y})")
            self.driver.press_coordinates(x, y, event_name=event_name)
            self.driver.enter_text(str(number), event_name)
        else:
            internal_logger.debug(f"Entering number '{number}' into element '{element}'")
            self.driver.enter_text_element(located, str(number), event_name)

    def press_keycode(self, keycode: str, event_name: Optional[str] = None) -> None:
        """
        Press a specified keycode.

        :param keycode: The keycode to be pressed.
        :param event_name: The event triggering the press.
        """
        try:
            screenshot_np = self.strategy_manager.capture_screenshot()
            utils.save_screenshot(screenshot_np, "press_keycode", output_dir=self.execution_dir)
        except Exception as e:
            internal_logger.error(f"Error capturing screenshot: {e}")

        internal_logger.info(f"Pressing keycode: {keycode}")
        self.driver.press_keycode(keycode, event_name)


    @with_self_healing
    def clear_element_text(self, element: str, aoi_x: str = "0", aoi_y: str = "0", aoi_width: str = "100",
                          aoi_height: str = "100", event_name: Optional[str] = None, *, located: Any=None) -> None:
        """
        Clear text from a specified element.

        :param element: The target element (Image template, OCR template, or XPath).
        :param aoi_x: X percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_y: Y percentage of Area of Interest top-left corner (0-100). Default: 0.
        :param aoi_width: Width percentage of Area of Interest (0-100). Default: 100.
        :param aoi_height: Height percentage of Area of Interest (0-100). Default: 100.
        :param event_name: The event triggering the action.
        """
        if isinstance(located, tuple):
            x, y = located
            internal_logger.debug(f"Clearing text at coordinates ({x}, {y})")
            self.driver.press_coordinates(
                x, y, event_name=event_name)
            self.driver.clear_text(event_name)
        else:
            internal_logger.debug(f"Clearing text from element '{element}'")
            self.driver.clear_text_element(located, event_name)

    @with_self_healing
    def get_text(self, element: str, *, located=None) -> Optional[str]:
        """
        Get the text from a specified element.

        :param element: The target element (Image template, OCR template, or XPath).
        :return: The text from the element or None if not supported.
        """
        if located is None:
            internal_logger.error('get_text: located is None, element could not be found.')
            return None
        if isinstance(located, tuple):
            internal_logger.error('Get Text is not supported for coordinate-based (vision) results.')
            return None
        return self.driver.get_text_element(located)

    def sleep(self, duration: str) -> None:
        """
        Sleep for a specified duration.

        :param duration: The duration of the sleep in seconds.
        """
        time.sleep(int(duration))

    def _parse_script_and_args(self, script_or_json: str) -> tuple[str, list]:
        """
        Parse script and args from JSON string or return as-is.

        :param script_or_json: The JavaScript code/script command, or a JSON string.
        :return: Tuple of (script, args)
        """
        script = script_or_json
        args = []

        script_stripped = script_or_json.strip()
        if not script_stripped.startswith('{'):
            return script, args

        try:
            parsed = json.loads(script_or_json)
            if not isinstance(parsed, dict):
                return script, args

            script = parsed.get("script", script_or_json)
            if "args" not in parsed:
                return script, args

            args_value = parsed["args"]
            if isinstance(args_value, list) and len(args_value) == 1 and isinstance(args_value[0], dict):
                args = [args_value[0]]
            elif isinstance(args_value, dict):
                args = [args_value]
            else:
                args = [args_value] if not isinstance(args_value, list) else args_value

            internal_logger.debug(f'Parsed JSON: script="{script}", args={args}')
        except json.JSONDecodeError as e:
            internal_logger.debug(f'Not valid JSON, using as script directly: {e}')

        return script, args

    def execute_script(self, script_or_json: str, event_name: Optional[str] = None) -> Any:
        """
        Execute JavaScript/script in the current context.

        :param script_or_json: The JavaScript code/script command, or a JSON string containing
                               {"script": "...", "args": {...}} or {"script": "..."}.
                               Examples:
                               - "mobile:pressKey" (plain script)
                               - '{"script": "mobile:pressKey", "args": {"keycode": 3}}' (JSON with args)
                               - '{"script": "mobile:clear"}' (JSON without args)
        :type script_or_json: str
        :param event_name: The event triggering the script execution, if any.
        :type event_name: Optional[str]
        :return: The result of the script execution.
        :rtype: Any
        """
        try:
            screenshot_np = self.strategy_manager.capture_screenshot()
            utils.save_screenshot(screenshot_np, "execute_script", output_dir=self.execution_dir)
        except Exception as e:
            internal_logger.error(f"Error capturing screenshot: {e}")

        script, args = self._parse_script_and_args(script_or_json)

        internal_logger.info(f'Executing script: {script[:100]}...')  # Log first 100 chars
        if args:
            result = self.driver.execute_script(script, *args, event_name=event_name)
        else:
            result = self.driver.execute_script(script, event_name=event_name)
        internal_logger.debug(f'Script execution result: {result}')
        return result
