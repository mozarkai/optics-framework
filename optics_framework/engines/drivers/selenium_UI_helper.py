from fuzzywuzzy import fuzz
from bs4 import BeautifulSoup
from lxml import html
from optics_framework.common.logging_config import internal_logger
from optics_framework.common import utils
from optics_framework.engines.drivers.selenium_driver_manager import get_selenium_driver
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoSuchElementException

class UIHelper:
    def __init__(self):
        """
        Initialize
        """
        self.driver = None

    def _get_selenium_driver(self):
        if self.driver is None:
            self.driver = get_selenium_driver()
        return self.driver

    def get_page_source(self):
        """
        Fetch the current UI tree (page source) from the Appium driver.
        """
        time_stamp = utils.get_current_time_for_events()
        driver = self._get_selenium_driver()
        page_source = driver.page_source
        # Parse using BeautifulSoup
        soup = BeautifulSoup(page_source, 'lxml')
        utils.save_page_source_html(soup.prettify(), time_stamp)
        internal_logger.debug('\n\n========== PAGE SOURCE FETCHED ==========\n')
        internal_logger.debug(f'Page source fetched at: {time_stamp}')
        internal_logger.debug('\n==========================================\n')
        return page_source

    def find_element_by_text(self, text: str, threshold: int = 80):
        """
        Finds a Selenium WebElement that approximately matches the given text using fuzzywuzzy.

        Args:
            text (str): The target visible text to match.
            threshold (int): Minimum fuzzy match score (0-100).

        Returns:
            WebElement: Closest matching element.

        Raises:
            NoSuchElementException: If no element is found above threshold.
        """
        driver = self._get_selenium_driver()
        candidates = driver.find_elements(By.XPATH, "//*[normalize-space(text()) != '']")
        best_score = 0
        best_match = None

        for el in candidates:
            try:
                el_text = el.text.strip()
                score = fuzz.ratio(text.lower(), el_text.lower())

                if score > best_score and score >= threshold:
                    best_score = score
                    best_match = el
            except Exception:
                internal_logger.debug(f"Error processing element: {el}. Skipping.")
                continue

        if best_match:
            internal_logger.debug(f"Fuzzy match found: '{best_match.text}' (score: {best_score})")
            return best_match

        internal_logger.error(f"No fuzzy text match found for: '{text}'")
        raise NoSuchElementException(f"No fuzzy match found for '{text}'")


    def find_html_element_by_text(self, text: str, index: int = 0) -> dict:
        """
        Searches the HTML page source for elements that match the given text,
        based on visible content and useful attributes.

        Args:
            text (str): Descriptive user-provided string.
            index (int): Index of the matching element to return. Defaults to 0 (first match).

        Returns:
            dict: {
                "tag": tag name,
                "attrs": tag attributes,
                "matched_value": value that matched,
                "text": tag visible text,
                "matched_by": "text" or "attribute:<name>"
            }

        Raises:
            ValueError: If no match is found or the index is out of range.
        """
        page_source = self.get_page_source()
        index = int(index) if index is not None else 0
        try:
            soup = BeautifulSoup(page_source, 'html.parser')
        except Exception:
            internal_logger.warning("Falling back to html.parser due to error in lxml parser")
            soup = BeautifulSoup(page_source, 'html.parser')

        valid_tags = ['a', 'button', 'span', 'div', 'label', 'input', 'textarea', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
        candidates = []

        for tag in soup.find_all(True):  # All tags
            if tag.name not in valid_tags:
                continue

            # 1. Match by visible text
            visible_text = tag.get_text(strip=True)
            if visible_text and utils.compare_text(text, visible_text):
                candidates.append({
                    "tag": tag.name,
                    "attrs": tag.attrs,
                    "matched_value": visible_text,
                    "text": visible_text,
                    "matched_by": "text"
                })
                continue

            # 2. Match by common attributes
            for attr in ['aria-label', 'placeholder', 'id', 'class', 'name', 'title', 'alt', 'value']:
                attr_value = tag.get(attr)
                if attr_value:
                    if isinstance(attr_value, list):
                        values = attr_value
                    else:
                        values = [attr_value]

                    for val in values:
                        if utils.compare_text(text, val):
                            candidates.append({
                                "tag": tag.name,
                                "attrs": tag.attrs,
                                "matched_value": val,
                                "text": visible_text,
                                "matched_by": f"attribute:{attr}"
                            })
                            break  # Only need one attribute match per tag
                if len(candidates) > int(index):
                    break  # No need to over-collect if we already have what we need

        if not candidates:
            internal_logger.error(f"No match found for '{text}' in HTML source")
            raise ValueError(f"No match found for '{text}'")

        if index >= len(candidates):
            internal_logger.error(f"Match index {index} out of range for text '{text}'")
            raise ValueError(f"Match index {index} out of range. Only {len(candidates)} match(es) found.")

        return candidates[index]

    def find_html_element_by_xpath(self, xpath: str, index: int = 0):
        """
        Finds an element in the raw HTML page source using XPath.

        Args:
            page_source (str): Raw HTML from Selenium/Appium driver.page_source.
            xpath (str): XPath expression to search for.
            index (int): Index of the matched element to return. Defaults to 0.

        Returns:
            lxml.html.HtmlElement: The matched HTML element.

        Raises:
            ValueError: If no match is found or index is out of range.
        """
        try:
            page_source = self.get_page_source()
            tree = html.fromstring(page_source)
            elements = tree.xpath(xpath)

            if not elements:
                raise ValueError(f"No elements found for XPath: {xpath}")

            if index >= len(elements):
                raise ValueError(f"Index {index} is out of range. Found {len(elements)} element(s).")

            return elements[index]

        except Exception as e:
            raise ValueError(f"Error while parsing XPath: {e}")


    def convert_to_selenium_element(self, match: dict):
        """
        Resolves a match dictionary from HTML-parsed results into a Selenium WebElement.

        Args:
            match (dict): A dictionary with keys like:
                        - tag
                        - attrs (id, class, etc.)
                        - text (optional)
                        - matched_value (what matched)
                        - matched_by ("text" or "attribute:<name>")

        Returns:
            WebElement: The actual Selenium WebElement.

        Raises:
            ValueError: If no element could be found in the live DOM.
        """
        driver = self.driver

        try:
            matched_by = match.get("matched_by")
            matched_value = match.get("matched_value")
            attrs = match.get("attrs", {})
            text = match.get("text", "").strip()

            # Prioritize based on match source
            if matched_by == "text" and text:
                xpath = f"//*[normalize-space(text())='{text}']"
                return driver.find_element(By.XPATH, xpath)

            elif matched_by and matched_by.startswith("attribute:"):
                attr = matched_by.split(":", 1)[1]
                if attr == "id" and "id" in attrs:
                    return driver.find_element(By.ID, attrs["id"])
                elif attr == "name" and "name" in attrs:
                    return driver.find_element(By.NAME, attrs["name"])
                elif attr == "class" and "class" in attrs:
                    class_name = attrs["class"][0] if isinstance(attrs["class"], list) else attrs["class"]
                    return driver.find_element(By.CLASS_NAME, class_name)
                else:
                    # Fallback: generic attribute-based XPath
                    xpath = "//*[@" + attr + f"='{matched_value}']"
                    return driver.find_element(By.XPATH, xpath)

            # Final fallback using text
            if text:
                fallback_xpath = f"//*[normalize-space(text())='{text}']"
                return driver.find_element(By.XPATH, fallback_xpath)

        except NoSuchElementException:
            raise ValueError(f"Element found in HTML but not found in DOM (matched_by: {matched_by}, value: {matched_value})")

        raise ValueError("Unable to resolve element to a Selenium WebElement.")
