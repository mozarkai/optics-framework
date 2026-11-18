from __future__ import annotations

from pathlib import Path

import pytest

from optics_framework.helper.generate import (
    PytestGenerator,
    RobotGenerator,
    YAMLDataReader,
    generate_test_file,
)

pytestmark = pytest.mark.generate


CSV_TEST_CASES = """\
test_case,test_step
Login Test,Login Module
Login Test,Verify Module
"""

CSV_MODULES = """\
module_name,module_step,param_1,param_2
Login Module,Launch App,,
Login Module,Enter Text,${username_field},testuser
Login Module,Sleep,3000,
Verify Module,Validate Element,${login_button},
"""

CSV_ELEMENTS = """\
Element_Name,Element_ID
username_field,//input[@id='user']
login_button,loginBtn
"""

YAML_TEST_CASES = """\
Test Cases:
  - Login Test:
      - Login Module
      - Verify Module
"""

YAML_MODULES = """\
Modules:
  - Login Module:
      - Launch App
      - Enter Text ${username_field} testuser
      - Sleep 3000
  - Verify Module:
      - Validate Element ${login_button}
"""

YAML_ELEMENTS = """\
Elements:
  username_field: "//input[@id='user']"
  login_button: loginBtn
"""

CONFIG_YAML = """\
driver_sources:
  - appium:
      enabled: true
      url: http://localhost:4723
elements_sources:
  - appium_find_element:
      enabled: true
"""


def _write(folder: Path, name: str, content: str) -> Path:
    path = folder / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def csv_project(tmp_path: Path) -> Path:
    _write(tmp_path, "test_cases.csv", CSV_TEST_CASES)
    _write(tmp_path, "modules.csv", CSV_MODULES)
    _write(tmp_path, "elements.csv", CSV_ELEMENTS)
    _write(tmp_path, "config.yaml", CONFIG_YAML)
    return tmp_path


@pytest.fixture
def yaml_project(tmp_path: Path) -> Path:
    _write(tmp_path, "test_cases.yaml", YAML_TEST_CASES)
    _write(tmp_path, "modules.yaml", YAML_MODULES)
    _write(tmp_path, "elements.yaml", YAML_ELEMENTS)
    _write(tmp_path, "config.yaml", CONFIG_YAML)
    return tmp_path


class TestYAMLDataReader:

    def test_read_modules_dict_form(self, tmp_path):
        # Modules can also be a mapping (not a list of single-key dicts).
        _write(tmp_path, "m.yaml", "Modules:\n  Login Module:\n    - Sleep 1\n")
        modules = YAMLDataReader().read_modules(str(tmp_path / "m.yaml"))
        assert modules == {"Login Module": [("Sleep", ["1"])]}


def _resolve(framework, params, elements, method_name):
    if framework == "pytest":
        return PytestGenerator()._resolve_params(params, elements, method_name)
    return RobotGenerator()._resolve_params(params, {name: name for name in elements}, method_name)


class TestResolveParams:


    def test_unknown_method_keeps_name_value_as_kwarg(self):
        assert _resolve("pytest", ["mode=fast"], {}, "custom_widget_tap") == ["mode='fast'"]


def _generated_output(folder: Path, framework: str) -> str:
    ext = "py" if framework == "pytest" else "robot"
    out = folder / "generated" / "Tests" / f"test_{folder.name}.{ext}"
    assert out.exists(), f"expected generated file at {out}"
    return out.read_text(encoding="utf-8")


class TestGenerateTestFile:
    @pytest.mark.parametrize("framework", ["pytest", "robot"])
    def test_pipeline_from_yaml(self, yaml_project, framework):
        generate_test_file(str(yaml_project), framework=framework)
        code = _generated_output(yaml_project, framework)
        assert "Verify Module" in code or "verify_module" in code

    def test_custom_output_filename(self, csv_project):
        generate_test_file(str(csv_project), framework="pytest", output_filename="my_suite.py")
        assert (csv_project / "generated" / "Tests" / "my_suite.py").exists()

    def test_copies_input_templates(self, csv_project):
        templates = csv_project / "input_templates"
        templates.mkdir()
        (templates / "logo.png").write_bytes(b"fake-png")
        generate_test_file(str(csv_project), framework="pytest")
        copied = csv_project / "generated" / "Tests" / "input_templates" / "logo.png"
        assert copied.exists()
        assert copied.read_bytes() == b"fake-png"
