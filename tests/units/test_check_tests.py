import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("check_tests", Path(__file__).parents[2] / "scripts" / "check_tests.py")
check_tests = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_tests)


@pytest.mark.parametrize(("body", "refused"), [
    ("time.sleep(1)\n    assert ready()", "fixed delay"),
    ("while not ready():\n        time.sleep(0.1)\n    assert ready()", None),
    ('assert "run" in inspect.getsource(f)', "greps source"),
    ('assert hasattr(obj, "run")', "only proves the code was written"),
    ("assert True", "asserts a constant"),
    ("assert x == x", "compares a value with itself"),
    ("f()", "asserts nothing"),
    ("with pytest.raises(E):\n        f()", None),
    ("check_screen(f())", None),
])
def test_the_hook_refuses_tests_that_guard_nothing(tmp_path, capsys, body, refused):
    sample = tmp_path / "test_sample.py"
    sample.write_text(f"def test_a():\n    {body}\n")

    assert check_tests.main([str(sample), "README.md"]) == (1 if refused else 0)
    assert (refused or "\n") in capsys.readouterr().err + "\n"
