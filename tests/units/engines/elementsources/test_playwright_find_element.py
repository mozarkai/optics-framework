from unittest.mock import MagicMock

import pytest

pytest.importorskip("playwright")

from optics_framework.engines.elementsources.playwright_find_element import (  # noqa: E402
    PlaywrightFindElement,
)

pytestmark = pytest.mark.white_box


def test_rule_all_needs_every_element_on_the_same_poll():
    source = PlaywrightFindElement(driver=MagicMock())
    polls = iter([{"A": True, "B": False}, {"A": False, "B": True}] * 3)
    seen = {}

    def check(page, element, exact):
        if element == "A":
            seen.update(next(polls))
        return seen[element]

    with pytest.raises(TimeoutError):
        source._poll_until(["A", "B"], 0.5, "all", check)


def test_rule_all_passes_when_every_element_shows_together():
    source = PlaywrightFindElement(driver=MagicMock())

    status, _ = source._poll_until(["A", "B"], 1, "all", lambda page, element, exact: True)

    assert status is True
