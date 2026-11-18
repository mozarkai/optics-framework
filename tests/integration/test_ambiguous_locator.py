"""A failed check on a locator that matches several elements says so: it read only the first."""
import pytest

pytest.importorskip("playwright")

from optics_framework import Optics  # noqa: E402
from optics_framework.common.error import Code, OpticsError  # noqa: E402

pytestmark = pytest.mark.black_box

PAGE = "<html><body><p>7+5</p><p>12</p><input type='checkbox' checked><input type='checkbox'></body></html>"


@pytest.fixture
def page(tmp_path):
    (tmp_path / "calc.html").write_text(PAGE, encoding="utf-8")
    optics = Optics()
    optics.setup(config={
        "driver_sources": [{"playwright": {"enabled": True, "capabilities": {"headless": True}}}],
        "elements_sources": [{"playwright_find_element": {"enabled": True}}],
        "execution_output_path": str(tmp_path / "out"),
    })
    try:
        optics.launch_app((tmp_path / "calc.html").as_uri())
    except Exception as e:
        optics.quit()
        if "Executable doesn't exist" not in str(e):
            raise
        pytest.skip(f"Chromium is not installed for Playwright: {e}")
    try:
        yield optics
    finally:
        optics.quit()


def test_a_wrong_text_names_how_many_elements_the_locator_matched(page):
    with pytest.raises(OpticsError) as failure:
        page.assert_text("//p", "12", "1")
    assert failure.value.code == Code.E0401
    assert "shows '7+5'" in failure.value.message
    assert "matches 2 elements" in failure.value.message


def test_a_wrong_state_names_how_many_elements_the_locator_matched(page):
    with pytest.raises(OpticsError) as failure:
        page.is_element("//input", "unchecked", "1")
    assert "matches 2 elements" in failure.value.message


def test_a_locator_with_one_match_says_nothing_about_a_count(page):
    with pytest.raises(OpticsError) as failure:
        page.assert_text("(//p)[2]", "13", "1")
    assert failure.value.code == Code.E0401
    assert "shows '12' but expected '13'" in failure.value.message
    assert "matches" not in failure.value.message
