import time
from typing import Optional, Any, Tuple, List, Dict
from lxml import etree  # type: ignore

from optics_framework.common.logging_config import internal_logger
from optics_framework.common.elementsource_interface import ElementSourceInterface
from optics_framework.common.error import OpticsError, Code
from optics_framework.common import utils
from optics_framework.common.async_utils import run_async


PLAYWRIGHT_NOT_INITIALISED_MSG = (
    "Playwright driver is not initialized for PlaywrightPageSource."
)


class PlaywrightPageSource(ElementSourceInterface):
    """
    Playwright Page Source Element Source
    """
    REQUIRED_DRIVER_TYPE = "playwright"

    def __init__(self, driver: Optional[Any] = None):
        # 🔑 DO NOT validate here
        self.driver = driver
        self.page = None
        self.tree = None
        self.root = None

    # ---------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------

    def _require_page(self):
        """
        Ensure that a valid Playwright page instance is available.

        This method acts as a strict precondition guard for all Playwright
        operations that require an active page. It validates the driver
        state step-by-step and fails fast with clear, actionable errors.

        Validation sequence:
        1. Confirm that a driver object was injected.
        2. Confirm that the driver exposes a `page` attribute.
        3. Confirm that the page has been initialized (launch_app completed).

        Design notes:
        - This method intentionally raises errors instead of returning None
          to avoid silent failures and undefined behavior downstream.
        - Centralizing these checks prevents duplicated validation logic
          across element lookup, assertions, and page interactions.
        - The resolved page reference is cached on `self.page` for reuse.

        Returns:
            Any:
                A valid Playwright page instance.

        Raises:
            OpticsError:
                If the driver or page is missing or not initialized.
        """

        # ------------------------------------------------------------------
        # Diagnostic logging to help trace driver / page state during failures
        # ------------------------------------------------------------------
        internal_logger.debug(
            "[PlaywrightPageSource] driver=%s | has_page_attr=%s | page=%s",
            self.driver,
            hasattr(self.driver, "page") if self.driver else False,
            getattr(self.driver, "page", None) if self.driver else None
        )

        # ---------------------------------------------------------------
        # Guard 1: Driver must be injected into the page source
        # ---------------------------------------------------------------
        # Without a driver, no Playwright session exists and no operations
        # can be performed safely.
        if self.driver is None:
            raise OpticsError(
                Code.E0101,
                message=(
                    "Playwright driver is not injected into PlaywrightPageSource. "
                    "Session may not be initialized."
                )
            )

        # ---------------------------------------------------------------
        # Guard 2: Driver must expose a `page` attribute
        # ---------------------------------------------------------------
        # This protects against invalid driver implementations or incorrect
        # framework wiring.
        if not hasattr(self.driver, "page"):
            raise OpticsError(
                Code.E0101,
                message=(
                    "Playwright driver does not expose 'page'. "
                    "Invalid driver implementation or setup."
                )
            )

        # ---------------------------------------------------------------
        # Guard 3: Page must be initialized
        # ---------------------------------------------------------------
        # The driver exists, but launch_app() has not yet created a page.
        # Accessing elements before this point would be unsafe.
        if self.driver.page is None:
            raise OpticsError(
                Code.E0101,
                message=(
                    "Playwright page is not initialized yet. "
                    "Ensure launch_app() completed before using element sources."
                )
            )

        # ---------------------------------------------------------------
        # Cache and return the validated page instance
        # ---------------------------------------------------------------
        self.page = self.driver.page
        return self.page

    # ---------------------------------------------------------
    # Required interface methods
    # ---------------------------------------------------------

    def capture(self):
        """
        Screen capture is intentionally NOT supported by PlaywrightPageSource.

        Design rationale:
        - PlaywrightPageSource is responsible only for DOM-based page source
          inspection and element analysis.
        - Visual capture responsibilities (screenshots, image comparison,
          OCR, etc.) are handled by dedicated ElementSource implementations.
        - Keeping this class DOM-focused avoids mixing visual and structural
          concerns and keeps responsibilities clearly separated.

        Behavior:
        - Always raises NotImplementedError to fail fast if capture is
          mistakenly invoked.
        - Logs the failure at exception level to surface incorrect usage
          clearly in execution logs.

        Raises:
            NotImplementedError:
                Always raised to indicate unsupported operation.
        """

        # Explicitly log misuse to aid debugging and framework integration checks
        internal_logger.exception(
            "PlaywrightPageSource does not support screen capture."
        )

        # Fail fast to prevent silent or partial behavior
        raise NotImplementedError(
            "PlaywrightPageSource does not support screen capture."
        )

    def get_page_source(self) -> str:
        """
        Retrieve and parse the current page's DOM source using Playwright.

        Design intent:
        - Serve as the single authoritative entry point for fetching the
          live DOM from the Playwright page.
        - Convert raw HTML into an lxml tree for downstream XPath-based
          analysis and element extraction.
        - Keep DOM acquisition logic isolated from element filtering,
          bounding-box calculations, and assertions.

        Execution flow:
        1. Validate that a Playwright page is available via `_require_page`.
        2. Capture a timestamp for traceability and debugging.
        3. Fetch the full HTML content asynchronously from Playwright.
        4. Parse the HTML into an lxml tree and cache it on the instance.
        5. Emit detailed debug logs for observability and diagnostics.

        Important behavior notes:
        - This method always fetches a *fresh* DOM snapshot from Playwright.
        - Cached `self.tree` and `self.root` are overwritten on each call.
        - No filtering or mutation of the DOM is performed here.
        - Any timing or retry behavior must be handled by callers.

        Returns:
            str:
                The raw HTML content of the current page.
        """

        # Diagnostic log to trace invocation timing in complex flows
        internal_logger.error("trying get_page_source ..............")

        # Ensure Playwright page is initialized and available
        page = self._require_page()

        # Secondary diagnostic log to confirm successful page resolution
        internal_logger.error("trying get_page_source _require_page ..............")

        # Capture timestamp before DOM retrieval for correlation in logs
        timestamp = utils.get_timestamp()

        # Fetch the full HTML content asynchronously from Playwright
        html: str = run_async(page.content())

        # Log size of the retrieved DOM for debugging large or empty pages
        internal_logger.debug(
            "[PlaywrightPageSource] Page source fetched, length=%d",
            len(html)
        )

        # Parse HTML into an lxml tree for XPath-based processing
        self.tree = etree.HTML(html)
        self.root = self.tree

        # High-visibility debug markers to aid log scanning
        internal_logger.debug(
            "========== PLAYWRIGHT PAGE SOURCE FETCHED =========="
        )
        internal_logger.debug(
            "========== XML tree ========== %s ", html
        )
        internal_logger.debug("Timestamp: %s", timestamp)

        # Return raw HTML to callers that need the original source
        return html

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> List[Dict]:
        """
        Extract visible and interactive elements from the current web page.

        Design intent:
        - Provide a unified, cross-platform mechanism to introspect the current
          DOM and return meaningful UI elements for automation, inspection,
          or visual tooling.
        - Act as a high-level orchestration method that coordinates DOM parsing,
          bounding-box detection, filtering rules, and metadata enrichment.
        - Keep element discovery logic centralized to avoid duplication across
          engines, assertions, and test utilities.

        How this method works:
        1. Fetch and parse the latest DOM snapshot from Playwright.
        2. Traverse all DOM nodes using XPath.
        3. Attempt to calculate screen bounds for each node (visibility filter).
        4. Apply semantic filters (buttons, inputs, images, text, etc.).
        5. Extract display text using multiple fallbacks.
        6. Generate a stable XPath for each included element.
        7. Attach additional metadata for downstream consumers.

        Filtering behavior:
        - If `filter_config` is None or empty → all eligible elements are returned.
        - If multiple filters are provided → an element is included if it matches
          *any* of the specified filters.
        - Filters are semantic, not structural (e.g., "button" vs tag-only).

        Important behavior notes:
        - Elements without calculable bounds are skipped (not visible / not rendered).
        - DOM traversal order is preserved in the output list.
        - No retries or waits are performed here; timing is handled upstream.
        - This method is intentionally read-only and has no side effects
          beyond updating cached DOM state.

        Args:
            filter_config (Optional[List[str]]):
                Optional list of filter types. Supported values:
                    - "all": Show all elements (default when None or empty)
                    - "interactive": Only interactive elements
                    - "buttons": Only button elements
                    - "inputs": Only input/text field elements
                    - "images": Only image elements
                    - "text": Only text elements
                Filters may be combined, e.g. ["buttons", "inputs"].

        Returns:
            List[Dict]:
                A list of dictionaries with the following keys:
                    - text   : Display text or fallback identifier
                    - bounds : Screen coordinates (x1, y1, x2, y2)
                    - xpath  : Generated XPath selector
                    - extra  : Additional element metadata
        """
        # Ensure page source is fetched and parsed
        self.get_page_source()

        if self.tree is None:
            internal_logger.error("[PlaywrightPageSource] Tree is None, cannot extract elements")
            return []

        page = self._require_page()
        elements = self.tree.xpath(".//*")
        results = []

        for node in elements:
            bounds = self._extract_bounds(node, page)
            if not bounds:
                continue

            # Check if element should be included based on filter_config
            if not self._should_include_element(node, filter_config):
                continue

            text, used_key = self._extract_display_text(node, page)
            if not text:
                # If no text-like attribute, use tag name
                text, used_key = node.tag, None

            xpath = self.get_xpath(node)
            extra = self._build_extra_metadata(node.attrib, used_key, node.tag)

            results.append(
                {"text": text, "bounds": bounds, "xpath": xpath, "extra": extra}
            )

        return results

    # ---------------------------------------------------------
    # Helper methods for get_interactive_elements
    # ---------------------------------------------------------

    def _extract_bounds(self, node: etree.Element, page: Any) -> Optional[Dict[str, int]]:
        """
        Calculate the on-screen bounding box for a DOM element using Playwright.

        Design intent:
        - Bridge the gap between DOM-level elements (lxml) and rendered UI elements
          in the browser viewport.
        - Provide a visibility-aware filter: only elements that can be resolved
          to actual screen coordinates are considered usable.
        - Keep rendering concerns isolated from DOM traversal and filtering logic.

        How this method works:
        1. Generate a simple, Playwright-compatible XPath for the given DOM node.
        2. Resolve the XPath to a Playwright locator.
        3. Perform a lightweight existence check to avoid expensive calls.
        4. Request the element’s bounding box from the browser.
        5. Normalize the bounding box into integer screen coordinates.

        Important behavior notes:
        - Elements that cannot be located, rendered, or measured are silently
          excluded by returning `None`.
        - No retries or waits are performed here; this method is intentionally
          fast and side-effect free.
        - This method assumes the page has already been loaded and stabilized.
        - Bounding boxes are returned in absolute viewport coordinates.

        Args:
            node (etree.Element):
                Parsed lxml DOM node representing the HTML element.
            page (Any):
                Active Playwright page instance used for rendering queries.

        Returns:
            Optional[Dict[str, int]]:
                Dictionary with keys:
                    - x1, y1: top-left corner
                    - x2, y2: bottom-right corner
                Returns None if bounds cannot be determined.
        """
        try:
            # Build a selector from the element
            xpath = self._build_simple_xpath(node)
            if not xpath:
                return None

            # Try to locate the element using XPath
            locator = page.locator(f"xpath={xpath}")
            found = self._locator_exists(locator)

            if not found:
                return None

            # Get bounding box from the first matching element
            bbox = run_async(locator.first.bounding_box())

            if bbox is None:
                return None

            x1 = int(bbox["x"])
            y1 = int(bbox["y"])
            x2 = int(bbox["x"] + bbox["width"])
            y2 = int(bbox["y"] + bbox["height"])

            return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

        except Exception as e:
            internal_logger.debug(
                f"[PlaywrightPageSource] Could not extract bounds for element: {e}"
            )
            return None

    @staticmethod
    def _build_simple_xpath(node: etree.Element) -> Optional[str]:
        """
        Build a minimal, Playwright-compatible XPath for a given DOM node.

        Design intent:
        - Prefer stable attributes (id, data-testid, name).
        - Fall back to structural XPath only when needed.
        - Keep output simple and deterministic.
        """
        """
           Build a minimal, Playwright-compatible XPath for a given DOM node.

            Design intent:
            - Provide a *best-effort* XPath that is simple, readable, and fast to resolve.
            - Prefer stable, unique attributes over deep DOM traversal.
            - Act strictly as a fallback mechanism for operations like bounding-box
                calculation where a locator is required but precision is not critical.

            Why this method is intentionally "simple":
            - It is NOT meant to generate a perfectly unique or future-proof XPath.
            - It avoids expensive document-wide uniqueness checks.
            - It prioritizes speed and resilience over absolute accuracy.
            - More advanced XPath generation is handled elsewhere (`get_xpath`).

            Resolution strategy (in order):
             1. Use `id` attribute if present (most reliable and unique).
             2. Use `data-testid` if available (common in test-friendly UIs).
             3. Use `name` attribute when applicable.
             4. Fall back to a hierarchical tag-based XPath with positional indexes.

             Important behavior notes:
                - Returned XPath may match multiple elements; callers must handle this.
                - The XPath is always absolute (`//` or `/`) for Playwright compatibility.
                - If the node cannot be resolved meaningfully, `None` is returned.
                - This method performs NO validation against the live DOM.

             Args:
                    node (etree.Element):
                        lxml DOM element for which an XPath is required.

            Returns:
                    Optional[str]:
                        A simple XPath string or None if it cannot be constructed.
        """
        if node is None or not hasattr(node, "tag"):
            return None

        # 1️⃣ Attribute-based XPath (fast & stable)
        attr_xpath = PlaywrightPageSource._build_xpath_from_attributes(node)
        if attr_xpath:
            return attr_xpath

        # 2️⃣ Structural fallback
        return PlaywrightPageSource._build_structural_xpath(node)

    @staticmethod
    def _build_xpath_from_attributes(node: etree.Element) -> Optional[str]:
        tag = node.tag or "*"
        attrs = node.attrib or {}

        for attr in ("id", "data-testid", "name"):
            value = attrs.get(attr)
            if value:
                escaped = PlaywrightPageSource._escape_xpath_value(value)
                return f"//{tag}[@{attr}={escaped}]"

        return None

    @staticmethod
    def _build_structural_xpath(node: etree.Element) -> Optional[str]:
        path = []
        current = node

        while current is not None and hasattr(current, "tag"):
            parent = current.getparent()
            tag = current.tag or "*"

            if parent is None:
                path.insert(0, tag)
                break

            siblings = [sib for sib in parent if sib.tag == tag]
            if len(siblings) > 1:
                index = siblings.index(current) + 1
                path.insert(0, f"{tag}[{index}]")
            else:
                path.insert(0, tag)

            current = parent

        return "/" + "/".join(path) if path else None

    @staticmethod
    def _escape_xpath_value(val: str) -> str:
        if "'" not in val:
            return f"'{val}'"

        parts = val.split("'")
        return "concat('" + "', \"'\", '".join(parts) + "')"

    def _extract_display_text(
            self,
            node: etree.Element,
            page: Any,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract a human-readable display label for a DOM element.

        Resolution order is preserved exactly as before.
        """
        if node is None:
            return None, None

        # 1️⃣ Fast DOM-based text
        text = self._extract_dom_text(node)
        if text:
            return text

        # 2️⃣ Attribute-based fallbacks
        text = self._extract_attribute_text(node)
        if text:
            return text

        # 3️⃣ Playwright innerText (slow fallback)
        text = self._extract_playwright_text(node, page)
        if text:
            return text

        # 4️⃣ Identifier-based fallback
        return self._extract_identifier_text(node)

    @staticmethod
    def _extract_dom_text(
            node: etree.Element,
    ) -> Optional[Tuple[str, str]]:
        if node.text and node.text.strip():
            return node.text.strip(), "text"

        if node.tail and node.tail.strip():
            return node.tail.strip(), "tail"

        return None

    @staticmethod
    def _extract_attribute_text(
            node: etree.Element,
    ) -> Optional[Tuple[str, str]]:
        attrs = node.attrib or {}

        for key in ("aria-label", "title", "alt", "placeholder"):
            value = attrs.get(key, "").strip()
            if value:
                return value, key

        return None

    def _extract_playwright_text(
            self,
            node: etree.Element,
            page: Any,
    ) -> Optional[Tuple[str, str]]:
        try:
            xpath = self._build_simple_xpath(node)
            if not xpath:
                return None

            locator = page.locator(f"xpath={xpath}")
            if run_async(locator.count()) == 0:
                return None

            text = run_async(locator.first.inner_text())
            if text and text.strip():
                return text.strip(), "innerText"

        except Exception as e:
            internal_logger.debug(
                "[PlaywrightPageSource] Failed innerText extraction: %s",
                e,
            )

        return None

    @staticmethod
    def _extract_identifier_text(
            node: etree.Element,
    ) -> Tuple[Optional[str], Optional[str]]:
        attrs = node.attrib or {}

        element_id = attrs.get("id", "").strip()
        if element_id:
            return element_id, "id"

        class_name = attrs.get("class", "").strip()
        if class_name:
            first_class = class_name.split()[0]
            return first_class, "class"

        return None, None

    def _should_include_element(self, node: etree.Element, filter_config: Optional[List[str]]) -> bool:
        """
        Decide whether a DOM element should be included in the result set
        based on the provided filter configuration.
        Design intent:
        - Provide a flexible, declarative filtering mechanism for element
          extraction without hard-coding behavior at call sites.
        - Allow callers to request *categories* of elements (buttons, inputs,
          images, etc.) instead of dealing with raw DOM rules.
        - Keep this method purely deterministic and side-effect free.
        Filtering semantics:
        - If no filters are provided, ALL elements are included.
        - If the special value `"all"` is present, ALL elements are included.
        - Otherwise, the element is included if it matches *any* of the
          requested filter categories.
        Important behavior notes:
        - Filters are evaluated independently and OR’ed together.
        - This method does NOT short-circuit on first match to keep
          logic readable and extensible.
        - The actual classification logic is delegated to helper methods
          (`_is_button`, `_is_input`, etc.) to avoid duplication and
          keep responsibilities isolated.
        Args:
            node (etree.Element):
                The lxml DOM element being evaluated.
            filter_config (Optional[List[str]]):
                List of filter categories to apply.
        Returns:
            bool:
                True  → element should be included
                False → element should be excluded
        """

        # ---------------------------------------------------------
        # Default behavior: no filters means include everything
        # ---------------------------------------------------------
        # This keeps backward compatibility and avoids forcing
        # callers to explicitly specify ["all"].
        # Default behavior: show all elements when filter_config is None or empty
        if not filter_config or len(filter_config) == 0:
            return True

        # If "all" is in filter_config, show all elements
        if "all" in filter_config:
            return True

        # Check each filter type
        matches_any = False

        if "interactive" in filter_config and self._is_probably_interactive(node):
            matches_any = True

        if "buttons" in filter_config and self._is_button(node):
            matches_any = True

        if "inputs" in filter_config and self._is_input(node):
            matches_any = True

        if "images" in filter_config and self._is_image(node):
            matches_any = True

        if "text" in filter_config and self._is_text(node):
            matches_any = True

        return matches_any

    def _is_button(self, node: etree.Element) -> bool:
        """Check if element is a button."""
        tag = node.tag or ""
        attrs = node.attrib or {}

        # HTML button tag
        if tag.lower() == "button":
            return True

        # Elements with role="button"
        if attrs.get("role", "").lower() == "button":
            return True

        # Links that act as buttons (common pattern)
        if tag.lower() == "a" and (
            attrs.get("role", "").lower() == "button" or
            "button" in attrs.get("class", "").lower()
        ):
            return True

        return False

    def _is_input(self, node: etree.Element) -> bool:
        """Check if element is an input/text field."""
        tag = node.tag or ""

        # HTML input elements
        input_tags = ["input", "textarea", "select"]
        return tag.lower() in input_tags

    def _is_image(self, node: etree.Element) -> bool:
        """Check if element is an image."""
        tag = node.tag or ""
        attrs = node.attrib or {}

        # HTML img tag
        if tag.lower() == "img":
            return True

        # Elements with role="img"
        if attrs.get("role", "").lower() == "img":
            return True

        return False

    def _is_text(self, node: etree.Element) -> bool:
        """
        Determine whether an element should be treated as a *textual* element.

        Design intent:
        - Identify elements whose primary purpose is to present readable text
          to the user (labels, headings, paragraphs, links, etc.).
        - Exclude form inputs and interactive controls that manage user input
          rather than display content.
        - Keep this classification lightweight and heuristic-based, not DOM-perfect.

        Important behavior notes:
        - This method intentionally errs on the side of inclusion.
        - Actual text presence may be validated later using innerText or
          Playwright APIs when needed.
        - This method does NOT perform DOM queries or Playwright calls.

        Args:
            node (etree.Element):
                The lxml DOM element being evaluated.

        Returns:
            bool:
                True  → element is considered text-bearing
                False → element is not considered a text element
        """

        tag = node.tag or ""
        attrs = node.attrib or {}

        # Exclude inputs
        if self._is_input(node):
            return False

        # Text-containing tags
        text_tags = ["p", "span", "div", "h1", "h2", "h3", "h4", "h5", "h6",
                     "label", "li", "td", "th", "a", "strong", "em", "b", "i"]

        if tag.lower() in text_tags:
            # Check if it has text content or aria-label
            text_content = attrs.get("aria-label", "").strip()
            if text_content:
                return True
            # If it's a text tag, assume it might have text (will be checked via innerText)
            return True

        return False

    def _is_probably_interactive(self, node: etree.Element) -> bool:
        """
        Heuristically determine whether an element is *likely interactive*.

        Design intent:
        - Identify elements that users can interact with (click, focus, activate).
        - Rely on lightweight DOM inspection instead of Playwright runtime checks
          to keep this method fast and side-effect free.
        - Favor inclusivity: false positives are acceptable, false negatives are not.

        What this method DOES:
        - Uses semantic HTML tags, ARIA roles, and common attributes.
        - Detects both native and custom interactive elements.
        - Works across frameworks (plain HTML, React, Angular, etc.).

        What this method DOES NOT do:
        - It does not verify visibility, enabled state, or actual clickability.
        - It does not perform DOM queries or Playwright calls.
        - It does not wait, retry, or assert anything.

        Args:
            node (etree.Element):
                The lxml DOM element to evaluate.

        Returns:
            bool:
                True  → element is probably interactive
                False → element is probably non-interactive
        """

        tag = node.tag or ""
        attrs = node.attrib or {}

        # Buttons are interactive
        if self._is_button(node):
            return True

        # Links are interactive
        if tag.lower() == "a" and attrs.get("href"):
            return True

        # Inputs are interactive
        if self._is_input(node):
            return True

        # Elements with onclick handlers
        if "onclick" in attrs or attrs.get("onclick"):
            return True

        # Elements with role="button" or role="link"
        role = attrs.get("role", "").lower()
        if role in ["button", "link", "menuitem", "tab"]:
            return True

        # Elements with tabindex (usually interactive)
        if "tabindex" in attrs:
            try:
                tabindex = int(attrs.get("tabindex", "0"))
                if tabindex >= 0:  # Non-negative tabindex means focusable
                    return True
            except ValueError as e:
                internal_logger.debug(f"Invalid tabindex value: {e}")

        return False

    def get_xpath(self, node: etree.Element) -> str:
        """
        Generate an optimal XPath expression for a given HTML element.

        Design intent:
        - Produce a stable, reusable XPath suitable for Playwright interaction,
          debugging, logging, and downstream automation.
        - Prefer *semantic and unique* attributes over positional paths.
        - Fall back gracefully when uniqueness cannot be guaranteed.
        """
        if node is None or not hasattr(node, "tag"):
            return ""

        # 1️⃣ Attribute-based strategies (strong → weak)
        xpath = self._try_attribute_xpaths(
            node,
            primary_attrs=("id", "data-testid", "name"),
            secondary_attrs=("class", "aria-label", "title"),
        )
        if xpath:
            return xpath

        # 2️⃣ Structural fallback
        return self._build_hierarchical_xpath(node)

    def _try_attribute_xpaths(
            self,
            node: etree.Element,
            primary_attrs: tuple,
            secondary_attrs: tuple,
    ) -> Optional[str]:
        for attr in primary_attrs:
            xpath = self._build_and_validate_attr_xpath(node, attr)
            if xpath:
                return xpath

        for attr in secondary_attrs:
            xpath = self._build_and_validate_attr_xpath(node, attr)
            if xpath:
                return xpath

        return None

    def _build_and_validate_attr_xpath(
            self,
            node: etree.Element,
            attr_name: str,
    ) -> Optional[str]:
        tag = node.tag or "*"
        value = node.attrib.get(attr_name)

        if not value:
            return None

        xpath = f"//{tag}[@{attr_name}={self._escape_for_xpath_literal(value)}]"
        return self._ensure_xpath_uniqueness(node, xpath)

    def _ensure_xpath_uniqueness(
            self,
            node: etree.Element,
            xpath: str,
    ) -> Optional[str]:
        doc_tree = node.getroottree()

        try:
            matches = doc_tree.xpath(xpath)
        except (etree.XPathError, ValueError, TypeError):
            return None

        if not matches:
            return None

        if len(matches) == 1:
            return xpath

        try:
            index = matches.index(node)
        except ValueError:
            index = 0

        return f"({xpath})[{index + 1}]"

    def _build_hierarchical_xpath(self, node: etree.Element) -> str:
        """
        Build hierarchical XPath based on element structure.
        """
        tag = node.tag
        if not tag:
            return ""

        parent = node.getparent()
        segment = f"/{tag}"

        if parent is not None:
            siblings_same_tag = [c for c in parent if c.tag == tag]
            if len(siblings_same_tag) > 1:
                idx = siblings_same_tag.index(node) + 1
                segment += f"[{idx}]"

        if parent is not None and hasattr(parent, "tag"):
            parent_xpath = self._build_hierarchical_xpath(parent)
            return f"{parent_xpath}{segment}"

        return segment

    def _escape_for_xpath_literal(self, s: str) -> str:
        """
        Construct a hierarchical XPath based purely on DOM structure.

        Design intent:
        - Provide a deterministic fallback when attribute-based XPath generation
          is not possible or not reliable.
        - Ensure every element can still be addressed, even in the absence of
          unique attributes (id, name, data-testid, etc.).
        - Preserve DOM order by using positional indices where required.

        Characteristics:
        - This method relies ONLY on parent-child relationships.
        - XPath segments are built from the target node up to the root.
        - Positional indices ([n]) are added only when siblings share the same tag.
        - The resulting XPath is stable for a given DOM structure but may change
          if the DOM hierarchy itself changes.

        Important notes:
        - This method does not validate uniqueness globally.
        - It does not interact with Playwright or perform runtime queries.
        - It is intentionally simple and predictable to avoid side effects.

        Args:
            node (etree.Element):
                The lxml DOM element for which the hierarchical XPath is generated.

        Returns:
            str:
                A hierarchical XPath string, or an empty string if the node is invalid.
        """

        if '"' not in s:
            return f'"{s}"'
        if "'" not in s:
            return f"'{s}'"
        # If it contains both, break on double quotes and concat with '\"'
        parts = s.split('"')
        escaped_parts = []
        for i, p in enumerate(parts):
            if i == len(parts) - 1:
                escaped_parts.append(f'"{p}"')
            else:
                escaped_parts.extend([f'"{p}"', "'\"'"])
        return 'concat(' + ', '.join(escaped_parts) + ')'

    def _build_extra_metadata(self, attrs: dict, used_key: Optional[str], tag: str) -> dict:
        """
        Safely escape a string for inclusion in an XPath string literal.

        Purpose:
        - XPath does not allow unescaped mixing of single (') and double (") quotes
          inside string literals.
        - This helper ensures any arbitrary string can be safely embedded into
          an XPath expression without causing syntax errors.

        Escaping strategy:
        - If the string contains ONLY double quotes → wrap with single quotes.
        - If the string contains ONLY single quotes → wrap with double quotes.
        - If the string contains BOTH quote types → use XPath `concat()` to
          assemble the literal safely.

        Why `concat()` is required:
        - XPath does not support escaping quotes inside literals.
        - `concat()` allows us to split the string into safe fragments and
          reconstruct it at runtime inside the XPath engine.

        Design considerations:
        - This method is deterministic and side-effect free.
        - It performs no XPath execution—only string transformation.
        - Centralizing this logic avoids subtle XPath bugs scattered across code.

        Args:
            s (str):
                Raw string value that may contain single and/or double quotes.

        Returns:
            str:
                A valid XPath string literal representation of `s`.
        """
        extra = {
            k: v
            for k, v in attrs.items()
            if k != used_key and v and (isinstance(v, str) and v.lower() != "false" or not isinstance(v, str))
        }

        # Keep common fields explicitly
        extra["tag"] = tag
        extra["class"] = attrs.get("class")
        extra["id"] = attrs.get("id")
        extra["role"] = attrs.get("role")
        extra["type"] = attrs.get("type")  # For input elements
        extra["href"] = attrs.get("href")  # For links

        return extra

    # ---------------------------------------------------------
    # Element location
    # ---------------------------------------------------------

    def locate(self, element: str, index: Optional[int] = None) -> Any:
        """
        Locate a Playwright element and return the first matching handle.

        Design intent:
        - Provide a single, consistent element lookup entry point for Playwright.
        - Keep element resolution, existence checks, and index handling explicit
          and readable instead of hiding behavior in chained calls.
        - Avoid retry logic here; this method is a *pure locator*, not an assertion.

        Resolution flow:
        1. Ensure a valid Playwright page is available.
        2. Resolve logical Optics element names (if optics mapping is enabled).
        3. Convert the resolved selector into a Playwright locator.
        4. Perform a lightweight existence check before accessing the element.
        5. Apply index selection only after existence is confirmed.

        Important behavior notes:
        - Returns `None` if the element does not exist instead of raising,
          allowing callers to decide how to handle absence.
        - Indexing is optional and applied only when explicitly provided.
        - No retries, waits, or sleeps are performed here by design.
          Higher-level methods (assertions, flows) handle timing concerns.

        Args:
            element (str):
                Raw selector or logical Optics element name.
            index (Optional[int]):
                Optional zero-based index for selecting a specific match.

        Returns:
            Any:
                Playwright element handle (`locator.first`) or `None` if not found.
        """
        page = self._require_page()

        if hasattr(self.driver, "optics") and self.driver.optics:
            resolved = self.driver.optics.get_element_value(element)
            if resolved:
                element = resolved[0]

        locator, found = self._resolve_and_exists(page, element)
        if not found:
            return None

        if index is not None:
            locator = locator.nth(index)

        return locator.first

    # ---------------------------------------------------------
    # Assertions
    # ---------------------------------------------------------

    def assert_elements(self, elements, timeout=30, rule="any"):
        """
        Assert the presence of one or more elements on the current page.

        Design goals:
        - Provide a single, reusable assertion entry point for Playwright-based
          element presence checks.
        - Avoid duplicated control-flow and retry logic by delegating polling
          behavior to `_retry_until`.
        - Keep element resolution (`_resolve_locator`) and existence checks
          (`_locator_exists`) clearly separated from assertion semantics.

        Behavior:
        - `elements` may be a single selector or a list of selectors.
        - `rule="any"`  → assertion passes if at least one element is present.
        - `rule="all"`  → assertion passes only if all elements are present.
        - The check is retried until `timeout` expires.

        Important implementation notes:
        - This method does NOT raise on assertion failure; it returns
          `(False, timestamp)` to allow higher-level flows to decide behavior.
        - The nested `check()` function is intentionally minimal and stateless
          to prevent Sonar duplication and make retry logic generic.
        - No waits, sleeps, or retries should be added outside `_retry_until`.

        Returns:
            Tuple[bool, str]:
                - bool: assertion result
                - str : timestamp when the final evaluation occurred
        """
        if rule not in ("any", "all"):
            raise OpticsError(Code.E0403, message="Invalid rule. Use 'any' or 'all'.")

        if isinstance(elements, str):
            elements = [elements]

        try:
            page = self._require_page()
        except OpticsError:
            return False, utils.get_timestamp()

        def check():
            states = [
                self._resolve_and_exists(page, el)[1]
                for el in elements
            ]
            return any(states) if rule == "any" else all(states)

        return self._retry_until(timeout, check), utils.get_timestamp()

    @staticmethod
    def _resolve_locator(page: Any, element: str):
        """
        Centralized Playwright locator resolution.

        This method converts a generic element identifier into a concrete
        Playwright Locator based on its detected type.

        Why this method exists:
        - Ensures a SINGLE, consistent mapping between Optics element strings
          and Playwright locator APIs.
        - Prevents duplicated branching logic (Text / XPath / CSS) across
          locate(), assertions, and retry flows.
        - Keeps element-type detection isolated from business logic so future
          locator strategies (e.g. role-based, test-id, accessibility) can be
          added safely in one place.

        Resolution rules:
        - Text   → page.get_by_text(..., exact=False)
        - XPath  → page.locator("xpath=...")
        - Default→ page.locator(...) (CSS selector)

        IMPORTANT:
        - This method must remain side-effect free.
        - It MUST NOT perform existence checks, retries, or waiting logic.
        - All presence validation should be handled by higher-level methods
          to avoid Sonar duplication and control-flow coupling.
        """
        element_type = utils.determine_element_type(element)

        if element_type == "Text":
            return page.get_by_text(element, exact=False)
        if element_type == "XPath":
            return page.locator(f"xpath={element}")

        # Default: CSS selector
        return page.locator(element)

    @staticmethod
    def _locator_exists(locator) -> bool:
        """
            Check whether a Playwright locator resolves to at least one element.

            Purpose:
            - Provide a lightweight, reusable existence check for Playwright locators.
            - Centralize the `.count()` logic so presence checks are consistent across
              locate(), assertions, and retry-based workflows.
            - Avoid raising Playwright or async-related exceptions during control flow.

            Design decisions:
            - Uses `locator.count()` instead of accessing `.first` directly to avoid
              triggering Playwright errors when no elements exist.
            - Wraps the call in a try/except block to guarantee a boolean result
              under all circumstances (timeouts, detached DOM, navigation, etc.).

            Behavior guarantees:
            - Returns `True`  → at least one matching element exists.
            - Returns `False` → no matching elements exist OR an exception occurred.
            - Never raises an exception to the caller.

            Important notes:
            - This method performs **no waiting or retry logic**.
            - It must remain side-effect free and fast.
            - Timing concerns should be handled by `_retry_until` or higher-level APIs.

            Args:
                locator:
                    A Playwright Locator instance.

            Returns:
                bool:
                    `True` if one or more elements are present, otherwise `False`.
        """
        try:
            return run_async(locator.count()) > 0
        except Exception:
            return False

    @staticmethod
    def _retry_until(timeout: int, condition_fn) -> bool:
        """
            Generic retry helper with time-bound polling.

            Purpose:
            - Repeatedly evaluates a caller-provided condition function until it
              returns True or the timeout period expires.
            - Centralizes retry logic to avoid duplicated loops across locator,
              assertion, and state-checking methods.

            Design principles:
            - Time-based, not attempt-based: retries are controlled strictly by
              elapsed time, ensuring predictable behavior.
            - Exception-tolerant: transient errors (DOM updates, navigation,
              stale elements) are expected and intentionally ignored.
            - Side-effect free: this method does not perform waits, logging,
              or element resolution itself.

            Usage expectations:
            - `condition_fn` must be a callable returning a boolean.
            - `condition_fn` may raise exceptions; they will be swallowed and retried.
            - Callers are responsible for deciding what a "successful" condition means.

            Returns:
                bool:
                    True  → condition satisfied within timeout
                    False → timeout reached without success
            """
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                if condition_fn():
                    return True
            except Exception:
                # Intentionally ignore transient exceptions from condition_fn
                # (e.g. DOM mutations, stale elements, timing issues) and
                # continue retrying until timeout is reached.
                pass
            time.sleep(0.3)
        return False

    def _resolve_and_exists(self, page: Any, element: str) -> Tuple[Any, bool]:
        """
        Resolve an element selector into a Playwright locator and evaluate its presence.

        Purpose:
        - Provide a small, reusable helper that combines locator resolution and
          existence checking into a single, explicit step.
        - Reduce repeated patterns where callers need both the resolved locator
          and a boolean presence result.

        Design intent:
        - Keep resolution logic (`_resolve_locator`) and existence checks
          (`_locator_exists`) composed but not hidden.
        - Avoid embedding retry, wait, or assertion behavior in this method.
        - Make calling code more readable by returning both values together.

        Behavior notes:
        - The returned locator is always created, even if the element does not exist.
        - Presence is determined at the moment of invocation; no retries or waits
          are performed here.
        - This method does not raise if the element is missing.

        Args:
            page (Any):
                Active Playwright page instance used for locator resolution.
            element (str):
                Raw selector or resolved Optics element identifier.

        Returns:
            Tuple[Any, bool]:
                - Any  : Playwright locator for the resolved element
                - bool : True if at least one matching element exists, False otherwise
        """
        locator = self._resolve_locator(page, element)
        return locator, self._locator_exists(locator)
