import pytest

pytest.importorskip("playwright")

from optics_framework.engines.drivers.playwright import Playwright  # noqa: E402
from optics_framework.engines.elementsources import playwright_find_element  # noqa: E402
from optics_framework.engines.elementsources.playwright_find_element import PlaywrightFindElement  # noqa: E402


class ChangingPage:
    """A page that shows one set of CSS ids per poll; the source's sleep between polls brings the next set."""

    def __init__(self, *screens):
        self.screens, self.now, self.slept = list(screens), 0.0, []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds
        self.screens.append(self.screens.pop(0))

    def locator(self, selector):
        page = self

        class Matches:
            async def count(self):
                return int(selector.lstrip("#") in page.screens[0])

            def nth(self, _index):
                return self

        return Matches()


@pytest.fixture
def source(monkeypatch):
    def on(page):
        monkeypatch.setattr(playwright_find_element, "time", page)
        driver = Playwright({})
        driver.page = page
        return PlaywrightFindElement(driver=driver)
    return on


def test_rule_all_needs_every_element_on_the_same_poll(source):
    page = ChangingPage({"a"}, {"b"})
    find = source(page)
    with pytest.raises(TimeoutError):
        find.assert_elements(["css=#a", "css=#b"], timeout=1, rule="all")
    assert page.slept == [0.3] * 4


def test_rule_all_passes_when_every_element_shows_together(source):
    page = ChangingPage({"a"}, {"a", "b"})
    status, _ = source(page).assert_elements(["css=#a", "css=#b"], timeout=1, rule="all")
    assert (status, page.slept) == (True, [0.3])
