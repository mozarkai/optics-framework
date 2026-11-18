from collections import namedtuple
from unittest.mock import MagicMock
import pytest

from optics_framework.api.verifier import Verifier
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.optics_builder import OpticsBuilder


OcrPoint = namedtuple("OcrPoint", "x y text")


def one_match(value):
    result = MagicMock(value=value)
    result.strategy.element_source.count.return_value = 1
    return result


class MockOpticsBuilder(OpticsBuilder):
    def __init__(self):
        self.mock_driver = MagicMock()
        self.mock_element_source = MagicMock()
        self.session_config = MagicMock()
        self.session_config.execution_output_path = "/tmp"
        self.session_config.save_captures = False
        self.session = MagicMock()

    def get_driver(self):
        return self.mock_driver

    def get_element_source(self):
        return self.mock_element_source

    def get_text_detection(self):
        return None

    def get_image_detection(self):
        return None

    @property
    def event_sdk(self):
        return MagicMock()


@pytest.fixture
def verifier():
    return Verifier(MockOpticsBuilder())


class TestAssertVisibilityVsPresence:
    def test_assert_visibility_false_with_fail_true_raises(self, verifier):
        verifier.strategy_manager.assert_visibility = MagicMock(return_value=(False, None, None))

        with pytest.raises(AssertionError, match="Visibility assertion failed"):
            verifier.assert_visibility("Option 47", timeout_str="3", rule="any", fail=True)


class TestIsElementState:
    @pytest.fixture
    def located(self, verifier):
        element = object()
        verifier.strategy_manager.locate = MagicMock(return_value=iter([one_match(element)]))
        return element

    def test_an_element_no_strategy_finds_is_not_found(self, verifier):
        verifier.strategy_manager.locate = MagicMock(side_effect=OpticsError(Code.E0201, message="nope"))
        with pytest.raises(OpticsError) as failure:
            verifier.is_element("css=#x", "enabled", 1)
        assert failure.value.code == Code.E0201

    def test_an_element_found_only_on_screen_has_no_state(self, verifier):
        verifier.strategy_manager.locate = MagicMock(return_value=iter([one_match((10, 20))]))
        with pytest.raises(OpticsError, match="found only by OCR or image"):
            verifier.is_element("Submit", "checked", 1)


class TestAssertText:
    @pytest.fixture(autouse=True)
    def located(self, verifier, monkeypatch):
        monkeypatch.setattr(Verifier, "_TEXT_POLL_INTERVAL_S", 0)
        verifier.strategy_manager.assert_presence = MagicMock()
        element = object()
        verifier.strategy_manager.locate = MagicMock(side_effect=lambda _: iter([one_match(element)]))
        return element

    def test_text_that_ocr_read_with_the_match_is_compared(self, verifier):
        verifier.strategy_manager.locate = MagicMock(return_value=iter([one_match(OcrPoint(10, 20, "DELHI"))]))

        verifier.assert_text("DELHI", "DELHI", timeout="0")

    def test_text_the_driver_cannot_read_ends_as_not_found(self, verifier):
        verifier.driver.get_text_element.side_effect = RuntimeError("stale element")

        with pytest.raises(OpticsError) as raised:
            verifier.assert_text("css=#state", "DELHI", timeout="0")

        assert raised.value.code == Code.E0201

    def test_a_secret_is_compared_without_its_prefix(self, verifier):
        verifier.driver.get_text_element.return_value = "4321"

        verifier.assert_text("css=#pin", "@:4321", timeout="0")

    def test_a_secret_stays_out_of_the_error(self, verifier):
        verifier.driver.get_text_element.return_value = "4320"

        with pytest.raises(OpticsError) as raised:
            verifier.assert_text("css=#pin", "@:4321", timeout="0")

        assert "4321" not in str(raised.value)
        assert "4320" not in str(raised.value)
