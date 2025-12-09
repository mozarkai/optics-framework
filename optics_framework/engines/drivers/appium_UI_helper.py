import re
from typing import List, Dict, Tuple, Optional
from fuzzywuzzy import fuzz
from lxml import etree
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
## Removed import of get_appium_driver (no longer needed)


class UIHelper:
    def __init__(self, appium_driver):
        """
        Initialize UIHelper with Appium object (not just WebDriver).
        """
        self.driver = appium_driver
        self.tree = None
        self.root = None
        self.prev_hash = None

    def get_page_source(self):
        """
        Fetch the current UI tree (page source) from the Appium driver.
        """
        time_stamp = utils.get_timestamp()
        page_source = self.driver.driver.page_source
        self.tree = etree.ElementTree(etree.fromstring(page_source.encode("utf-8")))
        self.root = self.tree.getroot()
        internal_logger.debug("\n\n========== PAGE SOURCE FETCHED ==========")
        internal_logger.debug(f"Page source fetched at: {time_stamp}")
        internal_logger.debug("\n==========================================")
        utils.save_page_source(page_source, time_stamp, self.driver.event_sdk.config_handler.config.execution_output_path)
        return page_source, time_stamp

    # fetching page source and handling UI tree
    def get_distinct_page_source(self):
        """
        Fetch the current UI tree (page source) from the Appium driver continuously.
        Update instance's root and tree attributes when there's a change in page UI.
        """
        time_stamp = utils.get_timestamp()
        page_source = self.driver.driver.page_source
        new_hash = utils.compute_hash(page_source)

        if self.prev_hash == new_hash:
            internal_logger.debug(
                "\nPage source unchanged. Skipping further processing.\n"
            )
            return None, time_stamp

        self.prev_hash = new_hash
        self.tree = etree.ElementTree(etree.fromstring(page_source.encode("utf-8")))
        self.root = self.tree.getroot()
        internal_logger.debug("\n\n========== PAGE SOURCE FETCHED ==========")
        internal_logger.debug(f"Page source fetched at: {time_stamp}")
        internal_logger.debug("\n==========================================")
        return page_source, time_stamp

    def find_xpath_from_text(self, text):
        """
        Find the XPath of an element based on the text content.

        Args:
            text (str): The text content to search for in the UI tree.

        Returns:
            str: The XPath of the element containing the
            text content, or None if not found.
        """
        locators = self.get_locator_and_strategy(text)
        if locators:
            strategy = locators["strategy"]
            locator = locators["locator"]
            xpath = self.get_view_locator(strategy=strategy, locator=locator)
            return xpath
        return None

    def find_xpath(self, xpath):
        """
        Process the given XPath and return the exact path from the UI tree after applying various matching strategies.
        """
        internal_logger.debug(f"Finding Xpath {xpath}...")
        _, time_stamp = (
            self.get_page_source()
        )  # Fetch UI tree when processing the XPath
        try:
            # 1. Exact Match
            try:
                # internal_logger.debug(f'Finding Xpath using exact match')
                found_xpath = self.find_exact(xpath)

                if found_xpath:
                    internal_logger.debug("Xpath found using exact match")
                    return found_xpath, time_stamp
            except Exception as e:
                internal_logger.error(
                    f"Error in exact match for XPath '{xpath}': {str(e)}"
                )

            # 2. Relative Match
            try:
                found_xpath = self.find_relative(xpath)
                if found_xpath:
                    internal_logger.debug("Xpath found using relative match")
                    return found_xpath, time_stamp
            except Exception as e:
                internal_logger.error(
                    f"Error in relative match for XPath '{xpath}': {str(e)}"
                )

            # 3. Partial Match
            try:
                found_xpath = self.find_partial(xpath)
                if found_xpath:
                    internal_logger.debug("Xpath found using partial match")
                    return found_xpath, time_stamp
            except Exception as e:
                internal_logger.error(
                    f"Error in partial match for XPath '{xpath}': {str(e)}"
                )

            # 4. Attribute Match with Fuzzy Prefix and Suffix Handling
            try:
                found_xpath = self.find_attribute_match(xpath)
                if found_xpath:
                    internal_logger.debug("Xpath found using attribute match")
                    return found_xpath, time_stamp
            except Exception as e:
                internal_logger.error(
                    f"Error in attribute match for XPath '{xpath}': {str(e)}"
                )

            # If no match is found
            internal_logger.error(
                f"No match found for XPath '{xpath}' after applying all strategies."
            )
            return None, None
        except Exception as e:
            internal_logger.error(
                f"Unexpected error in find_xpath for XPath '{xpath}': {str(e)}"
            )
            return None, None

    def find_exact(self, xpath):
        """Attempts an exact match for the given XPath."""
        try:
            elements = self.root.xpath(xpath)
            if elements:
                internal_logger.debug(f"Exact XPath: {xpath}")
                return xpath
        except Exception as e:
            internal_logger.debug(f"Exact match error: {e}")
        return None

    def find_relative(self, xpath):
        """Attempts to match a simplified, relative XPath."""
        relative_xpath = self.make_relative(xpath)
        internal_logger.debug(f"Relative XPath: {relative_xpath}")
        try:
            elements = self.root.xpath(relative_xpath)
            if elements:
                internal_logger.debug(
                    f"Relative match found, element type: {type(elements[0])}"
                )
                # Use ElementTree.getpath()
                return self.simplify_xpath(elements[0])
        except Exception as e:
            internal_logger.debug(f"Relative match error: {e}")
        return None

    def make_relative(self, xpath):
        """
        Simplifies the XPath by removing intermediate elements,
        supports both Android and iOS, and avoids redundant `//`.
        """
        parts = xpath.split("/")
        simplified = []

        for part in parts:
            # Skip empty parts (caused by leading or redundant slashes)
            if not part:
                continue

            # Add `//` only if the part starts with specific prefixes and doesn't already have `//`
            if part.startswith("android.widget") or part.startswith("XCUIElementType"):
                if simplified and simplified[-1].startswith("//"):
                    simplified.append(part)
                else:
                    simplified.append("//" + part)
            else:
                simplified.append(part)

        # Join the parts back into a single simplified XPath
        return "/".join(simplified)

    def make_partial_match(self, xpath):
        """
        Converts the XPath for partial matching using `contains()` for specified attributes,
        while ignoring common stop words.
        """
        # List of attributes to support for partial matching
        attributes = ["content-desc", "resource-id", "name", "value", "label", "text"]

        # List of common stop words to ignore
        stop_words = {"a", "an", "the", "for", "in", "on", "with", "of", "and", "or"}

        # Loop through each supported attribute and process it
        for attribute in attributes:
            attr_placeholder = f"@{attribute}="
            if attr_placeholder in xpath:
                # Extract the attribute value
                attr_value = self.extract_attribute(xpath, attribute)
                if attr_value:
                    # Split the attribute value into terms
                    terms = attr_value.split()
                    # Filter out stop words
                    filtered_terms = [
                        term for term in terms if term.lower() not in stop_words
                    ]
                    if filtered_terms:
                        # Construct conditions using `contains()` for each filtered term
                        conditions = " or ".join(
                            [
                                f'contains(@{attribute}, "{term}")'
                                for term in filtered_terms
                            ]
                        )
                        # Replace the exact match in the XPath with the partial match conditions
                        xpath = xpath.replace(
                            f'@{attribute}="{attr_value}"', conditions
                        )

        return xpath

    def find_partial(self, xpath):
        """Attempts partial matching using `contains()` in XPath."""
        partial_xpath = self.make_partial_match(xpath)
        internal_logger.debug(f"Partial XPath: {partial_xpath}")
        try:
            elements = self.root.xpath(partial_xpath)
            if elements:
                internal_logger.debug(
                    f"Partial match found, element type: {type(elements[0])}"
                )
                # Use ElementTree.getpath()
                return self.simplify_xpath(elements[0])
        except Exception as e:
            internal_logger.debug(f"Partial match error: {e}")
        return None

    def fuzzy_match_prefix(self, prefix1, prefix2):
        """Performs a fuzzy comparison of two prefixes."""
        return fuzz.ratio(prefix1, prefix2) >= 80

    def find_attribute_match(self, xpath):
        """Attempts matching by focusing on resource-id (fuzzy for prefix, exact or fuzzy for suffix)."""
        input_attributes = {
            "resource-id": self.extract_attribute(xpath, "resource-id"),
            "content-desc": self.extract_attribute(xpath, "content-desc"),
            "text": self.extract_attribute(xpath, "text"),
            "value": self.extract_attribute(xpath, "value"),
            "name": self.extract_attribute(xpath, "name"),
            "label": self.extract_attribute(xpath, "label"),
        }
        # If no attributes are found, return None
        if not any(input_attributes.values()):
            return None

        best_match = None
        best_fuzzy_score = 0

        # Ensure self.root is valid
        if not hasattr(self, "root") or self.root is None:
            internal_logger.error("Root element is not initialized.")
            return None

        for element in self.root.findall(".//*"):
            if element is None:  # Safeguard against invalid elements
                continue

            for attr, input_value in input_attributes.items():
                if not input_value:
                    continue

                elem_value = element.get(attr)
                if not elem_value:
                    continue

                # Handle splitting logic for attributes with `/`
                input_prefix, input_suffix = self.split_element(input_value)
                elem_prefix, elem_suffix = self.split_element(elem_value)

                # Check exact match first
                if input_value == elem_value:
                    internal_logger.debug(f"Exact match found for {attr}: {elem_value}")
                    return self.simplify_xpath(element)

                # Fuzzy match logic
                if self.fuzzy_match_prefix(input_prefix, elem_prefix):
                    if input_suffix == elem_suffix:
                        internal_logger.debug(
                            f"Attribute match found for {attr} with exact suffix match: {elem_value}"
                        )
                        return self.simplify_xpath(element)

                    # Fuzzy match on suffix if exact match fails
                    suffix_score = fuzz.ratio(input_suffix or "", elem_suffix or "")
                    if suffix_score > best_fuzzy_score:
                        best_fuzzy_score = suffix_score
                        best_match = element

        # Handle best fuzzy match
        if best_fuzzy_score >= 70:  # Threshold for fuzzy match acceptance
            internal_logger.debug(
                f"Fuzzy match found with score {best_fuzzy_score}, element: {best_match}"
            )
            return self.simplify_xpath(best_match)

        # No match found
        internal_logger.debug("No match found using attribute matching.")
        return None

    def split_element(self, element):
        """Splits an element into prefix and suffix."""
        return element.rsplit(":", 1) if ":" in element else (element, "")

    def extract_attribute(self, xpath, attribute):
        """Extracts the value of a given attribute from the XPath."""
        marker = f"@{attribute}="
        try:
            start = xpath.index(marker) + len(marker) + 1
            end = xpath.index('"', start)
            return xpath[start:end]
        except ValueError:
            return None

    def simplify_xpath(self, element):
        """
        Simplify the XPath by focusing on key attributes like resource-id, content-desc, text, or class.
        """
        attributes = self.extract_key_attributes(element)

        # Start building the simplified XPath based on available attributes
        xpath_parts = []

        # Always include the class name (e.g., android.widget.FrameLayout)
        if attributes["class"]:
            xpath_parts.append(f"//{attributes['class']}")

        # Add resource-id if available
        if attributes["resource-id"]:
            xpath_parts.append(f"[@resource-id='{attributes['resource-id']}']")

        # Optionally add content-desc or text if available
        if attributes["content-desc"]:
            xpath_parts.append(
                f"[contains(@content-desc, '{attributes['content-desc']}')]"
            )
        elif attributes["text"]:
            xpath_parts.append(f"[contains(@text, '{attributes['text']}')]")

        # Combine all parts into a simplified XPath
        simplified_xpath = "".join(xpath_parts)

        internal_logger.debug(f"Simplified XPath: {simplified_xpath}")
        return simplified_xpath

    def extract_key_attributes(self, element):
        """
        Extracts the key attributes from an element for both Android and iOS.

        Android attributes:
            - resource-id
            - content-desc
            - text
            - class (widget class)

        iOS attributes:
            - name
            - value
            - label
            - class (XCUIElementType)

        :param element: XML element from the UI tree.
        :return: Dictionary containing extracted attributes.
        """
        attributes = {
            "resource-id": element.attrib.get("resource-id", ""),  # Android
            "content-desc": element.attrib.get("content-desc", ""),  # Android
            "text": element.attrib.get("text", ""),  # Android
            "name": element.attrib.get("name", ""),  # iOS
            "value": element.attrib.get("value", ""),  # iOS
            "label": element.attrib.get("label", ""),  # iOS
            "class": element.tag,  # Widget class (e.g., android.widget.Button or XCUIElementTypeButton)
        }

        # Remove empty attributes for cleaner output
        attributes = {k: v for k, v in attributes.items() if v}
        return attributes

    def get_locator_and_strategy(self, element):
        """
        Determines the best strategy and locator for the given element identifier.
        """
        _, time_stamp = self.get_page_source()
        tree = self.tree

        strategies = [
            ("text", "//*[@text]", "text"),
            ("resource-id", "//*[@resource-id]", "resource-id"),
            ("content-desc", "//*[@content-desc]", "content-desc"),
            ("name", "//*[@name]", "name"),
            ("value", "//*[@value]", "value"),
            ("label", "//*[@label]", "label"),
        ]

        # First pass: exact and suffix matches
        for strategy_name, xpath_query, attrib in strategies:
            elements = self.tree.xpath(xpath_query)
            for elem in elements:
                value = elem.attrib.get(attrib, "").strip()
                if not value:
                    continue
                # Exact full match
                if value == element:
                    internal_logger.debug("Exact match found.")
                    internal_logger.debug(
                        f"Match found using '{strategy_name}' strategy: '{value}'"
                    )
                    attributes = elem.attrib
                    return {
                        "strategy": strategy_name,
                        "locator": value,
                        "attributes": attributes,
                        "timestamp": time_stamp,
                    }
                # Exact suffix match (resource-id style)
                if "/" in value and value.rsplit("/", 1)[-1] == element:
                    internal_logger.debug("Exact suffix match found.")
                    internal_logger.debug(
                        f"Match found using '{strategy_name}' strategy: '{value}'"
                    )
                    attributes = elem.attrib
                    return {
                        "strategy": strategy_name,
                        "locator": value,
                        "attributes": attributes,
                        "timestamp": time_stamp,
                    }

        # Second pass: collect fuzzy candidates and pick the best-scoring one
        best_candidate = None
        best_score = 0
        for strategy_name, xpath_query, attrib in strategies:
            elements = tree.xpath(xpath_query)
            for elem in elements:
                value = elem.attrib.get(attrib, "").strip()
                if not value:
                    continue
                # Compute fuzzy score using the same metric as utils.compare_text
                # but keep the best candidate across all strategies
                try:
                    score = fuzz.ratio(value.lower().strip(), element.lower().strip())
                except Exception:
                    score = 0
                if score > best_score:
                    best_score = score
                    best_candidate = (strategy_name, value, elem.attrib)

        # Accept best fuzzy match if score meets threshold (80)
        if best_candidate and best_score >= 80:
            strategy_name, value, attributes = best_candidate
            internal_logger.debug(
                f"Fuzzy match selected (score={best_score}) using '{strategy_name}': '{value}'"
            )
            return {
                "strategy": strategy_name,
                "locator": value,
                "attributes": attributes,
                "timestamp": time_stamp,
            }

        internal_logger.debug(
            f"No matching element found in any of the locator strategies for '{element}'."
        )
        return None

    def get_view_locator(self, strategy, locator):
        """
        Fetches the full XPath of the given element directly from the UI tree using the strategy found.
        Supports both Android and iOS attributes with prioritized attribute selection.
        """
        try:
            self.get_page_source()
            tree = self.tree
            # Construct the XPath based on the strategy and platform-specific attributes
            if strategy in [
                "text",
                "resource-id",
                "content-desc",
                "name",
                "value",
                "label",
            ]:
                # Directly use the strategy as the attribute name
                xpath_query = f"//*[@{strategy}='{locator}']"
            elif strategy == "xpath":
                # internal_logger.debug("Debug: Strategy is XPath, returning locator directly.")
                return locator
            else:
                internal_logger.debug(f"Unsupported strategy: {strategy}")
                return None

            # Run the XPath query
            elements = tree.xpath(xpath_query)
            if elements:
                # If an element is found, manually construct the full XPath of the first matching element
                element = elements[0]
                xpath_parts = []
                # Define attribute priority for Android and iOS
                android_priority = ["resource-id", "text", "content-desc"]
                ios_priority = ["name", "value", "label"]

                while element is not None:
                    tag = element.tag
                    attributes = []

                    # Choose the highest-priority attribute that exists in the element
                    for attr in android_priority + ios_priority:
                        if attr in element.attrib and element.attrib[attr]:
                            attributes.append(f"@{attr}='{element.attrib[attr]}'")
                            # break

                    # Construct the XPath part with the selected attribute
                    xpath_part = tag
                    if attributes:
                        xpath_part += "[" + " and ".join(attributes) + "]"
                        xpath_parts.insert(0, xpath_part)

                        # Stop traversal if a high-priority identifier is found
                        if (
                            "resource-id" in element.attrib
                            or "name" in element.attrib
                            or "label" in element.attrib
                        ):
                            break
                    else:
                        # If no unique attributes, add an index if siblings have the same tag
                        siblings = (
                            element.getparent().findall(tag)
                            if element.getparent() is not None
                            else []
                        )
                        index = siblings.index(element) + 1 if len(siblings) > 1 else 1
                        xpath_part += f"[{index}]"
                        xpath_parts.insert(0, xpath_part)
                    # Move to the parent element
                    element = element.getparent()

                # Combine the parts to form the final simplified XPath
                full_xpath = "//" + "/".join(xpath_parts)
                # Find the XPath that is acceptable by Appium
                final_xpath, _ = self.find_xpath(full_xpath)
                return final_xpath
            internal_logger.debug(f"No element found for '{locator}' in the UI tree.")
            return None
        except Exception as e:
            internal_logger.debug(f"Error getting view locator from tree: {e}")
            return None

    def get_locator_and_strategy_using_index(
        self, element, index, strategy=None
    ) -> dict:
        """
        Perform a linear search across all strategies (resource-id, text, content-desc, etc.) in the UI tree,
        match against the input, and index the found matches.

        Args:
            element (str): The element identifier to search for.
            index (int): zero indexing
            strategy (str): supported attributes in string, 'resource-id', 'text', 'content-desc', 'name', 'value', 'label'

        Returns:
            list: A list of dictionaries, each containing the strategy, value, and index of the match.
        """
        self.get_page_source()
        tree = self.tree

        # Collect all elements in positional order
        all_strategies = [
            "resource-id",
            "text",
            "content-desc",
            "name",
            "value",
            "label",
        ]  # Supported attributes
        all_elements = []

        strategies = [strategy] if strategy else all_strategies

        # If a specific strategy is provided, ensure it's valid
        if strategy and strategy not in strategies:
            raise ValueError(
                f"Invalid strategy '{strategy}'. Supported strategies: {strategies}"
            )

        for strategy in strategies:
            elements = tree.xpath(f"//*[@{strategy}]")
            for elem in elements:
                attr_value = elem.attrib.get(strategy, "").strip()
                bounds = elem.attrib.get("bounds", "")  # Parse bounds if available
                position = self.parse_bounds(bounds)
                all_elements.append(
                    {"strategy": strategy, "value": attr_value, "position": position}
                )

        # Perform a linear match against all elements
        # Prefer exact matches (or suffix after '/') before falling back to fuzzy compare
        exact_matches = []
        fuzzy_matches = []
        for idx, elem in enumerate(all_elements):
            val = elem.get("value", "")
            # exact full-string match
            if val == element:
                exact_matches.append(
                    {
                        "index": idx,
                        "strategy": elem["strategy"],
                        "value": val,
                        "position": elem["position"],
                    }
                )
                continue

            # exact suffix match (e.g., resource-id like 'pkg/name' -> 'name')
            if "/" in val and val.rsplit("/", 1)[-1] == element:
                exact_matches.append(
                    {
                        "index": idx,
                        "strategy": elem["strategy"],
                        "value": val,
                        "position": elem["position"],
                    }
                )
                continue

            # fallback to fuzzy/partial compare
            if utils.compare_text(val, element):
                fuzzy_matches.append(
                    {
                        "index": idx,
                        "strategy": elem["strategy"],
                        "value": val,
                        "position": elem["position"],
                    }
                )

        # If exact matches exist, prefer them; otherwise use fuzzy matches
        matches = exact_matches if exact_matches else fuzzy_matches

        # Log matches
        internal_logger.debug(
            f"Found {len(matches)} matches for '{element}': {matches}"
        )

        if index >= len(matches):
            raise IndexError(
                f"Index {index} is out of range for the matches found. Total matches: {len(matches)}."
            )

        desired_match = matches[index]
        internal_logger.debug(
            f"Found the matches for {element} and returning {index} index match: {desired_match} "
        )

        strategy = desired_match["strategy"]
        locator = desired_match["value"]

        internal_logger.debug(f"Returning strategy: {strategy}, locator: {locator}")
        return {"strategy": strategy, "locator": locator}

    def parse_bounds(self, bounds):
        """
        Parse the 'bounds' attribute to extract position information.
        Args:
            bounds (str): Bounds string in the format "[x1,y1][x2,y2]".
        Returns:
            dict: A dictionary with coordinates {x1, y1, x2, y2}.
        """
        try:
            numbers = re.findall(r"\d+", bounds)  # Extract all numbers from the string
            if len(numbers) == 4:
                x1, y1, x2, y2 = map(int, numbers)
                return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            else:
                raise ValueError(f"Unexpected bounds format: {bounds}")
        except Exception as e:
            internal_logger.debug(f"Error parsing bounds: {bounds} - {e}")
            return {"x1": 0, "y1": 0, "x2": 0, "y2": 0}

    def get_bounding_box_for_text(self, attributes):
        """
        Extract the bounding box for a text element using its attributes.

        Args:
            attributes (dict): The attributes of the element containing 'bounds'.

        Returns:
            tuple: Bounding box coordinates in the form of (top_left, bottom_right).
        """
        if attributes and "bounds" in attributes:
            # Parse the bounds attribute to get coordinates
            bounds = attributes["bounds"]
            bounds = bounds.strip("[]").split("][")
            top_left = tuple(map(int, bounds[0].split(",")))
            bottom_right = tuple(map(int, bounds[1].split(",")))

            internal_logger.debug(f"Bounding box extracted: {top_left}, {bottom_right}")
            return (top_left, bottom_right)
        else:
            internal_logger.debug(f"Bounds not available in attributes: {attributes}")

    def get_bounding_box_for_xpath(self, xpath):
        # refresh page source tree and root
        self.get_page_source()
        if not xpath:
            internal_logger.debug("Invalid Xpath, bounding box cannot be fetched.")
            return None

        # Get the element attributes based on the xpath
        attributes = self.get_element_attributes_by_xpath(xpath)
        internal_logger.debug(attributes)

        try:
            if attributes:
                # Check if bounds key exists
                if "bounds" in attributes:
                    # Parse the bounds attribute to get coordinates
                    bounds = attributes["bounds"]
                    bounds = bounds.strip("[]").split("][")
                    top_left = tuple(map(int, bounds[0].split(",")))
                    bottom_right = tuple(map(int, bounds[1].split(",")))

                    internal_logger.debug(
                        f"Bounding box for element with xpath '{xpath}': {top_left}, {bottom_right}"
                    )
                    return (top_left, bottom_right)

                # If bounds key is not available, calculate using x, y, width, and height
                elif all(k in attributes for k in ["x", "y", "width", "height"]):
                    x = int(attributes["x"])
                    y = int(attributes["y"])
                    width = int(attributes["width"])
                    height = int(attributes["height"])

                    top_left = (x, y)
                    bottom_right = (x + width, y + height)

                    internal_logger.debug(
                        f"Bounding box calculated for element with xpath '{xpath}': {top_left}, {bottom_right}"
                    )
                    return (top_left, bottom_right)

                else:
                    internal_logger.debug(
                        f"Required attributes (x, y, width, height) not found in element attributes: {attributes}"
                    )
                    return None  # Graceful exit if required keys are missing
            else:
                internal_logger.debug(
                    f"Element with xpath '{xpath}' not found or attributes unavailable."
                )
                return None  # Graceful exit if attributes are missing

        except Exception as e:
            internal_logger.debug(
                f"Error calculating bounding box for xpath '{xpath}': {str(e)}"
            )
            return None  # Graceful exit on unexpected errors

    def get_element_attributes_by_xpath(self, xpath):
        """
        Find an element by its XPath in the Appium UI tree and retrieve its attributes.

        Args:
            xpath (str): The XPath to search for in the page source.

        Returns:
            dict: Dictionary containing the element's attributes, or None if not found.
        """

        tree = self.tree

        # Find the element using the provided XPath
        try:
            elements = tree.xpath(xpath)
            if elements:
                # If an element is found, return its attributes as a dictionary
                return dict(elements[0].attrib)
            else:
                internal_logger.debug(f"No element found with XPath: {xpath}")
                return None
        except etree.XPathSyntaxError as e:
            internal_logger.debug(f"Invalid XPath syntax: {xpath} - Error: {str(e)}")

    # element extraction
    def get_interactive_elements(self, filter_config: Optional[List[str]] = None) -> List[Dict]:
        """
        Cross-platform element extraction supporting both Android and iOS.

        Args:
            filter_config: Optional list of filter types. Valid values:
                - "all": Show all elements (default when None or empty)
                - "interactive": Only interactive elements
                - "buttons": Only button elements
                - "inputs": Only input/text field elements
                - "images": Only image elements
                - "text": Only text elements
                Can be combined: ["buttons", "inputs"]
        """
        page_source, _ = self.get_page_source()
        root = etree.ElementTree(
            etree.fromstring(page_source.encode("utf-8"))
        ).getroot()
        elements = root.xpath(".//*")
        results = []

        for node in elements:
            bounds = self._extract_bounds(node)
            if not bounds:
                continue

            # Check if element should be included based on filter_config
            if not self._should_include_element(node, filter_config):
                continue

            text, used_key = self._extract_display_text(node.attrib)
            if not text:
                # If no text-like attribute, use tag name
                text, used_key = node.tag, None

            xpath = self.get_xpath(node)
            extra = self._build_extra_metadata(node.attrib, used_key, node.tag)

            results.append(
                {"text": text, "bounds": bounds, "xpath": xpath, "extra": extra}
            )

        return results

    def _extract_bounds(self, node: etree.Element) -> Optional[Dict[str, int]]:
        """
        Supports:
        - Android: bounds="[x1,y1][x2,y2]"
        - iOS (XCUI): x=".." y=".." width=".." height=".."
        Returns dict with x1,y1,x2,y2 or None if cannot parse.
        """
        attrs = node.attrib or {}

        # Android style
        bounds_str = attrs.get("bounds", "")
        if bounds_str:
            match = re.findall(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
            if match:
                x1, y1, x2, y2 = map(int, match[0])
                return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

        # iOS style (XCUIElementType*)
        # Attributes are strings; make sure they are digits/valid ints.
        x = attrs.get("x")
        y = attrs.get("y")
        w = attrs.get("width")
        h = attrs.get("height")

        def _to_int(v: Optional[str]) -> Optional[int]:
            if v is None:
                return None
            try:
                # iOS sometimes shows floats; cast safely
                return int(float(v))
            except ValueError:
                return None

        xi, yi, wi, hi = map(_to_int, (x, y, w, h))
        if None not in (xi, yi, wi, hi):
            # Guard against zero/negative sizes
            if wi > 0 and hi > 0:
                return {"x1": xi, "y1": yi, "x2": xi + wi, "y2": yi + hi}

        return None

    def _extract_display_text(self, attrs: dict) -> Tuple[Optional[str], Optional[str]]:
        """
        Cross-platform text picking order:
          - Android: text, content-desc, resource-id tail
          - iOS: name, label, value
        We'll unify and return the first non-empty.
        """
        # Normalize occasional empty strings like "" or " "
        def norm(v: Optional[str]) -> Optional[str]:
            if v is None:
                return None
            s = v.strip()
            return s if s else None

        text_candidates = [
            ("text", norm(attrs.get("text"))),                 # Android
            ("content-desc", norm(attrs.get("content-desc"))), # Android
            ("name", norm(attrs.get("name"))),                 # iOS
            ("label", norm(attrs.get("label"))),               # iOS
            ("value", norm(attrs.get("value"))),               # iOS
            (
                "resource-id",
                norm(attrs.get("resource-id", "").split("/")[-1]) if attrs.get("resource-id") else None,  # Android
            ),
        ]
        for key, val in text_candidates:
            if val:
                return val, key
        return None, None

    def _build_extra_metadata(
        self, attrs: dict, used_key: Optional[str], tag: str
    ) -> dict:
        extra = {
            k: v
            for k, v in attrs.items()
            if k != used_key and v and (isinstance(v, str) and v.lower() != "false" or not isinstance(v, str))
        }
        # Keep a few common fields explicitly
        extra["class"] = attrs.get("class")  # Android
        extra["resource-id"] = attrs.get("resource-id")  # Android
        extra["visible"] = attrs.get("visible")  # iOS
        extra["enabled"] = attrs.get("enabled")  # both
        extra["tag"] = tag  # e.g., XCUIElementTypeButton or android.widget.Button
        return extra

    def get_xpath(self, node: etree.Element) -> str:
        """
        Generate an optimal XPath for a given node using attribute-based
        uniqueness checks and semi-unique indexing, falling back to a
        hierarchical path when required. Mirrors the behavior of the
        provided getOptimalXPath logic.
        """
        # Short-circuit for safety: ensure we have a valid element
        if node is None or not hasattr(node, "tag"):
            return ""

        # Attribute sets (mobile-centric with web-friendly aliases)
        UNIQUE_ATTRIBUTES = [
            "name",
            "content-desc",
            "id",
            "resource-id",
            "accessibility-id",
        ]
        MAYBE_UNIQUE_ATTRIBUTES = ["label", "text", "value"]

        # Utility: safely escape values for XPath literals
        def lit(val: str) -> str:
            return self._escape_for_xpath_literal(val)

        # Obtain the document (tree) for this node so index comparisons work
        doc_tree = node.getroottree()

        # Evaluate XPath against this node's document and determine uniqueness
        def determine_xpath_uniqueness(xpath: str):
            try:
                matches = doc_tree.xpath(xpath)
            except (etree.XPathError, ValueError, TypeError):
                return False, None

            # If no match, not useful
            if not matches:
                return False, None
            # Multiple matches -> semi-unique, compute index of this node if present
            if len(matches) > 1:
                try:
                    idx = matches.index(node)
                except ValueError:
                    idx = 0
                return False, idx
            # Exactly one match -> unique
            return True, None

        # Build XPath from a single attribute
        def build_xpath_from_single_attribute(attr_name: str, tag_for_xpath: str):
            val = node.attrib.get(attr_name)
            if not val:
                return None
            return f"//{tag_for_xpath}[@{attr_name}={lit(val)}]"

        # Build XPath from an attribute pair
        def build_xpath_from_attribute_pair(attr_pair: tuple[str, str], tag_for_xpath: str):
            a1, a2 = attr_pair
            v1, v2 = node.attrib.get(a1), node.attrib.get(a2)
            if not v1 or not v2:
                return None
            return f"//{tag_for_xpath}[@{a1}={lit(v1)} and @{a2}={lit(v2)}]"

        # Semi-unique wrapper with explicit index (1-based)
        def build_semi_unique_xpath(xpath: str, index: int) -> str:
            return f"({xpath})[{index + 1}]"

        # Try attributes (singles or pairs) for uniqueness, keeping first semi-unique
        def try_attributes_for_unique_xpath(attrs):
            tag_for_xpath = node.tag or "*"
            is_pairs = bool(attrs and isinstance(attrs[0], tuple))
            unique_xpath = None
            semi_unique_xpath = None

            for entry in attrs:
                if is_pairs:
                    xpath = build_xpath_from_attribute_pair(entry, tag_for_xpath)
                else:
                    xpath = build_xpath_from_single_attribute(entry, tag_for_xpath)
                if not xpath:
                    continue

                is_unique, idx = determine_xpath_uniqueness(xpath)
                if is_unique:
                    unique_xpath = xpath
                    break
                if semi_unique_xpath is None and idx is not None:
                    semi_unique_xpath = build_semi_unique_xpath(xpath, idx)

            if unique_xpath:
                return unique_xpath, True
            if semi_unique_xpath:
                return semi_unique_xpath, False
            return None, None

        # Try node name alone (//tag or /tag for root)
        def try_node_name_xpath():
            tag = node.tag or "*"
            xpath = f"//{tag}"
            is_unique, _ = determine_xpath_uniqueness(xpath)
            if not is_unique:
                return None, None
            # If node has no parent, prefer absolute root path
            if node.getparent() is None:
                xpath = f"/{tag}"
            return xpath, True

        # Generate all attribute pair permutations
        def build_attribute_pairs_permutations(attributes: list[str]) -> list[tuple[str, str]]:
            pairs = []
            for i, v1 in enumerate(attributes):
                for v2 in attributes[i + 1 :]:
                    pairs.append((v1, v2))
            return pairs

        # Ordered cases to try
        def build_xpath_cases():
            all_attrs = [*UNIQUE_ATTRIBUTES, *MAYBE_UNIQUE_ATTRIBUTES]
            return [
                UNIQUE_ATTRIBUTES,
                build_attribute_pairs_permutations(all_attrs),
                MAYBE_UNIQUE_ATTRIBUTES,
                [],  # node name last
            ]

        # Try all cases; prefer fully unique, otherwise keep first semi-unique
        def try_cases_for_unique_xpath():
            semi_unique = None
            for attrs in build_xpath_cases():
                if len(attrs) == 0:
                    xpath, is_unique = try_node_name_xpath()
                else:
                    xpath, is_unique = try_attributes_for_unique_xpath(attrs)
                if is_unique:
                    return xpath
                if semi_unique is None and xpath:
                    semi_unique = xpath
            return semi_unique

        # Build hierarchical XPath (short, index only when siblings share tag)
        def build_hierarchical_xpath():
            tag = node.tag
            if not tag:
                return ""

            parent = node.getparent()
            # Build current node segment with index only if needed
            segment = f"/{tag}"
            if parent is not None:
                siblings_same_tag = [c for c in parent if c.tag == tag]
                if len(siblings_same_tag) > 1:
                    idx = siblings_same_tag.index(node) + 1
                    segment += f"[{idx}]"

            # If there is a parent, recurse to get parent's optimal path (which may be attribute-based)
            if parent is not None and hasattr(parent, "tag"):
                parent_xpath = self.get_xpath(parent)
                return f"{parent_xpath}{segment}"

            # No parent: return root absolute path
            return segment

        # 1) Try attribute-based uniqueness (singles, pairs, maybe-unique, node name)
        candidate = try_cases_for_unique_xpath()
        if candidate:
            return candidate

        # 2) Fallback to hierarchical path
        return build_hierarchical_xpath() or self._build_structural_xpath(node)

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
            if i == len(parts)-1:
                escaped_parts.append(f'"{p}"')
            else:
                escaped_parts.extend([f'"{p}"', "'\"'"])
        return 'concat(' + ', '.join(escaped_parts) + ')'

    def _build_attribute_condition(self, attr: str, val: str) -> str:
        """
        Return the appropriate condition expression for the attribute match.
        - Exact match for iOS name/label (typically normalized and unique)
        - Exact match for Android resource-id when namespaced
        - Contains match for content-like attributes (Android text/desc, iOS value)
        """
        val = val.strip()
        lit = self._escape_for_xpath_literal(val)

        # Android id exact match when namespaced
        if attr == "resource-id" and "/" in val:
            return f"@{attr}={lit}"

        # iOS: be strict for 'name'/'label'
        if attr in ("name", "label"):
            return f"@{attr}={lit}"

        # Keep contains() for content-ish attributes (Android text/desc, iOS value)
        return f"contains(@{attr}, {lit})"

    def _build_structural_xpath(self, node: etree.Element) -> str:
        """
        Fallback method to build full XPath based on element structure.
        Works for both Android and iOS trees.
        - Android: stops at hierarchy root
        - iOS: walks to XCUIElementTypeApplication root
        """
        path = []
        cur = node
        while cur is not None:
            parent = cur.getparent()
            if parent is None:
                # reached document root
                path.append(cur.tag)
                break
            siblings = [sib for sib in parent if sib.tag == cur.tag]
            index = siblings.index(cur) + 1 if len(siblings) > 1 else 1
            path.append(f"{cur.tag}[{index}]")
            cur = parent
        return "/" + "/".join(reversed(path))

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
        matches_any = False

        if "interactive" in filter_config:
            if self._is_probably_interactive(node):
                matches_any = True

        if "buttons" in filter_config:
            if self._is_button(node):
                matches_any = True

        if "inputs" in filter_config:
            if self._is_input(node):
                matches_any = True

        if "images" in filter_config:
            if self._is_image(node):
                matches_any = True

        if "text" in filter_config:
            if self._is_text(node):
                matches_any = True

        return matches_any

    def _is_button(self, node: etree.Element) -> bool:
        """Check if element is a button."""
        tag = node.tag or ""
        # Android: android.widget.Button
        # iOS: XCUIElementTypeButton
        return "Button" in tag

    def _is_input(self, node: etree.Element) -> bool:
        """Check if element is an input/text field."""
        tag = node.tag or ""
        # Android: EditText, TextView (when editable)
        # iOS: XCUIElementTypeTextField, XCUIElementTypeSecureTextField, XCUIElementTypeTextView
        input_tags = ["TextField", "EditText", "SecureTextField", "TextView"]
        return any(input_tag in tag for input_tag in input_tags)

    def _is_image(self, node: etree.Element) -> bool:
        """Check if element is an image."""
        tag = node.tag or ""
        # Android: ImageView
        # iOS: XCUIElementTypeImage
        return "Image" in tag

    def _is_text(self, node: etree.Element) -> bool:
        """Check if element is a text element (non-input)."""
        tag = node.tag or ""
        # Android: TextView (non-editable)
        # iOS: XCUIElementTypeStaticText
        # Exclude inputs
        if self._is_input(node):
            return False
        return "StaticText" in tag or ("TextView" in tag and "EditText" not in tag)

    def _is_probably_interactive(self, node: etree.Element) -> bool:
        """
        Check if element is probably interactive (clickable, enabled, etc.).
        """
        attrs = node.attrib or {}
        tag = node.tag or ""

        # Quick heuristics
        if attrs.get("clickable") == "true":
            return True
        if attrs.get("enabled") == "true" and attrs.get("visible", "true") != "false":
            if tag.startswith("XCUIElementTypeButton") or tag.startswith("android.widget.Button"):
                return True
            if "ImageView" in tag or "XCUIElementTypeImage" in tag:
                # Often icon buttons; your call whether to include
                return True
        return False
