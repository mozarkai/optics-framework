from fuzzywuzzy import fuzz
from bs4 import BeautifulSoup
from lxml import html
from typing import Optional, Tuple, Any
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
        time_stamp = utils.get_timestamp()
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
        soup = self._get_html_soup()
        candidates = self._collect_matching_tags(soup, text)

        if not candidates:
            internal_logger.error(f"No match found for '{text}' in HTML source")
            raise ValueError(f"No match found for '{text}'")

        if index >= len(candidates):
            internal_logger.error(f"Match index {index} out of range for text '{text}'")
            raise ValueError(f"Match index {index} out of range. Only {len(candidates)} match(es) found.")

        return candidates[index]


    def _get_html_soup(self) -> BeautifulSoup:
        """Parses and returns the page source as a BeautifulSoup object."""
        page_source = self.get_page_source()
        try:
            return BeautifulSoup(page_source, 'lxml')
        except Exception:
            internal_logger.warning("Falling back to html.parser due to error in lxml parser")
            return BeautifulSoup(page_source, 'html.parser')


    def _collect_matching_tags(self, soup: BeautifulSoup, target_text: str) -> list:
        """Collects tags that match the given text in either visible content or common attributes."""
        valid_tags = ['a', 'button', 'span', 'div', 'label', 'input', 'textarea', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
        candidates = []

        for tag in soup.find_all(True):
            if tag.name not in valid_tags:
                continue

            if self._matches_visible_text(tag, target_text):
                candidates.append(self._build_match_result(tag, tag.get_text(strip=True), "text"))
                continue

            matched_attr = self._match_tag_attributes(tag, target_text)
            if matched_attr:
                matched_value, attr_name = matched_attr
                candidates.append(self._build_match_result(tag, matched_value, f"attribute:{attr_name}"))

        return candidates


    def _matches_visible_text(self, tag, target_text: str) -> bool:
        visible_text = tag.get_text(strip=True)
        return visible_text and utils.compare_text(target_text, visible_text)


    def _match_tag_attributes(self, tag, target_text: str) -> Optional[Tuple[str, str]]:
        """Returns (matched_value, attribute_name) if match is found, else None."""
        for attr in ['aria-label', 'placeholder', 'id', 'class', 'name', 'title', 'alt', 'value']:
            attr_value = tag.get(attr)
            if not attr_value:
                continue

            values = attr_value if isinstance(attr_value, list) else [attr_value]
            for val in values:
                if utils.compare_text(target_text, val):
                    return val, attr
        return None


    def _build_match_result(self, tag, matched_value: str, matched_by: str) -> dict:
        return {
            "tag": tag.name,
            "attrs": tag.attrs,
            "matched_value": matched_value,
            "text": tag.get_text(strip=True),
            "matched_by": matched_by
        }


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
        matched_by = match.get("matched_by")
        matched_value = match.get("matched_value")
        attrs = match.get("attrs", {})
        text = match.get("text", "").strip()

        try:
            if matched_by == "text" and text:
                return self._find_element_by_text(text, driver)

            if matched_by and matched_by.startswith("attribute:"):
                attr = matched_by.split(":", 1)[1]
                return self._find_element_by_attribute(attr, attrs, matched_value, driver)

            if text:  # Fallback
                return self._find_element_by_text(text, driver)

        except NoSuchElementException:
            raise ValueError(
                f"Element found in HTML but not found in DOM (matched_by: {matched_by}, value: {matched_value})"
            )

        raise ValueError("Unable to resolve element to a Selenium WebElement.")


    def _find_element_by_text(self, text: str, driver) -> Any:
        xpath = f"//*[normalize-space(text())='{text}']"
        return driver.find_element(By.XPATH, xpath)


    def _find_element_by_attribute(self, attr: str, attrs: dict, matched_value: str, driver) -> Any:
        if attr == "id" and "id" in attrs:
            return driver.find_element(By.ID, attrs["id"])
        if attr == "name" and "name" in attrs:
            return driver.find_element(By.NAME, attrs["name"])
        if attr == "class" and "class" in attrs:
            class_name = attrs["class"][0] if isinstance(attrs["class"], list) else attrs["class"]
            return driver.find_element(By.CLASS_NAME, class_name)

        # Fallback to generic attribute-based XPath
        xpath = "//*[@" + attr + f"='{matched_value}']"
        return driver.find_element(By.XPATH, xpath)
