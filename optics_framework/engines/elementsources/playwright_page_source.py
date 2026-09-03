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
        internal_logger.debug(
            "[PlaywrightPageSource] driver=%s | has_page_attr=%s | page=%s",
            self.driver,
            hasattr(self.driver, "page") if self.driver else False,
            getattr(self.driver, "page", None) if self.driver else None
        )

        # 🔴 Driver not injected
        if self.driver is None:
            raise OpticsError(
                Code.E0101,
                message=(
                    "Playwright driver is not injected into PlaywrightPageSource. "
                    "Session may not be initialized."
                )
            )

        # 🔴 Driver exists but page attribute missing
        if not hasattr(self.driver, "page"):
            raise OpticsError(
                Code.E0101,
                message=(
                    "Playwright driver does not expose 'page'. "
                    "Invalid driver implementation or setup."
                )
            )

        # 🔴 Page attribute exists but page not yet created
        if self.driver.page is None:
            raise OpticsError(
                Code.E0101,
                message=(
                    "Playwright page is not initialized yet. "
                    "Ensure launch_app() completed before using element sources."
                )
            )

        self.page = self.driver.page
        return self.page

    # ---------------------------------------------------------
    # Required interface methods
    # ---------------------------------------------------------

    def capture(self):
        internal_logger.exception(
            "PlaywrightPageSource does not support screen capture."
        )
        raise NotImplementedError(
            "PlaywrightPageSource does not support screen capture."
        )

    def get_page_source(self) -> Tuple[str, str]:
        """
        Returns full DOM HTML and timestamp.
        Returns:
            Tuple[str, str]: (page_source, timestamp)
        """
        page = self._require_page()
        timestamp = utils.get_timestamp()

        html: str = run_async(page.content())
        internal_logger.debug(
            "[PlaywrightPageSource] Page source fetched, length=%d",
            len(html)
        )
        self.tree = etree.HTML(html)
        self.root = self.tree

        internal_logger.debug(
            "========== PLAYWRIGHT PAGE SOURCE FETCHED =========="
        )
        internal_logger.debug(
            "========== XML tree ========== %s ",html
        )
        internal_logger.debug("Timestamp: %s", timestamp)

        return html, str(timestamp)

    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> List[Dict]:
        """
        Cross-platform element extraction for web pages.

        Args:
            filter_config: Optional list of filter types. Valid values:
                - "all": Show all elements (default when None or empty)
                - "interactive": Only interactive elements
                - "buttons": Only button elements
                - "inputs": Only input/text field elements
                - "images": Only image elements
                - "text": Only text elements
                Can be combined: ["buttons", "inputs"]

        Returns:
            List of dictionaries with keys: text, bounds, xpath, extra
        """
        # Ensure page source is fetched and parsed
        self.get_page_source()  # Returns (html, timestamp); updates self.tree

        if self.tree is None:
            internal_logger.error("[PlaywrightPageSource] Tree is None, cannot extract elements")
            return []

        page = self._require_page()
        elements = self.tree.xpath(".//*")
        # One page.evaluate() call resolves bounds for every candidate node, instead of a
        # Playwright locator round-trip per node (count() + bounding_box() each).
        node_xpaths = [self._build_simple_xpath(node) for node in elements]
        rects = self._resolve_bounds_batch(page, node_xpaths)
        results = []

        for node, rect in zip(elements, rects):
            bounds = self._rect_to_bounds(rect)
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

    # In-browser: resolves each xpath via document.evaluate and reports its
    # getBoundingClientRect(), or null when unmatched / not laid out. The visibility
    # check approximates Playwright's own "not visible" -> null bounding_box() contract,
    # since that internal algorithm isn't reachable from plain page.evaluate() JS.
    _BOUNDS_BATCH_SCRIPT = """
    (xpaths) => xpaths.map((xp) => {
        if (!xp) return null;
        try {
            const result = document.evaluate(
                xp, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null
            );
            const el = result.singleNodeValue;
            if (!el) return null;
            const rect = el.getBoundingClientRect();
            if (!rect || rect.width <= 0 || rect.height <= 0) return null;
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') return null;
            return { x: rect.x, y: rect.y, width: rect.width, height: rect.height };
        } catch (e) {
            return null;
        }
    });
    """

    def _resolve_bounds_batch(
        self, page: Any, xpaths: List[Optional[str]]
    ) -> List[Optional[Dict[str, float]]]:
        """Resolve bounding rects for every candidate xpath in one round trip."""
        if not xpaths:
            return []
        try:
            rects = run_async(page.evaluate(self._BOUNDS_BATCH_SCRIPT, xpaths))
            if rects is None or len(rects) != len(xpaths):
                return [None] * len(xpaths)
            return rects
        except Exception as e:
            # The batch call itself failed (e.g. page mid-navigation). Falling back to one
            # evaluate() per node here would reintroduce the exact per-node round-trip cost
            # this batching exists to avoid, so degrade to "no bounds this pass" instead,
            # same as a single node failing used to.
            internal_logger.debug(
                f"[PlaywrightPageSource] Batched bounds lookup failed: {e}"
            )
            return [None] * len(xpaths)

    @staticmethod
    def _rect_to_bounds(rect: Optional[Dict[str, float]]) -> Optional[Dict[str, int]]:
        """Convert a ``{x,y,width,height}`` rect (or ``None``) into ``{x1,y1,x2,y2}``."""
        if not rect:
            return None
        try:
            x1 = int(rect["x"])
            y1 = int(rect["y"])
            x2 = int(rect["x"] + rect["width"])
            y2 = int(rect["y"] + rect["height"])
        except (KeyError, TypeError, ValueError):
            return None
        return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

    def _escape_xpath_value(self, val: str) -> str:
        """
        Escape single quotes in XPath value.

        :param val: Value to escape
        :return: Escaped XPath value
        """
        if "'" not in val:
            return f"'{val}'"
        # Use concat for values with single quotes
        parts = val.split("'")
        return "concat('" + "', \"'\", '".join(parts) + "')"

    def _build_xpath_from_attribute(self, tag: str, attr_name: str, attr_value: str) -> str:
        """
        Build XPath from a single attribute.

        :param tag: Element tag name
        :param attr_name: Attribute name
        :param attr_value: Attribute value
        :return: XPath string
        """
        escaped_value = self._escape_xpath_value(attr_value)
        return f"//{tag}[@{attr_name}={escaped_value}]"

    def _try_unique_attributes(self, tag: str, attrs: dict) -> Optional[str]:
        """
        Try to build XPath from unique attributes (id, data-testid, name).

        :param tag: Element tag name
        :param attrs: Element attributes
        :return: XPath string or None
        """
        for attr_name in ["id", "data-testid", "name"]:
            if attr_name in attrs and attrs[attr_name]:
                return self._build_xpath_from_attribute(tag, attr_name, attrs[attr_name])
        return None

    def _build_hierarchical_path(self, node: etree.Element) -> Optional[str]:
        """
        Build hierarchical XPath path with index.

        :param node: Element node
        :return: XPath path string or None
        """
        path = []
        current = node
        while current is not None and hasattr(current, "tag"):
            parent = current.getparent()
            if parent is None:
                path.insert(0, current.tag or "*")
                break

            siblings = [sib for sib in parent if sib.tag == current.tag]
            if len(siblings) > 1:
                idx = siblings.index(current) + 1
                path.insert(0, f"{current.tag}[{idx}]")
            else:
                path.insert(0, current.tag or "*")

            current = parent

        return "/" + "/".join(path) if path else None

    def _build_simple_xpath(self, node: etree.Element) -> Optional[str]:
        """
        Build a simple XPath for locating an element in Playwright.
        This is a fallback method for getting bounds.
        """
        if node is None or not hasattr(node, "tag"):
            return None

        tag = node.tag or "*"
        attrs = node.attrib or {}

        # Try unique attributes first
        xpath = self._try_unique_attributes(tag, attrs)
        if xpath:
            return xpath

        # Fallback to hierarchical path with index
        return self._build_hierarchical_path(node)

    def _try_text_content(self, node: etree.Element) -> Tuple[Optional[str], Optional[str]]:
        """
        Try to extract text from node text content or tail.

        :param node: Element node
        :return: Tuple of (text_value, attribute_used) or (None, None)
        """
        text_content = node.text
        if text_content and text_content.strip():
            return text_content.strip(), "text"

        if node.tail and node.tail.strip():
            return node.tail.strip(), "tail"

        return None, None

    def _try_attribute_text(self, attrs: dict, attr_name: str, key: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Try to extract text from a specific attribute.

        :param attrs: Element attributes
        :param attr_name: Attribute name to check
        :param key: Key name to return
        :return: Tuple of (text_value, attribute_used) or (None, None)
        """
        value = attrs.get(attr_name, "").strip()
        if value:
            return value, key
        return None, None

    def _try_inner_text(self, node: etree.Element, page: Any) -> Tuple[Optional[str], Optional[str]]:
        """
        Try to get innerText via Playwright (slower, but more accurate).

        :param node: Element node
        :param page: Playwright page object
        :return: Tuple of (text_value, attribute_used) or (None, None)
        """
        try:
            xpath = self._build_simple_xpath(node)
            if not xpath:
                return None, None

            locator = page.locator(f"xpath={xpath}")
            count = run_async(locator.count())
            if count > 0:
                inner_text = run_async(locator.first.inner_text())
                if inner_text and inner_text.strip():
                    return inner_text.strip(), "innerText"
        except Exception as e:
            internal_logger.debug(f"Failed to get innerText via Playwright: {e}")

        return None, None

    def _try_class_text(self, attrs: dict) -> Tuple[Optional[str], Optional[str]]:
        """
        Try to extract text from class attribute (last resort).

        :param attrs: Element attributes
        :return: Tuple of (text_value, attribute_used) or (None, None)
        """
        class_name = attrs.get("class", "").strip()
        if class_name:
            first_class = class_name.split()[0]
            if first_class:
                return first_class, "class"
        return None, None

    def _extract_display_text(self, node: etree.Element, page: Any) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract display text from a web element.

        Priority order:
        1. Text content from lxml element (fastest)
        2. aria-label
        3. title
        4. alt (for images)
        5. placeholder (for inputs)
        6. innerText (via Playwright, slower)
        7. id
        8. class (last resort)

        Returns:
            Tuple of (text_value, attribute_used)
        """
        attrs = node.attrib or {}

        # Try text content from lxml element first (fastest)
        text, key = self._try_text_content(node)
        if text:
            return text, key

        # Try attributes in priority order
        for attr_name, key_name in [("aria-label", "aria-label"), ("title", "title"),
                                     ("alt", "alt"), ("placeholder", "placeholder")]:
            text, key = self._try_attribute_text(attrs, attr_name, key_name)
            if text:
                return text, key

        # Try to get innerText via Playwright (slower, but more accurate)
        text, key = self._try_inner_text(node, page)
        if text:
            return text, key

        # Try id
        text, key = self._try_attribute_text(attrs, "id", "id")
        if text:
            return text, key

        # Try class (last resort)
        return self._try_class_text(attrs)

    def _check_filter_type(self, node: etree.Element, filter_type: str) -> bool:
        """
        Check if element matches a specific filter type.

        :param node: Element node
        :param filter_type: Filter type to check
        :return: True if element matches filter type
        """
        filter_checks = {
            "interactive": self._is_probably_interactive,
            "buttons": self._is_button,
            "inputs": self._is_input,
            "images": self._is_image,
            "text": self._is_text,
        }
        check_func = filter_checks.get(filter_type)
        return check_func(node) if check_func else False

    def _should_include_element(self, node: etree.Element, filter_config: Optional[List[str]]) -> bool:
        """
        Determine if an element should be included based on filter_config.

        Args:
            node: The XML element node
            filter_config: Optional list of filter types

        Returns:
            True if element should be included, False otherwise
        """
        # Default behavior: show all elements when filter_config is None or empty
        if not filter_config or len(filter_config) == 0:
            return True

        # If "all" is in filter_config, show all elements
        if "all" in filter_config:
            return True

        # Check each filter type
        for filter_type in filter_config:
            if self._check_filter_type(node, filter_type):
                return True

        return False

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
        """Check if element is a text element (non-input)."""
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
        Check if element is probably interactive (clickable, enabled, etc.).
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

    def _determine_xpath_uniqueness(self, xpath: str, doc_tree: Any, node: etree.Element) -> Tuple[bool, Optional[int]]:
        """
        Evaluate XPath against document and determine uniqueness.

        :param xpath: XPath string to evaluate
        :param doc_tree: Document tree
        :param node: Target node
        :return: Tuple of (is_unique, index_if_not_unique)
        """
        try:
            matches = doc_tree.xpath(xpath)
        except (etree.XPathError, ValueError, TypeError):
            return False, None

        if not matches:
            return False, None

        if len(matches) > 1:
            try:
                idx = matches.index(node)
            except ValueError:
                idx = 0
            return False, idx

        return True, None

    def _build_xpath_from_single_attribute(self, tag: str, attrs: dict, attr_name: str, val: Optional[str] = None) -> Optional[str]:
        """
        Build XPath from a single attribute.

        :param tag: Element tag name
        :param attrs: Element attributes
        :param attr_name: Attribute name
        :param val: Optional attribute value override
        :return: XPath string or None
        """
        value = val if val is not None else attrs.get(attr_name)
        if not value:
            return None

        if attr_name == "class":
            class_token = value.strip().split()[0]
            if not class_token:
                return None
            token_literal = self._escape_for_xpath_literal(f" {class_token} ")
            return f"//{tag}[contains(concat(' ', normalize-space(@class), ' '), {token_literal})]"

        return f"//{tag}[@{attr_name}={self._escape_for_xpath_literal(value)}]"

    def _build_xpath_from_attribute_pair(self, tag: str, attrs: dict, a1: str, a2: str) -> Optional[str]:
        """
        Build XPath from a pair of attributes.

        :param tag: Element tag name
        :param attrs: Element attributes
        :param a1: First attribute name
        :param a2: Second attribute name
        :return: XPath string or None
        """
        v1, v2 = attrs.get(a1), attrs.get(a2)
        if not v1 or not v2:
            return None
        return f"//{tag}[@{a1}={self._escape_for_xpath_literal(v1)} and @{a2}={self._escape_for_xpath_literal(v2)}]"

    def _build_xpath_from_text(self, tag: str, node: etree.Element) -> Optional[str]:
        """
        Build XPath from text content (short text only).

        :param tag: Element tag name
        :param node: Element node
        :return: XPath string or None
        """
        try:
            text_content = " ".join(node.itertext()).strip()
        except Exception:
            return None
        if not text_content:
            return None
        # Avoid overly long or noisy text selectors
        if len(text_content) > 80 or "\n" in text_content:
            return None
        return f"//{tag}[normalize-space(.)={self._escape_for_xpath_literal(text_content)}]"

    def _resolve_xpath(self, xpath: str, doc_tree: Any, node: etree.Element) -> Optional[str]:
        """
        Return unique or semi-unique XPath.

        :param xpath: XPath string to resolve
        :param doc_tree: Document tree
        :param node: Target node
        :return: Resolved XPath string or None
        """
        is_unique, idx = self._determine_xpath_uniqueness(xpath, doc_tree, node)
        if is_unique:
            return xpath
        if idx is not None:
            return f"({xpath})[{idx + 1}]"
        return None

    def _try_unique_attributes_xpath(self, tag: str, attrs: dict, doc_tree: Any, node: etree.Element) -> Optional[str]:
        """
        Try to build XPath from unique attributes.

        :param tag: Element tag name
        :param attrs: Element attributes
        :param doc_tree: Document tree
        :param node: Target node
        :return: Resolved XPath string or None
        """
        unique_attrs = [
            "id", "data-testid", "data-test", "data-qa", "data-cy",
            "data-automation", "name",
        ]
        for attr in unique_attrs:
            xpath = self._build_xpath_from_single_attribute(tag, attrs, attr)
            if xpath:
                resolved = self._resolve_xpath(xpath, doc_tree, node)
                if resolved:
                    return resolved
        return None

    def _try_attribute_pairs_xpath(self, tag: str, attrs: dict, doc_tree: Any, node: etree.Element) -> Optional[str]:
        """
        Try to build XPath from attribute pairs.

        :param tag: Element tag name
        :param attrs: Element attributes
        :param doc_tree: Document tree
        :param node: Target node
        :return: Resolved XPath string or None
        """
        unique_attrs = ["id", "data-testid", "data-test", "data-qa", "data-cy", "data-automation", "name"]
        pair_candidates = unique_attrs + ["aria-label", "placeholder", "title", "alt", "role", "type"]
        for i, a1 in enumerate(pair_candidates):
            for a2 in pair_candidates[i + 1:]:
                xpath = self._build_xpath_from_attribute_pair(tag, attrs, a1, a2)
                if xpath:
                    resolved = self._resolve_xpath(xpath, doc_tree, node)
                    if resolved:
                        return resolved
        return None

    def _try_text_xpath(self, tag: str, node: etree.Element, doc_tree: Any) -> Optional[str]:
        """
        Try to build XPath from text content.

        :param tag: Element tag name
        :param node: Element node
        :param doc_tree: Document tree
        :return: Resolved XPath string or None
        """
        text_xpath = self._build_xpath_from_text(tag, node)
        if text_xpath:
            return self._resolve_xpath(text_xpath, doc_tree, node)
        return None

    def _try_maybe_unique_attributes_xpath(self, tag: str, attrs: dict, doc_tree: Any, node: etree.Element) -> Optional[str]:
        """
        Try to build XPath from maybe-unique attributes.

        :param tag: Element tag name
        :param attrs: Element attributes
        :param doc_tree: Document tree
        :param node: Target node
        :return: Resolved XPath string or None
        """
        maybe_unique_attrs = [
            "aria-label", "placeholder", "title", "alt", "role", "type", "class",
        ]
        for attr in maybe_unique_attrs:
            xpath = self._build_xpath_from_single_attribute(tag, attrs, attr)
            if xpath:
                resolved = self._resolve_xpath(xpath, doc_tree, node)
                if resolved:
                    return resolved
        return None

    def get_xpath(self, node: etree.Element) -> str:
        """
        Generate an optimal XPath for a given HTML element.

        Prioritizes:
        1. Stable unique attributes (id, data-testid, name, etc.)
        2. Attribute pairs for semi-uniqueness (fast + reliable)
        3. Short text-based selectors when safe
        4. Fallbacks like aria-label, title, placeholder, class token
        5. Hierarchical path with tag names
        """
        if node is None or not hasattr(node, "tag"):
            return ""

        tag = node.tag or "*"
        attrs = node.attrib or {}
        doc_tree = node.getroottree()

        # Try unique attributes first
        xpath = self._try_unique_attributes_xpath(tag, attrs, doc_tree, node)
        if xpath:
            return xpath

        # Try attribute pairs for better uniqueness
        xpath = self._try_attribute_pairs_xpath(tag, attrs, doc_tree, node)
        if xpath:
            return xpath

        # Try short text-based XPath (useful for buttons/links)
        xpath = self._try_text_xpath(tag, node, doc_tree)
        if xpath:
            return xpath

        # Try maybe-unique attributes
        xpath = self._try_maybe_unique_attributes_xpath(tag, attrs, doc_tree, node)
        if xpath:
            return xpath

        # Fallback to hierarchical path
        return self._build_hierarchical_xpath(node)

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
        Safely escape a string for inclusion in an XPath string literal.
        Uses the concat() trick if both single and double quotes are present.
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
        Build metadata dictionary with element attributes.

        Args:
            attrs: Element attributes dictionary
            used_key: The attribute key used for text extraction (to exclude from extra)
            tag: Element tag name

        Returns:
            Dictionary with metadata
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

    def _resolve_optics_element(self, element: str) -> tuple[str, str]:
        """
        Resolve Optics element name to selector.

        :param element: Original element string
        :return: Tuple of (original_element, resolved_element)
        """
        original_element = element

        if hasattr(self.driver, "optics") and self.driver.optics:
            resolved = self.driver.optics.get_element_value(element)
            if resolved:
                element = resolved[0]
                internal_logger.debug(
                    "[PlaywrightLocate] Resolved element '%s' → '%s'",
                    original_element, element
                )
            else:
                internal_logger.debug(
                    "[PlaywrightLocate] Using raw selector '%s'",
                    element
                )

        return original_element, element

    def _build_playwright_locator(self, page: Any, element: str, element_type: str) -> Any:
        """
        Build Playwright locator based on element type.

        :param page: Playwright page object
        :param element: Element selector string
        :param element_type: Type of element (Text, XPath, CSS)
        :return: Playwright locator
        """
        if element_type == "Text":
            text_value = self._strip_prefix_for_page_source(element, "text=")
            return page.get_by_text(text_value, exact=False)

        if element_type == "XPath":
            xpath_value = self._strip_prefix_for_page_source(element, "xpath=")
            return page.locator(f"xpath={xpath_value}")

        # CSS / default
        css_value = self._strip_prefix_for_page_source(element, "css=")
        return page.locator(css_value)

    def _strip_prefix_for_page_source(self, element: str, prefix: str) -> str:
        """
        Strip prefix from element string if present (case-insensitive).

        :param element: Element string
        :param prefix: Prefix to strip (e.g., "xpath=", "text=", "css=")
        :return: Element string without prefix
        """
        if element.lower().startswith(prefix.lower()):
            eq_index = element.find("=")
            return element[eq_index + 1:] if eq_index >= 0 else element
        return element

    def locate(self, element: str, index: Optional[int] = None) -> Any:
        page = self._require_page()

        original_element, element = self._resolve_optics_element(element)
        element_type = utils.determine_element_type(element)

        try:
            locator = self._build_playwright_locator(page, element, element_type)

            if index is not None:
                locator = locator.nth(index)

            count = run_async(locator.count())
            internal_logger.debug(
                "[PlaywrightLocate] Locator '%s' found %d elements",
                element, count
            )

            if count == 0:
                return None

            return locator.first

        except Exception as e:
            internal_logger.error(
                "[PlaywrightLocate] Error locating element '%s' (resolved='%s')",
                original_element,
                element,
                exc_info=True
            )
            raise OpticsError(
                Code.E0201,
                message=f"No elements found for: {original_element}",
                cause=e,
            ) from e

    # ---------------------------------------------------------
    # Assertions
    # ---------------------------------------------------------

    def _check_single_element_presence(self, page: Any, element: str) -> bool:
        """
        Check if a single element is present on the page.

        :param page: Playwright page object
        :param element: Element selector string
        :return: True if element is found, False otherwise
        """
        try:
            internal_logger.debug(
                "[PlaywrightPageSource] Element '%s'",
                element
            )
            element_type = utils.determine_element_type(element)
            if element_type == "Text":
                locator = page.get_by_text(element, exact=False)
            elif element_type == "XPath":
                locator = page.locator(f"xpath={element}")
            else:
                # CSS selector
                locator = page.locator(element)

            count = run_async(locator.count())
            return count > 0
        except Exception as e:
            internal_logger.debug(
                "[PlaywrightPageSource] Error checking '%s': %s",
                element, str(e)
            )
            return False

    def _check_elements_batch(self, page: Any, elements: List[str], rule: str) -> Tuple[bool, List[bool]]:
        """
        Check a batch of elements and return results.

        :param page: Playwright page object
        :param elements: List of element selectors
        :param rule: Assertion rule ("any" or "all")
        :return: Tuple of (should_return_early, results_list)
        """
        results = []
        for element in elements:
            found = self._check_single_element_presence(page, element)
            results.append(found)

            if rule == "any" and found:
                return True, results

        if rule == "all" and all(results):
            return True, results

        return False, results

    def assert_elements(self, elements, timeout=30, rule="any"):
        """
        Assert the presence of elements on the current page (Playwright).

        Args:
            elements (list | str): List of selectors or single selector
            timeout (int): Max wait time in seconds
            rule (str): "any" or "all"

        Returns:
            (bool, str): (status, timestamp)
        """
        if rule not in ("any", "all"):
            raise OpticsError(Code.E0403, message="Invalid rule. Use 'any' or 'all'.")

        if isinstance(elements, str):
            elements = [elements]

        # Ensure driver is initialized before entering the loop (OpticsError propagates if not)
        page = self._require_page()

        start_time = time.time()

        internal_logger.info(
            "[PlaywrightPageSource] Asserting elements=%s rule=%s timeout=%ss",
            elements, rule, timeout
        )

        while time.time() - start_time < timeout:
            should_return, _ = self._check_elements_batch(page, elements, rule)
            if should_return:
                return True, utils.get_timestamp()

            time.sleep(0.3)

        internal_logger.warning(
            "[PlaywrightPageSource] Timeout reached. rule=%s elements=%s",
            rule, elements
        )
        raise TimeoutError(
            f"Timeout reached: Elements not found based on rule '{rule}': {elements}"
        )
