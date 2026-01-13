import os
from typing import Any

import yaml
import pytest
from optics_framework.optics import Optics



PLAYWRIGHT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__),
    "config.yaml"
)


def load_config(path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------
# FIXTURE: Framework setup / teardown
# ---------------------------------------------------------
@pytest.fixture(scope="module")
def optics():
    config = load_config(PLAYWRIGHT_CONFIG_PATH)

    optics = Optics()
    optics.setup(config=config)

    optics.launch_app("https://www.saucedemo.com/")

    yield optics

    optics.quit()


# ---------------------------------------------------------
# TEST 1: Framework setup + launch
# ---------------------------------------------------------
def test_framework_launch_app(optics):
    optics.assert_presence('//input[@id="user-name"]')
    optics.assert_presence('//input[@id="password"]')


# ---------------------------------------------------------
# TEST 2: add_element + get_element_value
# ---------------------------------------------------------
def test_framework_add_and_get_element(optics):
    optics.add_element("username_input", '//input[@id="user-name"]')
    optics.add_element("password_input", '//input[@id="password"]')

    value = optics.get_element_value("username_input")

    assert isinstance(value, list)
    assert value == ['//input[@id="user-name"]']


# ---------------------------------------------------------
# TEST 3: enter_text_using_keyboard + clear_element_text
# ---------------------------------------------------------
def test_framework_clear_element_text(optics):
    optics.add_element("username_input", '//input[@id="user-name"]')
    # Focus input
    username = optics.get_element_value("username_input")
    optics.press_element(username)
    # Enter text INTO element
    optics.enter_text_using_keyboard("standard_user")
    optics.sleep("2")
    # Clear input
    optics.clear_element_text(username)
    optics.sleep("2")
    # Verify cleared
    value = optics.get_text(username)
    assert value is None or value == ""



# ---------------------------------------------------------
# TEST 4: Login flow (press + enter + keycode)
# ---------------------------------------------------------
def test_framework_login_flow(optics):
    optics.press_element('//input[@id="user-name"]')
    optics.enter_text_using_keyboard("standard_user")
    optics.press_element('//input[@id="password"]')
    optics.enter_text_using_keyboard("secret_sauce")
    optics.press_keycode("Enter")
    optics.sleep("2")
    optics.assert_presence('//span[text()="Products"]')


# ---------------------------------------------------------
# TEST 5: validate_element + validate_screen
# ---------------------------------------------------------
def test_framework_validation_methods(optics):
    optics.press_element('//input[@id="user-name"]')
    optics.enter_text_using_keyboard("standard_user")
    optics.press_element('//input[@id="password"]')
    optics.enter_text_using_keyboard("secret_sauce")
    optics.press_keycode("Enter")
    optics.sleep("2")
    optics.validate_element('//span[text()="Products"]')
    optics.validate_screen(
        [
            '//div[@class="inventory_list"]',
            '//a[@class="shopping_cart_link"]'
        ]
    )
    page_source = optics.capture_pagesource()
    assert "inventory_list" in page_source and "shopping_cart_link" in page_source

# ---------------------------------------------------------
# TEST 6: get_text
# ---------------------------------------------------------
def test_framework_get_text(optics):
    optics.press_element('//input[@id="user-name"]')
    optics.enter_text_using_keyboard("standard_user")

    optics.press_element('//input[@id="password"]')
    optics.enter_text_using_keyboard("secret_sauce")

    optics.press_keycode("Enter")
    optics.sleep("2")
    optics.add_element("page_title", '//span[@class="title"]')
    title = optics.get_text('//span[@class="title"]')
    assert title == "Products"


# ---------------------------------------------------------
# TEST 7: press_by_percentage
# ---------------------------------------------------------
def test_framework_press_by_percentage(optics):
    # Click somewhere in the viewport (non-destructive)
    optics.press_by_percentage("50", "50")
    optics.sleep("1")


# ---------------------------------------------------------
# TEST 8: scroll + scroll_until_element_appears
# ---------------------------------------------------------
def test_framework_scroll_methods(optics):
    optics.scroll("down")
    optics.sleep("1")
    optics.scroll_until_element_appears(
        '//button[contains(@id,"add-to-cart")]',
        timeout="10"
    )


# ---------------------------------------------------------
# TEST 9: get_interactive_elements
# ---------------------------------------------------------
def test_framework_get_interactive_elements(optics):
    elements = optics.get_interactive_elements(["buttons"])
    assert isinstance(elements, list)
    assert len(elements) > 0


# ---------------------------------------------------------
# TEST 10: capture screenshot + page source
# ---------------------------------------------------------
def test_framework_capture_artifacts(optics):
    screenshot = optics.capture_screenshot()
    assert screenshot is not None

    page_source = optics.capture_pagesource()
    assert "<html" in page_source.lower()


# ---------------------------------------------------------
# TEST 11: App metadata
# ---------------------------------------------------------
def test_framework_get_app_version(optics):
    version = optics.get_app_version()
    # Playwright web apps often return None; just ensure no crash
    assert version is None or isinstance(version, str)
