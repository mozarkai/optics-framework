import logging

import pytest

from optics_framework.common import logging_config
from optics_framework.common.error import Code, OpticsError
from optics_framework.common.models import ElementData
from optics_framework.common.utils import split_store_as, store_result, substitute_inline


class TestSplitStoreAs:
    def test_takes_the_cell_out_and_keeps_the_rest_in_order(self):
        assert split_store_as(["#balance", "1", "store_as=bal"]) == (["#balance", "1"], "bal")

    def test_the_cell_may_sit_anywhere_and_the_name_may_be_written_as_a_variable(self):
        assert split_store_as(["store_as=${bal}", "#balance"]) == (["#balance"], "bal")

    def test_a_step_without_the_cell_is_left_alone(self):
        assert split_store_as(["#balance", "text=Pay"]) == (["#balance", "text=Pay"], None)


class TestStoreResult:
    def test_stores_the_text_under_the_name(self):
        elements = ElementData()
        store_result(elements, "bal", "1,200.50")
        assert elements.get_first("bal") == "1,200.50"

    def test_replaces_an_earlier_value_instead_of_adding_a_fallback(self):
        elements = ElementData()
        store_result(elements, "bal", "10")
        store_result(elements, "bal", "20")
        assert elements.get_element("bal") == ["20"]

    def test_numbers_are_kept_as_text_and_structures_as_json(self):
        elements = ElementData()
        store_result(elements, "n", 3)
        store_result(elements, "body", {"id": 7})
        assert elements.get_first("n") == "3"
        assert elements.get_first("body") == '{"id": 7}'

    def test_a_keyword_that_returned_nothing_is_an_error_not_a_silent_skip(self):
        with pytest.raises(OpticsError) as raised:
            store_result(ElementData(), "bal", None)
        assert raised.value.code == Code.E0403

    def test_an_empty_name_is_an_error(self):
        with pytest.raises(OpticsError) as raised:
            store_result(ElementData(), "", "x")
        assert raised.value.code == Code.E0403


class TestSubstituteInline:
    @staticmethod
    def _elements(**values):
        elements = ElementData()
        for name, value in values.items():
            elements.add_element(name, value)
        return elements

    def test_replaces_the_name_inside_a_larger_param(self):
        def press_element(element):
            pass
        assert substitute_inline(press_element, ["(//item)[${i}]"], self._elements(i="3")) == ["(//item)[3]"]

    def test_a_whole_param_is_left_for_the_fallback_expansion(self):
        def press_element(element):
            pass
        assert substitute_inline(press_element, ["${btn}"], self._elements(btn="#ok")) == ["${btn}"]

    def test_an_unknown_name_fails_instead_of_reaching_the_app_as_text(self):
        def press_element(element):
            pass
        with pytest.raises(OpticsError) as exc:
            substitute_inline(press_element, ["(//item)[${missing}]"], ElementData())
        assert exc.value.code == Code.E0403

    def test_evaluate_and_condition_keep_their_own_expressions(self):
        def evaluate(param1, param2):
            pass

        def condition(*args):
            pass
        elements = self._elements(a="1")
        assert substitute_inline(evaluate, ["${r}", "${a} + 1"], elements) == ["${r}", "${a} + 1"]
        assert substitute_inline(condition, ["${a} > 0", "Mod"], elements) == ["${a} > 0", "Mod"]


class TestSecretMasking:
    @pytest.fixture(autouse=True)
    def _no_secrets_from_other_tests(self):
        logging_config._secret_values.clear()
        yield
        logging_config._secret_values.clear()

    @pytest.mark.parametrize("name", ["password", "login_pwd", "otp", "PIN", "api_key", "x_token", "client_secret", "apiId", "key"])
    def test_names_that_hold_secrets(self, name):
        assert logging_config.SENSITIVE_NAME.search(name)

    def test_a_stored_secret_is_masked_in_logs_but_kept_for_the_keyword(self, caplog):
        elements = ElementData()
        store_result(elements, "login_otp", "482913")

        logging_config.execution_logger.propagate = True
        with caplog.at_level(logging.INFO, logger="optics.execution"):
            logging_config.execution_logger.info("Executing enter_text with params: ['#otp', '482913']")

        assert "482913" not in caplog.text and "****" in caplog.text
        assert elements.get_first("login_otp") == "482913"

    def test_a_plain_name_is_not_masked(self):
        store_result(ElementData(), "title", "Products")
        assert logging_config.mask_secrets("title is Products") == "title is Products"

    def test_the_at_prefix_still_marks_a_secret(self):
        assert logging_config.mask_secrets("typing @:hunter2") == "typing ****"

    def test_a_locator_named_like_a_secret_is_not_masked(self):
        locator = '//*[@resource-id="app:id/passwordET"]'
        ElementData().add_element("password_field", locator)
        assert logging_config.mask_secrets(f"Enter Text ({locator}, x)") == f"Enter Text ({locator}, x)"

    def test_what_a_keyword_logs_while_reading_a_secret_is_masked_too(self, caplog):
        elements = ElementData()
        logging_config.execution_logger.propagate = True
        with caplog.at_level(logging.INFO, logger="optics.execution"):
            with logging_config.holding_logs_for_secret("login_otp"):
                logging_config.execution_logger.info("Text of element: 482913")
                store_result(elements, "login_otp", "482913")

        assert "Text of element: ****" in caplog.text and "482913" not in caplog.text

    def test_a_plain_name_logs_straight_away(self, caplog):
        logging_config.execution_logger.propagate = True
        with caplog.at_level(logging.INFO, logger="optics.execution"):
            with logging_config.holding_logs_for_secret("title"):
                logging_config.execution_logger.info("Text of element: Products")
                assert "Products" in caplog.text
