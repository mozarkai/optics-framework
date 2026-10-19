import pytest

from optics_framework.common.utils import is_dom_only_xpath


@pytest.mark.parametrize(
    ("xpath", "dom_only"),
    [
        ('//input[@name="email"]', True),
        ("//button", True),
        ("xpath=//div", True),
        ("(//input)[2]", True),
        ("//li/ancestor::ul", True),
        ("//div/text()", True),
        ("//input/@value", True),
        ("//a[contains(@href,'android.com')]", True),
        ("//input|//button", True),
        ("//a[@title='a|b']", True),
        ("//a[@href='//x.com/a|b']", True),
        ("//*", False),
        ("//*[@id='x']", False),
        ("//*[local-name()='svg']", False),
        ("//*[not(self::div)]", False),
        ("id('x')", False),
        ("//INPUT", False),
        ("//node()", False),
        ("//node[@desc='x']", False),
        ("/hierarchy/android.widget.FrameLayout", False),
        ("//AppiumAUT//XCUIElementTypeButton", False),
        ("//android.widget.Button", False),
        ("//input|//android.widget.EditText", False),
        ("//input|//*[@name='x']", False),
        ("//input[@text='Email']", False),
        ("//my-widget", False),
    ],
)
def test_is_dom_only_xpath(xpath, dom_only):
    assert is_dom_only_xpath(xpath) is dom_only
