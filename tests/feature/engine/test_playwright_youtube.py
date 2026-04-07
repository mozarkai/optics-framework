import os
import yaml
import pytest

from optics_framework.optics import Optics


PLAYWRIGHT_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../../optics_framework/samples/playwright/config.yaml"
)


def load_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def optics_instance():
    """
    Fixture to setup and teardown Optics with Playwright driver
    """
    config = load_config(PLAYWRIGHT_CONFIG_PATH)
    optics = Optics()
    optics.setup(config=config)
    yield optics
    optics.quit()


def test_youtube_search_and_play(optics_instance):
    optics = optics_instance

    # ---------------------------------------------------------
    # Step 1: Launch YouTube
    # ---------------------------------------------------------
    optics.launch_app("https://www.youtube.com")

    # ---------------------------------------------------------
    # Step 2: Register elements
    # ---------------------------------------------------------
    optics.add_element("search_box", 'input[name="search_query"]')
    optics.add_element("video_title", 'text=Wild Stone Edge')

    # ---------------------------------------------------------
    # Step 3: Assert search box
    # ---------------------------------------------------------
    optics.assert_presence('input[name="search_query"]')

    # ---------------------------------------------------------
    # Step 4: Search
    # ---------------------------------------------------------
    optics.press_element('input[name="search_query"]')
    optics.enter_text_using_keyboard(
        "Wild Stone Edge Perfume Revie"
    )
    optics.press_keycode("Enter")

    # ---------------------------------------------------------
    # Step 5: Scroll until video appears
    # ---------------------------------------------------------
    print("========== PAGE SOURCE (XML TREE) ==========")
    optics.capture_screenshot()
    version = optics.get_app_version()
    print(version)
    optics.scroll_until_element_appears("Wild Stone")

    optics.sleep("10")

if __name__ == "__main__":
    import pytest
    pytest.main([
        __file__,
        "-v",
        "-s",
        "--log-cli-level=DEBUG"
    ])
