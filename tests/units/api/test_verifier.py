"""Verifier on a fake phone: the real strategies over a fake driver, element source and OCR engine."""
import pytest

from optics_framework.common.error import Code, OpticsError
from tests.units.fakes import Node, Phone, phone_keywords

SUBMIT = (10, 20)


@pytest.fixture
def verifier(tmp_path):
    def build(*nodes, ocr=False, images=False):
        return phone_keywords(Phone(nodes), tmp_path, ocr=ocr, images=images).verifier
    return build


def expect_failure(check, code, message):
    with pytest.raises(OpticsError) as failure:
        check()
    assert (failure.value.code, failure.value.message) == (code, message)


class TestAssertVisibility:
    def test_an_element_not_shown(self, verifier):
        expect_failure(lambda: verifier().assert_visibility("Option 47", "0"), Code.E0201, "'Option 47' is not on the screen.")

    @pytest.mark.parametrize("check", ["assert_visibility", "assert_presence"])
    @pytest.mark.parametrize("fail", [False, "false", " FALSE "])
    def test_an_element_not_shown_with_fail_off_returns_false(self, verifier, check, fail):
        assert getattr(verifier(), check)("Option 47", "0", fail=fail) is False

    @pytest.mark.parametrize("check", ["assert_visibility", "assert_presence"])
    @pytest.mark.parametrize("elements", ["Option 47|button.png", "Submit|missing.png"])
    def test_any_of_two_kinds_passes_on_the_kind_shown(self, verifier, check, elements):
        shown = verifier(Node("Submit", SUBMIT), Node("", (50, 60), image="button.png"), images=True)
        assert getattr(shown, check)(elements, "0") is True


class TestIsElement:
    @pytest.mark.parametrize("node", [
        pytest.param(Node("Submit", SUBMIT), id="an element"),
        pytest.param(Node("Submit", SUBMIT, web=True), id="a web element"),
    ])
    @pytest.mark.parametrize(("enabled", "state", "message"), [
        pytest.param(True, "enabled", None, id="enabled"),
        pytest.param(False, "disabled", None, id="disabled"),
        pytest.param(False, "enabled", "Element 'Submit' is disabled but expected enabled.", id="disabled, expected enabled"),
        pytest.param(True, "disabled", "Element 'Submit' is enabled but expected disabled.", id="enabled, expected disabled"),
    ])
    def test_state(self, verifier, node, enabled, state, message):
        node.enabled = enabled
        check = verifier(node)
        if message is None:
            check.is_element("Submit", state, 0)
        else:
            expect_failure(lambda: check.is_element("Submit", state, 0), Code.E0401, message)

    def test_an_element_not_shown(self, verifier):
        expect_failure(lambda: verifier().is_element("Submit", "enabled", 0), Code.E0201, "'Submit' is not on the screen.")

    def test_an_element_only_ocr_reads(self, verifier):
        expect_failure(lambda: verifier(Node("Submit", SUBMIT, ocr=True), ocr=True).is_element("Submit", "enabled", 0), Code.E0401,
              "Cannot check the enabled state of 'Submit': it was found only by OCR or image.")


class TestAssertText:
    @pytest.mark.parametrize("node", [
        pytest.param(Node("PIN", SUBMIT, value="4321"), id="an element"),
        pytest.param(Node("PIN", SUBMIT, value="4321", web=True), id="a web element"),
    ])
    @pytest.mark.parametrize("expected", [
        pytest.param("4321", id="text"),
        pytest.param("  4321 ", id="text with spaces around it"),
        pytest.param("@:4321", id="a secret"),
    ])
    def test_text_that_matches(self, verifier, node, expected):
        verifier(node).assert_text("PIN", expected, timeout="0")

    @pytest.mark.parametrize(("expected", "message"), [
        pytest.param("1234", "Element 'PIN' shows '4321' but expected '1234'.", id="text"),
        pytest.param("@:1234", "Element 'PIN' shows '****' but expected '****'.", id="a secret"),
    ])
    def test_text_that_does_not_match(self, verifier, expected, message):
        expect_failure(lambda: verifier(Node("PIN", SUBMIT, value="4321")).assert_text("PIN", expected, timeout="0"), Code.E0401, message)

    def test_text_only_ocr_reads(self, verifier):
        expect_failure(lambda: verifier(Node("DELHI", SUBMIT, ocr=True), ocr=True).assert_text("DELHI", "DELHI", timeout="0"),
              Code.E0401, "Cannot read the text of 'DELHI': it was found only by OCR or image.")

    def test_text_the_driver_cannot_read(self, verifier):
        expect_failure(lambda: verifier(Node("State", SUBMIT, readable=False)).assert_text("State", "DELHI", timeout="0"),
              Code.E0201, "'State' is not on the screen.")
