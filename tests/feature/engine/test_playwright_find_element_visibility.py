"""Feature test: PlaywrightFindElement presence-vs-visibility, against a real browser.

Uses a deterministic local page (via page.set_content) rather than a live site, so the
off-screen target is unambiguous and the test doesn't depend on a third party's DOM layout.

The browser/page are launched via run_async (mirroring how
optics_framework/engines/drivers/playwright.py itself launches the browser) rather than a
separate asyncio.run() loop -- Playwright objects aren't safe to use across event loops, and
launching them on a different loop than the one run_async() schedules calls onto causes
locator.count()/etc. to silently fail.
"""
import pytest
from playwright.async_api import async_playwright

from optics_framework.common.async_utils import run_async
from optics_framework.engines.elementsources.playwright_find_element import PlaywrightFindElement

TARGET_TEXT = "Off-screen Marker Text"
VIEWPORT = {"width": 800, "height": 400}
HTML = f"""
<html><body>
  <div style="height: 3000px;">spacer</div>
  <p id="target">{TARGET_TEXT}</p>
</body></html>
"""


class _FakeDriver:
    """Minimal stand-in for engines/drivers/playwright.py's Playwright driver --
    PlaywrightFindElement only ever reads `.page` off it."""

    def __init__(self, page):
        self.page = page


async def _setup():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport=VIEWPORT)
    await page.set_content(HTML)
    return pw, browser, page


async def _teardown(pw, browser):
    await browser.close()
    await pw.stop()


@pytest.fixture
def source_and_page():
    pw, browser, page = run_async(_setup())
    try:
        yield PlaywrightFindElement(driver=_FakeDriver(page)), page
    finally:
        run_async(_teardown(pw, browser))


def test_target_is_genuinely_off_screen(source_and_page):
    """Sanity check on the fixture itself: the marker must actually be below the fold."""
    _, page = source_and_page
    box = run_async(page.locator("#target").bounding_box())
    assert box is not None
    assert box["y"] >= VIEWPORT["height"]


def test_assert_elements_finds_off_screen_element(source_and_page):
    """The fix: presence must not require visibility."""
    source, _ = source_and_page
    result, timestamp = source.assert_elements([TARGET_TEXT], timeout=5, rule="any")
    assert result is True
    assert timestamp is not None


def test_locate_also_succeeds_for_off_screen_element(source_and_page):
    """Documents a real Playwright limitation (not a bug in this fix): `locate()`'s
    `wait_for(state="visible")` only checks CSS (non-empty bounding box, not
    `visibility:hidden`) -- it does NOT check whether the element is scrolled into the
    current viewport. So `locate()` succeeds here too, same as `assert_elements`. If a
    real viewport-aware `assert_elements_visible` is ever added for Playwright, it must
    pair this with an explicit bounding_box() vs. viewport_size() check, the same way
    SeleniumFindElement pairs is_displayed() with a getBoundingClientRect() check --
    state="visible" alone is not sufficient, contrary to earlier assumptions.
    """
    source, _ = source_and_page
    located = source.locate(TARGET_TEXT)
    assert located is not None


# --------------------------------------------------------------------------------------
# Same behavior, against a real third-party page rather than a controlled local one.
#
# "Disclaimer" (matches the footer "Disclaimers" link) was chosen deliberately: unlike
# "References" or "About Wikipedia", it does NOT also appear in Wikipedia's left sidebar
# navigation -- so a loose text match can't accidentally grab a visible duplicate near the
# top of the page instead of the actual off-screen footer link.
# --------------------------------------------------------------------------------------

WIKI_URL = "https://en.wikipedia.org/wiki/Python_(programming_language)"
WIKI_TARGET_TEXT = "Disclaimer"


async def _setup_wikipedia():
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    page = await browser.new_page(viewport=VIEWPORT)
    await page.goto(WIKI_URL)
    return pw, browser, page


@pytest.fixture
def source_and_wikipedia_page():
    pw, browser, page = run_async(_setup_wikipedia())
    try:
        yield PlaywrightFindElement(driver=_FakeDriver(page)), page
    finally:
        run_async(_teardown(pw, browser))


def test_wikipedia_disclaimer_is_genuinely_off_screen(source_and_wikipedia_page):
    """Sanity check: confirms 'Disclaimer' isn't duplicated near the top of the page the
    way 'References'/'About Wikipedia' are (both also appear in the left nav sidebar)."""
    _, page = source_and_wikipedia_page
    locator = page.get_by_text(WIKI_TARGET_TEXT, exact=False).first
    box = run_async(locator.bounding_box())
    viewport = page.viewport_size
    assert box is not None
    assert box["y"] >= viewport["height"]


def test_assert_elements_finds_wikipedia_disclaimer_off_screen(source_and_wikipedia_page):
    """The fix, against a real page: presence must not require visibility."""
    source, _ = source_and_wikipedia_page
    result, timestamp = source.assert_elements([WIKI_TARGET_TEXT], timeout=5, rule="any")
    assert result is True
    assert timestamp is not None
