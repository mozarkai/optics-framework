import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Optional, Any
from urllib.parse import urlsplit
from playwright.async_api import (
    async_playwright,
    Page,
    Error as PlaywrightError,
    TimeoutError as PlaywrightTimeoutError,
)
from optics_framework.common.driver_interface import DriverInterface
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.eventSDK import EventSDK
from optics_framework.common.logging_config import internal_logger
from optics_framework.common.async_utils import run_async
from optics_framework.common import utils
from optics_framework.common.utils import SpecialKey


@dataclass(frozen=True, repr=False)
class RemoteBrowser:
    """A browser to connect to instead of launching one.

    ``${NAME}`` references in ``url`` and header values are read from the environment
    only when connecting. The repr names the host alone: tracebacks are logged with
    their locals, and the url's path, query and headers can carry credentials.
    """

    url: str
    headers: dict = field(default_factory=dict)
    protocol: str = "cdp"
    context: str = "new"

    def __repr__(self) -> str:
        return f"RemoteBrowser(url={utils.redact_url(self.url)!r}, protocol={self.protocol!r})"

    def expanded_headers(self) -> dict[str, str] | None:
        return {name: utils.expand_env(str(value)) for name, value in self.headers.items()} or None

    def mask(self, message: str) -> str:
        """``message`` with every credential this browser is reached with replaced by
        ``***``: Playwright repeats the endpoint, path and query included, in connect
        errors, and some grids put the token in the path."""
        templates = [self.url, *map(str, self.headers.values())]
        names = {name for template in templates for name in utils.env_names(template)}
        url = urlsplit(utils.expand_env(self.url))
        if url.path.strip("/"):
            host = url.netloc.rpartition("@")[2]
            message = message.replace(f"{host}{url.path}", f"{host}/***")
        secrets = [os.environ.get(name) for name in names]
        secrets += [url.password, url.query, *(self.expanded_headers() or {}).values()]
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            message = message.replace(secret, "***")
        return message


class Playwright(DriverInterface):
    DEPENDENCY_TYPE = "driver_sources"
    NAME = "playwright"
    PAGE_NOT_INITIALIZED_MSG = "Playwright page not initialized"
    DEFAULT_ELEMENT_TIMEOUT_MS = 10000
    DEFAULT_NAVIGATION_TIMEOUT_MS = 60000
    DEFAULT_SETTLE_TIMEOUT_MS = 3000
    DEFAULT_ACTION_TIMEOUT_MS = 15000
    DEFAULT_CONNECT_TIMEOUT_MS = 90000
    REMOTE_ENV = "OPTICS_PLAYWRIGHT_REMOTE"
    _CONNECT_METHODS = {"cdp": "connect_over_cdp", "playwright": "connect"}
    SETTLE_QUIET_S = 0.5
    SETTLE_POLL_S = 0.1
    # None while the document is still parsing, so "loading" never counts as stable.
    _DOM_FINGERPRINT_SCRIPT = """() => {
        if (document.readyState === 'loading' || !document.body) return null;
        const body = document.body;
        return [location.href, body.getElementsByTagName('*').length, body.textContent.length].join('|');
    }"""

    _SPECIAL_KEYS = {
        SpecialKey.ENTER: "Enter",
        SpecialKey.TAB: "Tab",
        SpecialKey.BACKSPACE: "Backspace",
        SpecialKey.SPACE: "Space",
        SpecialKey.ESCAPE: "Escape",
        SpecialKey.LEFT: "ArrowLeft",
        SpecialKey.RIGHT: "ArrowRight",
        SpecialKey.UP: "ArrowUp",
        SpecialKey.DOWN: "ArrowDown",
        SpecialKey.INSERT: "Insert",
        SpecialKey.DELETE: "Delete",
        SpecialKey.HOME: "Home",
        SpecialKey.END: "End",
        SpecialKey.PAGE_UP: "PageUp",
        SpecialKey.PAGE_DOWN: "PageDown",
        **{SpecialKey(f"f{n}"): f"F{n}" for n in range(1, 13)},
    }

    _CLOSED_SESSION_MARKERS = ("has been closed", "target closed", "target crashed")

    @staticmethod
    def is_dead_session_error(exc: BaseException) -> bool:
        if not isinstance(exc, PlaywrightError):
            return False
        message = str(exc).lower()
        return any(marker in message for marker in Playwright._CLOSED_SESSION_MARKERS)

    def __init__(self, config: dict, event_sdk: Optional[EventSDK] = None):
        self.config = config or {}
        self.event_sdk = event_sdk

        self._pw = None
        self._browser = None
        self._context = None
        self.page: Optional[Page] = None

        internal_logger.info("[Playwright] Driver initialized")

    def _setting(self, key, default):
        """A driver setting from config.yaml's `capabilities`, where the docs and
        `optics configure` put it, or from the top level of the driver entry."""
        capabilities = self.config.get("capabilities") or {}
        if key in capabilities:
            return capabilities[key]
        return self.config.get(key, default)

    def element_timeout_s(self) -> float:
        return int(self._setting("element_timeout_ms", self.DEFAULT_ELEMENT_TIMEOUT_MS)) / 1000

    def _navigation_timeout_ms(self) -> int:
        return int(self._setting("navigation_timeout_ms", self.DEFAULT_NAVIGATION_TIMEOUT_MS))

    def _action_timeout_s(self) -> float:
        return int(self._setting("action_timeout_ms", self.DEFAULT_ACTION_TIMEOUT_MS)) / 1000

    def _connect_timeout_ms(self) -> int:
        return int(self._setting("connect_timeout_ms", self.DEFAULT_CONNECT_TIMEOUT_MS))

    def _call_budget_s(self, operation_timeout_s: float) -> float:
        return operation_timeout_s + self._action_timeout_s()

    def _launch_budget_s(self) -> float:
        return self._call_budget_s((self._connect_timeout_ms() + self._navigation_timeout_ms()) / 1000)

    def run(self, coro, timeout_s: float | None = None):
        """Run a Playwright coroutine on the shared loop, bounded by ``timeout_s``
        or, when omitted, by ``action_timeout_ms``."""
        return run_async(coro, timeout=self._action_timeout_s() if timeout_s is None else timeout_s)

    def _settle_timeout_s(self) -> float:
        return int(self._setting("settle_timeout_ms", self.DEFAULT_SETTLE_TIMEOUT_MS)) / 1000

    def wait_for_page_settled(self) -> None:
        """Wait, at most ``settle_timeout_ms``, until the page stops navigating and its
        DOM stops changing.

        ``goto`` returns at ``domcontentloaded``, before a single-page app renders or
        runs its client-side redirects, so a snapshot taken straight after it can see
        an empty shell or hit a page mid-navigation. Appium's page source already waits
        for UI idle and Selenium's ``get`` waits for load; this gives Playwright reads
        the same footing. Reaching the cap is not an error: a page that never goes
        quiet (a live stream, a ticking clock) is read as it is.
        """
        budget_s = self._settle_timeout_s()
        if budget_s <= 0 or self.page is None:
            return
        self.run(self._wait_for_page_settled_async(budget_s), timeout_s=self._call_budget_s(budget_s))

    async def _wait_for_page_settled_async(self, budget_s: float) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + budget_s
        last: str | None = None
        stable_since = loop.time()
        while loop.time() < deadline:
            try:
                current = await self.page.evaluate(self._DOM_FINGERPRINT_SCRIPT)
            except PlaywrightError:
                # The document is being replaced; there is nothing stable to read yet.
                current = None
            now = loop.time()
            if current is None or current != last:
                last, stable_since = current, now
            elif now - stable_since >= self.SETTLE_QUIET_S:
                return
            await asyncio.sleep(self.SETTLE_POLL_S)
        internal_logger.debug("[Playwright] Page still changing after %.1fs; reading it anyway", budget_s)

    # =====================================================
    # APP / SESSION
    # =====================================================

    def launch_app(self, app_identifier=None, app_activity=None, event_name=None):
        """Open the browser, or navigate the one already open."""
        return self.run(
            self._launch_app_async(app_identifier, event_name=event_name),
            timeout_s=self._launch_budget_s(),
        )

    async def _launch_app_async(self, app_identifier, event_name):
        started_browser = False
        try:
            if self.page is None or self.page.is_closed():
                await self._cleanup_resources()
                started_browser = True
                await self._start_browser()

            if app_identifier:
                internal_logger.debug("[Playwright] Navigating to %s", app_identifier)
                await self._navigate_to(app_identifier)

            if event_name and self.event_sdk:
                self.event_sdk.capture_event(event_name)

            internal_logger.info("[Playwright] Application launched")
            return "PLAYWRIGHT_SESSION"

        except (asyncio.CancelledError, OpticsError):
            if started_browser:
                await self._cleanup_resources()
            raise
        except Exception as e:
            if started_browser:
                await self._cleanup_resources()
            internal_logger.debug("[Playwright] Launch failed", exc_info=True)
            raise OpticsError(Code.E0102, str(e), cause=e)

    async def _start_browser(self):
        browser = self._setting("browser", "chromium")
        viewport = self._setting("viewport", {"width": 1280, "height": 800})
        remote = self._remote()
        self._pw = await async_playwright().start()
        browser_type = getattr(self._pw, browser)
        if remote:
            self._browser = await self._connect(browser_type, remote)
        else:
            internal_logger.info("[Playwright] Launching browser")
            self._browser = await browser_type.launch(headless=self._setting("headless", False))
        if remote and remote.context == "existing" and self._browser.contexts:
            self._context = self._browser.contexts[0]
            pages = self._context.pages
            self.page = pages[0] if pages else await self._context.new_page()
        else:
            self._context = await self._browser.new_context(viewport=viewport)
            self.page = await self._context.new_page()
        self._context.set_default_timeout(self.element_timeout_s() * 1000)

    def _remote(self) -> RemoteBrowser | None:
        """The remote browser to connect to, or None to launch one locally.

        ``OPTICS_PLAYWRIGHT_REMOTE``, a JSON object with ``url`` and optionally
        ``headers``, ``protocol`` and ``context``, wins over the driver config, so an operator can
        point every session at a browser grid.
        """
        from_env = os.environ.get(self.REMOTE_ENV)
        if from_env:
            try:
                remote = RemoteBrowser(**json.loads(from_env))
            except (json.JSONDecodeError, TypeError) as e:
                raise OpticsError(
                    Code.E0501, f'{self.REMOTE_ENV} must be a JSON object with "url" and optionally "headers", "protocol" and "context": {e}'
                ) from None
        elif self.config.get("url"):
            remote = RemoteBrowser(
                self.config["url"],
                self._setting("headers", None) or {},
                self._setting("protocol", "cdp"),
                self._setting("context", "new"),
            )
        else:
            return None
        if remote.protocol not in self._CONNECT_METHODS:
            raise OpticsError(Code.E0501, f"protocol must be one of {sorted(self._CONNECT_METHODS)}, not {remote.protocol!r}")
        if remote.protocol == "cdp" and self._setting("browser", "chromium") != "chromium":
            raise OpticsError(Code.E0501, 'protocol "cdp" reaches Chromium-based browsers only; set browser: chromium')
        if remote.context not in ("new", "existing"):
            raise OpticsError(Code.E0501, f'context must be "new" or "existing", not {remote.context!r}')
        return remote

    async def _connect(self, browser_type, remote: RemoteBrowser):
        internal_logger.info(
            "[Playwright] Connecting to remote browser at %s over %s", utils.redact_url(remote.url), remote.protocol
        )
        connect = getattr(browser_type, self._CONNECT_METHODS[remote.protocol])
        # Expanded values are never bound to a local: tracebacks can be logged with locals.
        try:
            return await connect(
                utils.expand_env(remote.url), headers=remote.expanded_headers(), timeout=self._connect_timeout_ms()
            )
        except PlaywrightError as e:
            message = remote.mask(str(e))
        raise OpticsError(Code.E0102, message)

    async def _launch_other_app_async(self, app_name: str, event_name=None):
        """
        Launch another app in a new tab (not a new window).
        Creates a new page in the existing browser context.
        """
        try:
            if self._context is None:
                # If no context exists, initialize browser first
                internal_logger.info("[Playwright] No existing context, initializing browser")
                await self._launch_app_async(None, event_name=event_name)

            # Create a new page (tab) in the existing context
            internal_logger.debug("[Playwright] Creating new tab for %s", app_name)
            self.page = await self._context.new_page()

            # Navigate to the new URL
            if app_name:
                internal_logger.debug("[Playwright] Navigating to %s", app_name)
                await self._navigate_to(app_name)

            if event_name and self.event_sdk:
                self.event_sdk.capture_event(event_name)

            internal_logger.info("[Playwright] Launched other app in new tab")

        except OpticsError:
            raise
        except Exception as e:
            internal_logger.debug("[Playwright] Failed to launch other app: %s", e, exc_info=True)
            raise OpticsError(Code.E0102, str(e), cause=e)

    def launch_other_app(self, app_name: str, event_name=None):
        return self.run(
            self._launch_other_app_async(app_name, event_name=event_name),
            timeout_s=self._launch_budget_s(),
        )

    async def _navigate_to(self, url: str):
        """
        Navigate to a URL with configurable timeout and wait condition.
        If navigation times out but the URL is reached, continue with a warning.
        """
        if not self.page:
            raise OpticsError(Code.E0102, self.PAGE_NOT_INITIALIZED_MSG)

        timeout_ms = self._navigation_timeout_ms()
        wait_until = self._setting("navigation_wait_until", "domcontentloaded")

        try:
            await self.page.goto(url, timeout=timeout_ms, wait_until=wait_until)
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

        return element

    # =====================================================
    # PRESS / CLICK
    # =====================================================

    def press_element(self, element: str, repeat: int = 1, event_name=None):
        timeout_s = self.element_timeout_s()
        self.run(
            self._press_element_async(element, repeat, timeout_s, event_name=event_name),
            timeout_s=self._call_budget_s(timeout_s * (int(repeat) + 1)),
        )

    async def _press_element_async(self, element, repeat, timeout_s, event_name):
        locator = self._as_locator(element)
        timeout_ms = timeout_s * 1000
        await locator.wait_for(state="visible", timeout=timeout_ms)

        for _ in range(int(repeat)):
            await locator.click(timeout=timeout_ms)

        if event_name and self.event_sdk:
            self.event_sdk.capture_event(event_name)

    def press_coordinates(self, x: int, y: int, event_name=None):
        self.run(self.page.mouse.click(x, y))

    def press_percentage_coordinates(self, px, py, repeat=1, event_name=None):
        self.run(self._press_percentage_async(px, py, repeat))

    async def _viewport(self) -> dict:
        """The page's viewport; a browser's own context, adopted over CDP, emulates none."""
        return self.page.viewport_size or await self.page.evaluate(
            "() => ({width: window.innerWidth, height: window.innerHeight})"
        )

    async def _press_percentage_async(self, px, py, repeat):
        vp = await self._viewport()
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
        self.run(self.page.keyboard.press(key))

        if event_name and self.event_sdk:
            self.event_sdk.capture_event(event_name)

    # =====================================================
    # TEXT INPUT
    # =====================================================

    def enter_text(self, text, event_name=None):
        self.run(self._type(self.page.keyboard, text))

    def enter_text_using_keyboard(self, text, event_name=None):
        self.run(self._type(self.page.keyboard, text))

    def enter_text_element(self, element: str, text, event_name=None):
        locator = self._as_locator(element)
        if isinstance(text, SpecialKey):
            action = locator.press(self._key_name(text))
        else:
            action = locator.fill(utils.strip_sensitive_prefix(str(text)))
        self.run(action, timeout_s=self._call_budget_s(self.element_timeout_s()))

    def _type(self, keyboard, text):
        if isinstance(text, SpecialKey):
            return keyboard.press(self._key_name(text))
        return keyboard.type(utils.strip_sensitive_prefix(str(text)))

    def _key_name(self, key: SpecialKey) -> str:
        if key not in self._SPECIAL_KEYS:
            self._unsupported(f"<{key.value}>")
        return self._SPECIAL_KEYS[key]

    def clear_text(self, event_name=None):
        self.run(self.page.keyboard.press("Control+A"))
        self.run(self.page.keyboard.press("Backspace"))

    def clear_text_element(self, element: str, event_name=None):
        self.run(self._as_locator(element).fill(""), timeout_s=self._call_budget_s(self.element_timeout_s()))

    def _as_locator(self, element):
        """A locator for a selector string. Element sources already return a
        Locator, which must not be wrapped in page.locator() again."""
        normalized = self._normalize_locator(element)
        return self.page.locator(normalized) if isinstance(normalized, str) else normalized

    # =====================================================
    # SCROLL / SWIPE
    # =====================================================

    def swipe(self, x, y, direction, swipe_length, event_name=None):
        delta = swipe_length if direction == "down" else -swipe_length
        self.run(self.page.mouse.wheel(0, delta))

    def swipe_percentage(self, x_per: int, y_per: int, direction: str, swipe_length_percentage: int, event_name=None):
        self.run(self._swipe_percentage_async(direction, swipe_length_percentage))

    async def _swipe_percentage_async(self, direction: str, swipe_length_percentage: int):
        vp = await self._viewport()
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
            self.run(
                self._swipe_element_async(
                    element=element,
                    direction=direction,
                    swipe_length=swipe_length
                ),
                timeout_s=self._call_budget_s(self.element_timeout_s()),
            )
        except Exception as e:
            raise RuntimeError(
                f"swipe_element failed for Playwright. "
                f"Use scroll_from_element instead. Root cause: {e}"
            ) from e

    async def _swipe_element_async(self, element: str, direction: str, swipe_length: int):
        locator = self._as_locator(element)
        await locator.wait_for(state="visible", timeout=self.element_timeout_s() * 1000)

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
        for _ in range(2):
            self.run(self.page.mouse.wheel(0, pixels if direction == "down" else -pixels))
            self.run(self.page.wait_for_timeout(120))


    # =====================================================
    # GETTERS / TERMINATION
    # =====================================================

    def go_back(self, event_name=None):
        self.run(
            self._go_back_async(),
            timeout_s=self._call_budget_s(self._navigation_timeout_ms() / 1000),
        )
        if event_name and self.event_sdk:
            self.event_sdk.capture_event(event_name)

    async def _go_back_async(self):
        if not self.page:
            raise OpticsError(Code.E0102, self.PAGE_NOT_INITIALIZED_MSG)
        await self.page.go_back(
            timeout=self._navigation_timeout_ms(),
            wait_until=self._setting("navigation_wait_until", "domcontentloaded"),
        )

    def get_text_element(self, element: str) -> str:
        return self.run(
            self._as_locator(element).inner_text(),
            timeout_s=self._call_budget_s(self.element_timeout_s()),
        )

    def force_terminate_app(self, app_name: str, event_name=None):
        raise NotImplementedError("force_terminate_app not supported")

    def terminate(self):
        self.run(self._terminate_async())

    async def _cleanup_resources(self):
        """Close the browser and stop Playwright, whatever state a launch left them in.

        Closing a launched browser closes its pages. Closing a connected one closes the
        contexts Optics created and the connection, which is what ends the session on the
        remote end; an adopted context and its tabs stay open.
        """
        browser, pw = self._browser, self._pw
        self.page = self._context = self._browser = self._pw = None
        try:
            if browser is not None:
                await browser.close()
        except Exception as e:
            internal_logger.warning("[Playwright] Error closing the browser: %s", e)
        finally:
            # Also on cancellation: stopping Playwright ends its driver process, and with
            # it any connection a stuck close left open.
            try:
                if pw is not None:
                    await pw.stop()
            except Exception as e:
                internal_logger.warning("[Playwright] Error stopping Playwright: %s", e)

    async def _terminate_async(self):
        internal_logger.info("[Playwright] Terminating session")
        await self._cleanup_resources()
        if self.event_sdk:
            self.event_sdk.send_all_events()
        internal_logger.info("[Playwright] Session terminated successfully")

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
        return self.run(self._execute_script_async(script, *args, event_name=event_name))

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
