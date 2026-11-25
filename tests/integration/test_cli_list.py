"""optics list: the keywords a suite can call, and one keyword's parameters in CSV column order."""
import pytest

pytestmark = pytest.mark.black_box


@pytest.fixture
def optics_list(cli):
    return lambda *args: cli("list", *args)


def test_lists_keywords_as_a_suite_writes_them(optics_list):
    names = optics_list().stdout.splitlines()
    assert {"Press Element", "Assert Presence", "Run Loop"} <= set(names)


@pytest.mark.parametrize("name", [
    pytest.param("Assert Presence", id="its name"),
    pytest.param("assert presence", id="its name in lower case"),
    pytest.param("assert_presence", id="its method name"),
])
def test_one_keyword_shows_its_parameters_in_column_order(optics_list, name):
    result = optics_list(name)
    assert result.returncode == 0
    lines = result.stdout.splitlines()
    assert lines[0] == "Assert Presence"
    assert lines[1].split() == ["param_1", "elements"]
    assert lines[2].split() == ["param_2", "timeout_str", "=", "30"]
    assert "Asserts the presence of elements" in result.stdout


def test_an_unknown_keyword_suggests_the_closest_one(optics_list):
    result = optics_list("Asert Presence")
    assert result.returncode != 0
    assert "Did you mean 'Assert Presence'?" in result.stderr


@pytest.mark.parametrize("name, first", [
    pytest.param("Run Loop", 2, id="Run Loop"), pytest.param("Condition", 1, id="Condition"),
])
def test_a_keyword_that_takes_any_number_of_columns_says_so(optics_list, name, first):
    lines = optics_list(name).stdout.splitlines()
    assert lines[first].split() == [f"param_{first}...", "args..."]
