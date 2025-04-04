import re
from fuzzywuzzy import fuzz
from lxml import etree
from optics_framework.common.logging_config import logger
from optics_framework.common import utils
from optics_framework.engines.drivers.appium_driver_manager import get_appium_driver

class UIHelper:
    def __init__(self):
        """
        Initialize
        """
        self.driver = None
        self.tree = None
        self.root = None
        self.prev_hash = None
        self.prev_hash = None

    def _get_appium_driver(self):
        if self.driver is None:
            self.driver = get_appium_driver()
        return self.driver

    def get_page_source(self):
        """
        Fetch the current UI tree (page source) from the Appium driver.
        """
        time_stamp = utils.get_current_time_for_events()
        driver = self._get_appium_driver()
        page_source = driver.page_source

        self.tree = etree.ElementTree(etree.fromstring(page_source.encode('utf-8')))
        self.root = self.tree.getroot()

        logger.debug('\n\n========== PAGE SOURCE FETCHED ==========\n')
        logger.debug(f'Page source fetched at: {time_stamp}')
        logger.debug('\n==========================================\n')
        utils.save_page_source(page_source, time_stamp)
        return page_source, time_stamp

    # fetching page source and handling UI tree
    def get_distinct_page_source(self):
        """
        Fetch the current UI tree (page source) from the Appium driver continuously.
        Update instance's root and tree attributes when there's a change in page UI.
        """
        time_stamp = utils.get_current_time_for_events()
        driver = self._get_appium_driver()
        page_source = driver.page_source
        # Compute hash
        new_hash = utils.compute_hash(page_source)

        # Compare with previous hash
        if self.prev_hash == new_hash:
            logger.debug("\nPage source unchanged. Skipping further processing.\n")
            return None, time_stamp

        # Update previous hash
        self.prev_hash = new_hash

        # Parse the page source as an XML tree
        self.tree = etree.ElementTree(etree.fromstring(page_source.encode('utf-8')))
        self.root = self.tree.getroot()

        logger.debug('\n\n========== PAGE SOURCE FETCHED ==========\n')
        logger.debug(f'Page source fetched at: {time_stamp}')
        logger.debug('\n==========================================\n')
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
            strategy = locators['strategy']
            locator = locators['locator']
            xpath = self.get_view_locator(strategy=strategy, locator=locator)
            return xpath
        return None


    def find_xpath(self, xpath):
        """
        Process the given XPath and return the exact path from the UI tree after applying various matching strategies.
        """
        logger.debug(f'Finding Xpath {xpath}...')
        _ , time_stamp = self.get_page_source() # Fetch UI tree when processing the XPath
        try:
            # 1. Exact Match
            try:
                # logger.debug(f'Finding Xpath using exact match')
                found_xpath = self.find_exact(xpath)

                if found_xpath:
                    logger.debug('Xpath found using exact match')
                    return found_xpath, time_stamp
            except Exception as e:
                logger.error(
                    f"Error in exact match for XPath '{xpath}': {str(e)}")

            # 2. Relative Match
            try:
                found_xpath = self.find_relative(xpath)
                if found_xpath:
                    logger.debug('Xpath found using relative match')
                    return found_xpath, time_stamp
            except Exception as e:
                logger.error(
                    f"Error in relative match for XPath '{xpath}': {str(e)}")

            # 3. Partial Match
            try:
                found_xpath = self.find_partial(xpath)
                if found_xpath:
                    logger.debug('Xpath found using partial match')
                    return found_xpath, time_stamp
            except Exception as e:
                logger.error(
                    f"Error in partial match for XPath '{xpath}': {str(e)}")

            # 4. Attribute Match with Fuzzy Prefix and Suffix Handling
            try:
                found_xpath = self.find_attribute_match(xpath)
                if found_xpath:
                    logger.debug('Xpath found using attribute match')
                    return found_xpath, time_stamp
            except Exception as e:
                logger.error(
                    f"Error in attribute match for XPath '{xpath}': {str(e)}")

            # If no match is found
            logger.error(f"No match found for XPath '{xpath}' after applying all strategies.")
            return None, None
        except Exception as e:
            logger.error(
                f"Unexpected error in find_xpath for XPath '{xpath}': {str(e)}")
            return None, None

    def find_exact(self, xpath):
        """Attempts an exact match for the given XPath."""
        try:
            elements = self.root.xpath(xpath)
            if elements:
                logger.debug(f"Exact XPath: {xpath}")
                return xpath
        except Exception as e:
            logger.debug(f"Exact match error: {e}")
        return None

    def find_relative(self, xpath):
        """Attempts to match a simplified, relative XPath."""
        relative_xpath = self.make_relative(xpath)
        logger.debug(f"Relative XPath: {relative_xpath}")
        try:
            elements = self.root.xpath(relative_xpath)
            if elements:
                logger.debug(f"Relative match found, element type: {type(elements[0])}")
                # Use ElementTree.getpath()
                return self.simplify_xpath(elements[0])
        except Exception as e:
            logger.debug(f"Relative match error: {e}")
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
                    filtered_terms = [term for term in terms if term.lower() not in stop_words]
                    if filtered_terms:
                        # Construct conditions using `contains()` for each filtered term
                        conditions = ' or '.join(
                            [f'contains(@{attribute}, "{term}")' for term in filtered_terms])
                        # Replace the exact match in the XPath with the partial match conditions
                        xpath = xpath.replace(f'@{attribute}="{attr_value}"', conditions)

        return xpath

    def find_partial(self, xpath):
        """Attempts partial matching using `contains()` in XPath."""
        partial_xpath = self.make_partial_match(xpath)
        logger.debug(f"Partial XPath: {partial_xpath}")
        try:
            elements = self.root.xpath(partial_xpath)
            if elements:
                logger.debug(f"Partial match found, element type: {type(elements[0])}")
                # Use ElementTree.getpath()
                return self.simplify_xpath(elements[0])
        except Exception as e:
            logger.debug(f"Partial match error: {e}")
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
        if not hasattr(self, 'root') or self.root is None:
            logger.error("Root element is not initialized.")
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
                    logger.debug(f"Exact match found for {attr}: {elem_value}")
                    return self.simplify_xpath(element)

                # Fuzzy match logic
                if self.fuzzy_match_prefix(input_prefix, elem_prefix):
                    if input_suffix == elem_suffix:
                        logger.debug(
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
            logger.debug(f"Fuzzy match found with score {best_fuzzy_score}, element: {best_match}")
            return self.simplify_xpath(best_match)

        # No match found
        logger.debug("No match found using attribute matching.")
        return None

    def split_element(self, element):
        """Splits an element into prefix and suffix."""
        return element.rsplit(":", 1) if ":" in element else (element, "")

    def extract_attribute(self, xpath, attribute):
        """Extracts the value of a given attribute from the XPath."""
        marker = f'@{attribute}='
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
        if attributes['class']:
            xpath_parts.append(f"//{attributes['class']}")

        # Add resource-id if available
        if attributes['resource-id']:
            xpath_parts.append(
                f"[@resource-id=\'{attributes['resource-id']}\']")

        # Optionally add content-desc or text if available
        if attributes['content-desc']:
            xpath_parts.append(
                f"[contains(@content-desc, \'{attributes['content-desc']}\')]")
        elif attributes['text']:
            xpath_parts.append(f"[contains(@text, \'{attributes['text']}\')]")

        # Combine all parts into a simplified XPath
        simplified_xpath = "".join(xpath_parts)

        logger.debug(f"Simplified XPath: {simplified_xpath}")
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
            "resource-id": element.attrib.get('resource-id', ''),  # Android
            "content-desc": element.attrib.get('content-desc', ''),  # Android
            "text": element.attrib.get('text', ''),  # Android
            "name": element.attrib.get('name', ''),  # iOS
            "value": element.attrib.get('value', ''),  # iOS
            "label": element.attrib.get('label', ''),  # iOS
            "class": element.tag  # Widget class (e.g., android.widget.Button or XCUIElementTypeButton)
        }

        # Remove empty attributes for cleaner output
        attributes = {k: v for k, v in attributes.items() if v}
        return attributes


    def get_locator_and_strategy(self, element):
        """
        Determines the best strategy and locator for the given element identifier.
        """
        _ , time_stamp = self.get_page_source()
        tree = self.tree

        strategies = [
            ("text", "//*[@text]", "text"),
            ("resource-id", "//*[@resource-id]", "resource-id"),
            ("content-desc", "//*[@content-desc]", "content-desc"),
            ("name", "//*[@name]", "name"),
            ("value", "//*[@value]", "value"),
            ("label", "//*[@label]", "label"),
        ]

        for strategy_name, xpath_query, attrib in strategies:
            elements = self.tree.xpath(xpath_query)
            for elem in elements:
                value = elem.attrib.get(attrib, '').strip()
                if value and value == element:
                    logger.debug('Exact match found.')
                    logger.debug(f"Match found using '{strategy_name}' strategy: '{value}'")
                    attributes = elem.attrib
                    return {"strategy": strategy_name, "locator": value, "attributes": attributes, "timestamp": time_stamp}
                elif value and "/" in value:
                    _ , suffix = value.rsplit("/", 1)  # Split at the last "/"
                    if element == suffix:
                        logger.debug('Exact match found.')
                        logger.debug(f"Match found using '{strategy_name}' strategy: '{value}'")
                        attributes = elem.attrib
                        return {"strategy": strategy_name, "locator": value, "attributes": attributes, "timestamp": time_stamp}

        for strategy_name, xpath_query, attrib in strategies:
            elements = tree.xpath(xpath_query)
            for elem in elements:
                value = elem.attrib.get(attrib, '').strip()
                if value and utils.compare_text(value, element):
                    logger.debug(f"Match found using '{strategy_name}' strategy: '{value}'")
                    attributes = elem.attrib
                    return {"strategy": strategy_name, "locator": value, "attributes": attributes, "timestamp": time_stamp}

        logger.debug(f"No matching element found in any of the locator strategies for '{element}'.")
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
            if strategy in ['text', 'resource-id', 'content-desc', 'name', 'value', 'label']:
                # Directly use the strategy as the attribute name
                xpath_query = f"//*[@{strategy}='{locator}']"
            elif strategy == 'xpath':
                # logger.debug("Debug: Strategy is XPath, returning locator directly.")
                return locator
            else:
                logger.debug(f"Unsupported strategy: {strategy}")
                return None

            # Run the XPath query
            elements = tree.xpath(xpath_query)
            if elements:
                # If an element is found, manually construct the full XPath of the first matching element
                element = elements[0]
                xpath_parts = []
                # Define attribute priority for Android and iOS
                android_priority = ['resource-id', 'text', 'content-desc']
                ios_priority = ['name', 'value', 'label']

                while element is not None:
                    tag = element.tag
                    attributes = []

                    # Choose the highest-priority attribute that exists in the element
                    for attr in android_priority + ios_priority:
                        if attr in element.attrib and element.attrib[attr]:
                            attributes.append(
                                f"@{attr}='{element.attrib[attr]}'")
                            # break

                    # Construct the XPath part with the selected attribute
                    xpath_part = tag
                    if attributes:
                        xpath_part += "[" + " and ".join(attributes) + "]"
                        xpath_parts.insert(0, xpath_part)

                        # Stop traversal if a high-priority identifier is found
                        if 'resource-id' in element.attrib or 'name' in element.attrib or 'label' in element.attrib:
                            break
                    else:
                        # If no unique attributes, add an index if siblings have the same tag
                        siblings = element.getparent().findall(
                            tag) if element.getparent() is not None else []
                        index = siblings.index(
                            element) + 1 if len(siblings) > 1 else 1
                        xpath_part += f"[{index}]"
                        xpath_parts.insert(0, xpath_part)
                    # Move to the parent element
                    element = element.getparent()

                # Combine the parts to form the final simplified XPath
                full_xpath = '//' + '/'.join(xpath_parts)
                # Find the XPath that is acceptable by Appium
                final_xpath, _ = self.find_xpath(full_xpath)
                return final_xpath
            logger.debug(f"No element found for '{locator}' in the UI tree.")
            return None
        except Exception as e:
            logger.debug(f"Error getting view locator from tree: {e}")
            return None

    def get_locator_and_strategy_using_index(self, element, index, strategy=None) -> dict:
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
        all_strategies = ['resource-id', 'text', 'content-desc', 'name', 'value', 'label']  # Supported attributes
        all_elements = []

        strategies = [strategy] if strategy else all_strategies

        # If a specific strategy is provided, ensure it's valid
        if strategy and strategy not in strategies:
            raise ValueError(f"Invalid strategy '{strategy}'. Supported strategies: {strategies}")

        for strategy in strategies:
            elements = tree.xpath(f"//*[@{strategy}]")
            for elem in elements:
                attr_value = elem.attrib.get(strategy, '').strip()
                bounds = elem.attrib.get('bounds', '')  # Parse bounds if available
                position = self.parse_bounds(bounds)
                all_elements.append({
                    "strategy": strategy,
                    "value": attr_value,
                    "position": position
                })

        # Perform a linear match against all elements
        matches = []
        for idx, elem in enumerate(all_elements):
            if utils.compare_text(elem["value"], element):
                matches.append({
                    "index": idx,
                    "strategy": elem["strategy"],
                    "value": elem["value"],
                    "position": elem["position"]
                })

        # Log matches
        logger.debug(f"Found {len(matches)} matches for '{element}': {matches}")

        if index >= len(matches):
            raise   IndexError(f"Index {index} is out of range for the matches found. Total matches: {len(matches)}.")

        desired_match = matches[index]
        logger.debug(f'Found the matches for {element} and returning {index} index match: {desired_match} ')

        strategy = desired_match["strategy"]
        locator = desired_match["value"]

        logger.debug(f"Returning strategy: {strategy}, locator: {locator}")
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
            numbers = re.findall(r'\d+', bounds)  # Extract all numbers from the string
            if len(numbers) == 4:
                x1, y1, x2, y2 = map(int, numbers)
                return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
            else:
                raise ValueError(f"Unexpected bounds format: {bounds}")
        except Exception as e:
            logger.debug(f"Error parsing bounds: {bounds} - {e}")
            return {"x1": 0, "y1": 0, "x2": 0, "y2": 0}

    def get_bounding_box_for_text(self,attributes):
        """
        Extract the bounding box for a text element using its attributes.

        Args:
            attributes (dict): The attributes of the element containing 'bounds'.

        Returns:
            tuple: Bounding box coordinates in the form of (top_left, bottom_right).
        """
        if attributes and 'bounds' in attributes:
            # Parse the bounds attribute to get coordinates
            bounds = attributes['bounds']
            bounds = bounds.strip('[]').split('][')
            top_left = tuple(map(int, bounds[0].split(',')))
            bottom_right = tuple(map(int, bounds[1].split(',')))

            logger.debug(f"Bounding box extracted: {top_left}, {bottom_right}")
            return (top_left, bottom_right)
        else:
            logger.debug(f"Bounds not available in attributes: {attributes}")

    def get_bounding_box_for_xpath(self,xpath):
        # refresh page source tree and root
        self.get_page_source()
        if not xpath:
            logger.debug("Invalid Xpath, bounding box cannot be fetched.")
            return None

        # Get the element attributes based on the xpath
        attributes = self.get_element_attributes_by_xpath(xpath)
        logger.debug(attributes)

        try:
            if attributes:
                # Check if bounds key exists
                if 'bounds' in attributes:
                    # Parse the bounds attribute to get coordinates
                    bounds = attributes['bounds']
                    bounds = bounds.strip('[]').split('][')
                    top_left = tuple(map(int, bounds[0].split(',')))
                    bottom_right = tuple(map(int, bounds[1].split(',')))

                    logger.debug(f"Bounding box for element with xpath '{xpath}': {top_left}, {bottom_right}")
                    return (top_left, bottom_right)

                # If bounds key is not available, calculate using x, y, width, and height
                elif all(k in attributes for k in ['x', 'y', 'width', 'height']):
                    x = int(attributes['x'])
                    y = int(attributes['y'])
                    width = int(attributes['width'])
                    height = int(attributes['height'])

                    top_left = (x, y)
                    bottom_right = (x + width, y + height)

                    logger.debug(f"Bounding box calculated for element with xpath '{xpath}': {top_left}, {bottom_right}")
                    return (top_left, bottom_right)

                else:
                    logger.debug(f"Required attributes (x, y, width, height) not found in element attributes: {attributes}")
                    return None  # Graceful exit if required keys are missing
            else:
                logger.debug(f"Element with xpath '{xpath}' not found or attributes unavailable.")
                return None  # Graceful exit if attributes are missing

        except Exception as e:
            logger.debug(f"Error calculating bounding box for xpath '{xpath}': {str(e)}")
            return None  # Graceful exit on unexpected errors

    def get_element_attributes_by_xpath(self,xpath):
        """
        Find an element by its XPath in the Appium UI tree and retrieve its attributes.

        Args:
            xpath (str): The XPath to search for in the page source.

        Returns:
            dict: Dictionary containing the element's attributes, or None if not found.
        """

        tree = self.tree

        #Find the element using the provided XPath
        try:
            elements = tree.xpath(xpath)
            if elements:
                # If an element is found, return its attributes as a dictionary
                return dict(elements[0].attrib)
            else:
                logger.debug(f"No element found with XPath: {xpath}")
                return None
        except etree.XPathSyntaxError as e:
            logger.debug(f"Invalid XPath syntax: {xpath} - Error: {str(e)}")
