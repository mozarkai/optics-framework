from unittest.mock import MagicMock

import pytest

from playwright.async_api import Error as PlaywrightError

from optics_framework.engines.drivers import playwright_locator

pytestmark = pytest.mark.white_box


@pytest.mark.parametrize(
    "element, expected",
    [
        ("//input[@id='q']", ("locator", "xpath=//input[@id='q']")),
        ("xpath=//button", ("locator", "xpath=//button")),
        ("css=.cart", ("locator", ".cart")),
        ('role=button[name="Sign in"]', ("locator", 'role=button[name="Sign in"]')),
        ("data-testid=submit", ("locator", "data-testid=submit")),
    ],
)
def test_selectors_map_to_one_locator(element, expected):
    page = MagicMock()

    found = playwright_locator.candidates(page, element)

    method, arg = expected
    assert found == [getattr(page, method).return_value]
    getattr(page, method).assert_called_once_with(arg)


@pytest.mark.parametrize("element", ["#user-name", '[data-test="login"]', "p.m."])
def test_css_like_values_fall_back_to_text(element):
    page = MagicMock()

    found = playwright_locator.candidates(page, element)

    assert found[0] is page.locator.return_value
    page.locator.assert_called_once_with(element)
    page.get_by_text.assert_any_call(element, exact=True)


def test_plain_text_tries_every_exact_meaning_before_partial_ones():
    page = MagicMock()

    playwright_locator.candidates(page, "Password")

    calls = [(c[0], c[2].get("exact")) for c in page.method_calls]
    assert calls == [
        ("get_by_text", True), ("get_by_label", True), ("get_by_placeholder", True),
        ("get_by_role", True), ("get_by_role", True),
        ("get_by_text", False), ("get_by_label", False), ("get_by_placeholder", False),
        ("get_by_role", False), ("get_by_role", False),
    ]


def test_strict_matching_drops_partial_meanings():
    page = MagicMock()

    found = playwright_locator.candidates(page, "text=Products", exact=True)

    assert found == [page.get_by_text.return_value]
    page.get_by_text.assert_called_once_with("Products", exact=True)


def test_find_skips_hidden_matches(monkeypatch):
    monkeypatch.setattr(playwright_locator, "run_async", lambda coro, **_: coro)
    page = MagicMock()
    locator = page.locator.return_value
    locator.evaluate_all.return_value = [False, True]

    found = playwright_locator.find(page, "#go")

    assert found is locator.nth.return_value
    locator.nth.assert_called_once_with(1)


def test_find_gives_up_after_the_timeout(monkeypatch):
    monkeypatch.setattr(playwright_locator, "run_async", lambda coro, **_: coro)
    page = MagicMock()
    page.locator.return_value.evaluate_all.return_value = []

    assert playwright_locator.find(page, "#late", timeout_s=0.3) is None
    assert page.locator.return_value.evaluate_all.call_count > 1


def test_an_unparsable_selector_matches_nothing(monkeypatch):
    monkeypatch.setattr(playwright_locator, "run_async", lambda coro, **_: coro)
    page = MagicMock()
    page.locator.return_value.evaluate_all.side_effect = PlaywrightError("Unexpected token")
    page.get_by_text.return_value.evaluate_all.return_value = [True]

    assert playwright_locator.find(page, "p.m.") is page.get_by_text.return_value.nth.return_value


def test_a_closed_browser_is_not_mistaken_for_a_missing_element(monkeypatch):
    monkeypatch.setattr(playwright_locator, "run_async", lambda coro, **_: coro)
    page = MagicMock()
    page.locator.return_value.evaluate_all.side_effect = PlaywrightError("Target closed")

    with pytest.raises(PlaywrightError):
        playwright_locator.find(page, "#go")


def test_visibility_asks_for_elements_on_screen(monkeypatch):
    monkeypatch.setattr(playwright_locator, "run_async", lambda coro, **_: coro)
    page = MagicMock()
    page.locator.return_value.evaluate_all.return_value = [True]

    assert playwright_locator.is_visible(page, "xpath=//h1")
    assert page.locator.return_value.evaluate_all.call_args[0][1] is True
