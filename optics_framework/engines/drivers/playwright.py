import asyncio
from typing import Optional, Any
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.eventSDK import EventSDK
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.async_utils import run_async
from optics_framework.common import utils


class Playwright(DriverInterface):
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "playwright"
    PAGE_NOT_INITIALIZED_MSG = "Playwright page not initialized"


    def __init__(self, config: dict, event_sdk: Optional[EventSDK] = None):
        self.config = config or {}
        self.event_sdk = event_sdk

        self._pw = None
        self._browser = None
        self._context = None
        self.page: Optional[Page] = None

        internal_logger.info("[Playwright] Driver initialized")

    # =====================================================
    # APP / SESSION
    # =====================================================

    def launch_app(self, app_identifier=None, app_activity=None, event_name=None):
        return run_async(self._launch_app_async(app_identifier, event_name))

    async def _launch_app_async(self, app_identifier, event_name):
        try:
            internal_logger.info("[Playwright] Launching browser")

            self._pw = await async_playwright().start()

            browser = self.config.get("browser", "chromium")
            headless = True
            viewport = self.config.get("viewport", {"width": 1280, "height": 800})

            self._browser = await getattr(self._pw, browser).launch(headless=headless)
            self._context = await self._browser.new_context(viewport=viewport)
            self.page = await self._context.new_page()
            self.page.set_default_navigation_timeout(60000)
            self.page.set_default_timeout(60000)

            if app_identifier:
                internal_logger.debug("[Playwright] Navigating to %s", app_identifier)
                await self._navigate_to(app_identifier)

            if event_name and self.event_sdk:
                self.event_sdk.capture_event(event_name)

            internal_logger.info("[Playwright] Application launched")
            return "PLAYWRIGHT_SESSION"

        except asyncio.CancelledError:
            # Clean up resources if coroutine is cancelled
            await self._cleanup_resources()
            raise
        except Exception as e:
            # Clean up any partially initialized resources on error
            await self._cleanup_resources()
            internal_logger.debug("[Playwright] Launch failed", exc_info=True)
            raise OpticsError(Code.E0102, str(e), cause=e)

    async def _launch_other_app_async(self, app_name: str, event_name=None):
        """
        Launch another app in a new tab (not a new window).
        Creates a new page in the existing browser context.
        """
        try:
            if self._context is None:
                # If no context exists, initialize browser first
                internal_logger.info("[Playwright] No existing context, initializing browser")
                await self._launch_app_async(None, event_name)

            # Create a new page (tab) in the existing context
            internal_logger.debug("[Playwright] Creating new tab for %s", app_name)
            self.page = await self._context.new_page()
            # ⭐ very important stability fix

            # Navigate to the new URL
            if app_name:
                internal_logger.debug("[Playwright] Navigating to %s", app_name)
                await self._navigate_to(app_name)

            if event_name and self.event_sdk:
                self.event_sdk.capture_event(event_name)

            internal_logger.info("[Playwright] Launched other app in new tab")

        except Exception as e:
            internal_logger.debug("[Playwright] Failed to launch other app: %s", e, exc_info=True)
            raise OpticsError(Code.E0102, str(e), cause=e)

    def launch_other_app(self, app_name: str, event_name=None):
        return run_async(self._launch_other_app_async(app_name, event_name))

    async def _navigate_to(self, url: str):
        """
        Navigate to a URL with configurable timeout and wait condition.
        If navigation times out but the URL is reached, continue with a warning.
        """
        if not self.page:
            raise OpticsError(Code.E0102, self.PAGE_NOT_INITIALIZED_MSG)

        timeout_ms = int(self.config.get("navigation_timeout_ms", 60000))
        wait_until = self.config.get("navigation_wait_until", "domcontentloaded")

        try:
            await self.page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except PlaywrightTimeoutError as e:
            current_url = self.page.url or ""
            if current_url and url in current_url:
                internal_logger.warning(
                    "[Playwright] Navigation timed out (wait_until=%s, timeout_ms=%s) "
                    "but page URL is already set. Continuing.",
                    wait_until,
                    timeout_ms,
                )
                return
            raise OpticsError(Code.E0102, str(e), cause=e)

    def get_app_version(self) -> str:
        return "get_app_version not supported for Playwright"


    def _normalize_locator(self, element):
        """
        Normalize a selector string for Playwright locator.

        If element is already a Playwright locator object, return it as-is.
        If element is a string and is an XPath, prefix it with 'xpath='.
        Otherwise, return the string as-is.

        Args:
            element: String selector or Playwright locator object

        Returns:
            String selector (with xpath= prefix if needed) or locator object
        """
        # If it's already a locator object, return as-is
        if not isinstance(element, str):
            return element

        # Check if it's an XPath selector
        element_type = utils.determine_element_type(element)
        if element_type == "XPath":
            # If it doesn't already have the xpath= prefix, add it
            if not element.lower().startswith("xpath="):
                return f"xpath={element}"
        # plain visible text support (important for YouTube test)
        if isinstance(element, str) and not any(element.startswith(p) for p in ("css=", "xpath=", "text=", "//", "input", "#", ".")):
            return f"text={element}"
        return element

    # =====================================================
    # PRESS / CLICK
    # =====================================================

    def press_element(self, element: str, repeat: int = 1, event_name=None):
        run_async(self._press_element_async(element, repeat, event_name))

    async def _press_element_async(self, element, repeat, event_name):
        # Handle both string selectors and Playwright locator objects
        if isinstance(element, str):
            normalized = self._normalize_locator(element)
            locator = self.page.locator(normalized)
        else:
            # element is already a Playwright locator object
            locator = element
        await locator.wait_for(state="visible", timeout=15000)

        for _ in range(repeat):
            await locator.click(force=True)

        if event_name and self.event_sdk:
            self.event_sdk.capture_event(event_name)

    def press_coordinates(self, x: int, y: int, event_name=None):
        run_async(self.page.mouse.click(x, y))

    def press_percentage_coordinates(self, px, py, repeat=1, event_name=None):
        run_async(self._press_percentage_async(px, py, repeat))

    async def _press_percentage_async(self, px, py, repeat):
        vp = self.page.viewport_size
        x = int(vp["width"] * px / 100)
        y = int(vp["height"] * py / 100)
        for _ in range(repeat):
            await self.page.mouse.click(x, y)

    def press_keycode(self, keycode: str, event_name=None):
        """
        Press a keycode/key. Supports common keys like "Enter", "Tab", "Escape", etc.
        For Playwright, we use keyboard.press() which accepts key names.
        """
        # Map common keycode names to Playwright key names
        key_map = {
            "Enter": "Enter",
            "Return": "Enter",
            "Tab": "Tab",
            "Escape": "Escape",
            "Backspace": "Backspace",
            "Delete": "Delete",
            "Space": " ",
            "ArrowUp": "ArrowUp",
            "ArrowDown": "ArrowDown",
            "ArrowLeft": "ArrowLeft",
            "ArrowRight": "ArrowRight",
        }

        # Use mapped key or the keycode string directly (Playwright accepts key names)
        key = key_map.get(keycode, keycode)
        run_async(self.page.keyboard.press(key))
        # wait for youtube search results page load
        try:
            run_async(self.page.wait_for_load_state("networkidle", timeout=10000))
        except Exception:
            run_async(self.page.wait_for_timeout(4000))
        # stabilize search navigation
        run_async(self.page.wait_for_timeout(3000))

        if event_name and self.event_sdk:
            self.event_sdk.capture_event(event_name)

    # =====================================================
    # TEXT INPUT
    # =====================================================

    def enter_text(self, text: str, event_name=None):
        run_async(self.page.keyboard.type(text))

    def enter_text_using_keyboard(self, text: str, event_name=None):
        run_async(self.page.keyboard.type(text))

    def enter_text_element(self, element: str, text: str, event_name=None):
        normalized = self._normalize_locator(element)
        run_async(self.page.locator(normalized).fill(text))

    def clear_text(self, event_name=None):
        run_async(self.page.keyboard.press("Control+A"))
        run_async(self.page.keyboard.press("Backspace"))

    def clear_text_element(self, element: str, event_name=None):
        normalized = self._normalize_locator(element)
        run_async(self.page.locator(normalized).fill(""))

    # =====================================================
    # SCROLL / SWIPE
    # =====================================================

    def swipe(self, x, y, direction, swipe_length, event_name=None):
        delta = swipe_length if direction == "down" else -swipe_length
        run_async(self.page.mouse.wheel(0, delta))

    def swipe_percentage(self, x_per: int, y_per: int, direction: str, swipe_length_percentage: int, event_name=None):
        run_async(self._swipe_percentage_async(direction, swipe_length_percentage))

    async def _swipe_percentage_async(self, direction: str, swipe_length_percentage: int):
        vp = self.page.viewport_size
        delta = int(vp["height"] * swipe_length_percentage / 100)
        if direction != "down":
            delta = -delta
        await self.page.mouse.wheel(0, delta)

    def swipe_element(self, element: str, direction: str, swipe_length: int, event_name=None):
        """
        Playwright-safe swipe_element:
        Translates swipe into scroll on the element's nearest scrollable container.
        """

        if not self.page:
            raise RuntimeError(self.PAGE_NOT_INITIALIZED_MSG)

        # 🔒 Web rule: swipe == scroll
        try:
            run_async(
                self._swipe_element_async(
                    element=element,
                    direction=direction,
                    swipe_length=swipe_length
                )
            )
        except Exception as e:
            raise RuntimeError(
                f"swipe_element failed for Playwright. "
                f"Use scroll_from_element instead. Root cause: {e}"
            ) from e

    async def _swipe_element_async(self, element: str, direction: str, swipe_length: int):
        normalized = self._normalize_locator(element)
        locator = self.page.locator(normalized)
        await locator.wait_for(state="visible", timeout=10000)

        # Calculate scroll delta
        delta = swipe_length if direction == "down" else -swipe_length

        # Scroll nearest scrollable parent
        await locator.evaluate(
            """(el, delta) => {
                let node = el;
                while (node) {
                    if (node.scrollHeight > node.clientHeight) {
                        node.scrollBy(0, delta);
                        return;
                    }
                    node = node.parentElement;
                }
                window.scrollBy(0, delta);
            }""",
            delta
        )


    def scroll(self, direction: str = "down", pixels: int = 120, event_name=None):
        for _ in range(20):
            run_async(self.page.mouse.wheel(0, (pixels * 3) if direction == "down" else -(pixels * 3)))
            run_async(self.page.wait_for_timeout(120))


    # =====================================================
    # GETTERS / TERMINATION
    # =====================================================

    def get_text_element(self, element: str) -> str:
        normalized = self._normalize_locator(element)
        return run_async(self.page.locator(normalized).inner_text())

    def force_terminate_app(self, app_name: str, event_name=None):
        raise NotImplementedError("force_terminate_app not supported")

    def terminate(self):
        run_async(self._terminate_async())

    async def _cleanup_resources(self):
        """Clean up partially initialized resources on error or cancellation."""
        try:
            if self.page:
                await self.page.close()
                self.page = None
            if self._context:
                await self._context.close()
                self._context = None
            if self._browser:
                await self._browser.close()
                self._browser = None
            if self._pw:
                await self._pw.stop()
                self._pw = None
        except Exception as cleanup_error:
            internal_logger.warning(f"[Playwright] Error during cleanup: {cleanup_error}")

    async def _terminate_async(self):
        internal_logger.info("[Playwright] Terminating session")

        try:
            # Close page first
            if self.page:
                try:
                    await self.page.close()
                except Exception as e:
                    internal_logger.debug(f"[Playwright] Error closing page: {e}")
                finally:
                    self.page = None

            # Close context (this will close all pages in the context)
            if self._context:
                try:
                    await self._context.close()
                except Exception as e:
                    internal_logger.debug(f"[Playwright] Error closing context: {e}")
                finally:
                    self._context = None

            # Close browser (this will close all contexts and windows)
            if self._browser:
                try:
                    await self._browser.close()
                except Exception as e:
                    internal_logger.debug(f"[Playwright] Error closing browser: {e}")
                finally:
                    self._browser = None

            # Stop Playwright
            if self._pw:
                try:
                    await self._pw.stop()
                except Exception as e:
                    internal_logger.debug(f"[Playwright] Error stopping Playwright: {e}")
                finally:
                    self._pw = None

            # Send all events if event_sdk is available
            if self.event_sdk:
                self.event_sdk.send_all_events()

            internal_logger.info("[Playwright] Session terminated successfully")
        except Exception as e:
            internal_logger.warning(f"[Playwright] Error during termination: {e}")
            # Ensure cleanup even if there's an error
            self.page = None
            self._context = None
            self._browser = None
            self._pw = None

    def get_driver_session_id(self):
        return None

    def execute_script(self, script: str, *args, event_name: Optional[str] = None) -> Any:
        """
        Execute JavaScript in the current browser context.

        :param script: The JavaScript code to execute.
        :type script: str
        :param *args: Optional arguments to pass to the script.
        :param event_name: The event triggering the script execution, if any.
        :type event_name: Optional[str]
        :return: The result of the script execution.
        :rtype: Any
        """
        return run_async(self._execute_script_async(script, *args, event_name=event_name))

    async def _execute_script_async(self, script: str, *args, event_name: Optional[str] = None) -> Any:
        """Async helper for execute_script."""
        if not self.page:
            raise OpticsError(Code.E0102, self.PAGE_NOT_INITIALIZED_MSG)

        if event_name and self.event_sdk:
            self.event_sdk.capture_event(event_name)

        try:
            # Playwright's evaluate takes a script and a single argument
            # If multiple args provided, pass as a list; if one arg, pass as-is; if none, pass None
            if len(args) == 0:
                result = await self.page.evaluate(script)
            elif len(args) == 1:
                result = await self.page.evaluate(script, args[0])
            else:
                # Multiple args - pass as a list
                result = await self.page.evaluate(script, list(args))

            internal_logger.debug(f"[Playwright] Executed script: {script[:100]}...")  # Log first 100 chars
            internal_logger.debug(f"[Playwright] Script execution result: {result}")

            return result
        except Exception as e:
            internal_logger.debug(f"[Playwright] Failed to execute script: {e}")
            raise OpticsError(Code.E0401, message=f"Failed to execute script: {e}", cause=e) from e
