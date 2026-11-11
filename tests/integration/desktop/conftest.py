"""Desktop apps through Appium and appium-xa11y-driver, on the Host that ``OPTICS_E2E_XA11Y_URL`` names.

    OPTICS_E2E_XA11Y_URL=http://127.0.0.1:4723 OPTICS_E2E_XA11Y_PLATFORM=Linux pytest tests/integration/desktop
"""

import json
import urllib.request

import pytest

from tests.integration.desktop.support import APPIUM_URL, DESKTOPS, PLATFORM, runs


@pytest.fixture(scope="session")
def host():
    if not APPIUM_URL:
        pytest.skip("Set OPTICS_E2E_XA11Y_URL to an Appium server with appium-xa11y-driver")
    with urllib.request.urlopen(f"{APPIUM_URL}/status", timeout=3) as response:  # nosec
        json.load(response)
    return APPIUM_URL


@pytest.fixture(scope="session")
def desktop():
    return DESKTOPS[PLATFORM]


@pytest.fixture(scope="session", autouse=True)
def no_editor_of_the_user():
    """Stop before the first test when the editor already runs: the suite types into it and quits it."""
    editor = DESKTOPS[PLATFORM].editor_name
    if APPIUM_URL and runs(APPIUM_URL, editor):
        pytest.exit(f"Quit {editor} before the desktop suite: it types into {editor} and quits it.", returncode=1)
