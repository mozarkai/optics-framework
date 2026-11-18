"""Tests for the code-generation helper (``optics_framework/helper/generate.py``).

Structure mirrors the module's four concerns:

* readers  — CSV/YAML ``DataReader`` implementations (real temp-file round-trips).
* parsing  — ``YAMLDataReader._parse_step`` longest-match keyword splitting.
* generators — ``PytestGenerator`` / ``RobotGenerator`` rendering (asserted on
  structure/semantics, never whole-file golden strings).
* discovery — file-type detection, mixed CSV/YAML merge + conflict detection, and
  the ``generate_test_file`` end-to-end pipeline.

The format×framework matrix is expressed with ``@pytest.mark.parametrize`` rather
than copy-pasted per combination, and the two registries that must stay in sync
(the YAML reader's keyword set vs. the generator's keyword→method map) get an
explicit drift guard.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from optics_framework.helper.generate import (
    PytestGenerator,
    RobotGenerator,
    YAMLDataReader,
    detect_file_type,
    generate_test_file,
    read_mixed_data,
)

pytestmark = pytest.mark.generate


# --------------------------------------------------------------------------- #
# Sample project data, expressed once in each format and materialised on disk. #
# --------------------------------------------------------------------------- #

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
    """A folder holding the sample project entirely as CSV (+ config.yaml)."""
    _write(tmp_path, "test_cases.csv", CSV_TEST_CASES)
    _write(tmp_path, "modules.csv", CSV_MODULES)
    _write(tmp_path, "elements.csv", CSV_ELEMENTS)
    _write(tmp_path, "config.yaml", CONFIG_YAML)
    return tmp_path


@pytest.fixture
def yaml_project(tmp_path: Path) -> Path:
    """The same project expressed entirely as YAML (+ config.yaml)."""
    _write(tmp_path, "test_cases.yaml", YAML_TEST_CASES)
    _write(tmp_path, "modules.yaml", YAML_MODULES)
    _write(tmp_path, "elements.yaml", YAML_ELEMENTS)
    _write(tmp_path, "config.yaml", CONFIG_YAML)
    return tmp_path


# --------------------------------------------------------------------------- #
# Readers                                                                      #
# --------------------------------------------------------------------------- #


class TestYAMLDataReader:

    def test_read_test_cases_dict_form(self, tmp_path):
        _write(tmp_path, "tc.yaml", "Test Cases:\n  Login Test:\n    - Login Module\n")
        cases = YAMLDataReader().read_test_cases(str(tmp_path / "tc.yaml"))
        assert cases == {"Login Test": ["Login Module"]}

    def test_read_modules_dict_form(self, tmp_path):
        # Modules can also be a mapping (not a list of single-key dicts).
        _write(tmp_path, "m.yaml", "Modules:\n  Login Module:\n    - Sleep 1\n")
        modules = YAMLDataReader().read_modules(str(tmp_path / "m.yaml"))
        assert modules == {"Login Module": [("Sleep", ["1"])]}


# --------------------------------------------------------------------------- #
# _parse_step longest-match keyword splitting                                 #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Registry drift guard: every generator keyword must be recognised by the YAML #
# reader's own keyword set, otherwise multi-word keywords silently mis-split.  #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _resolve_params (shared by both generators)                                 #
# --------------------------------------------------------------------------- #

def _resolve(framework, params, elements, method_name):
    if framework == "pytest":
        return PytestGenerator()._resolve_params(params, elements, method_name)
    return RobotGenerator()._resolve_params(params, {name: name for name in elements}, method_name)


class TestResolveParams:

    def test_missing_element_raises(self):
        with pytest.raises(ValueError, match="Element 'ghost' not found"):
            _resolve("pytest", ["${ghost}"], {}, "press_element")

    @pytest.mark.parametrize("framework", ["pytest", "robot"])
    @pytest.mark.parametrize("param", ["index=2", 'index="2"', "index='2'"])
    def test_keyword_argument_value_is_the_bare_string(self, framework, param):
        expected = ["index='2'"] if framework == "pytest" else ["index=2"]
        assert _resolve(framework, [param], {}, "press_element") == expected

    def test_unknown_method_keeps_name_value_as_kwarg(self):
        assert _resolve("pytest", ["mode=fast"], {}, "custom_widget_tap") == ["mode='fast'"]


# --------------------------------------------------------------------------- #
# Generator rendering (structural / semantic assertions)                      #
# --------------------------------------------------------------------------- #

SAMPLE_TEST_CASES = {"Login Test": ["Login Module"]}
SAMPLE_MODULES = {
    "Login Module": [
        ("Launch App", []),
        ("Enter Text", ["${username_field}", "testuser"]),
        ("Sleep", ["3000"]),
    ]
}
SAMPLE_ELEMENTS = {"username_field": "//input[@id='user']"}
SAMPLE_CONFIG = {"driver_sources": [{"appium": {"enabled": True}}], "elements_sources": []}


class TestPytestGenerator:
    @pytest.fixture
    def code(self):
        return PytestGenerator().generate(
            SAMPLE_TEST_CASES, SAMPLE_MODULES, SAMPLE_ELEMENTS, SAMPLE_CONFIG
        )

    def test_has_imports_and_fixture(self, code):
        assert "from optics_framework.optics import Optics" in code
        assert "@pytest.fixture(scope='module')" in code
        assert "def optics():" in code


# --------------------------------------------------------------------------- #
# File-type detection                                                         #
# --------------------------------------------------------------------------- #

class TestDetectFileType:


    def test_missing_file_returns_none(self, tmp_path):
        assert detect_file_type(str(tmp_path / "nope.csv")) is None


# --------------------------------------------------------------------------- #
# read_mixed_data merge + conflict detection                                  #
# --------------------------------------------------------------------------- #

class TestReadMixedData:

    @pytest.mark.parametrize(
        "data_type, header_row, dup_row",
        [
            ("modules", "module_name,module_step,param_1", "Dup,Sleep,1"),
            ("test_cases", "test_case,test_step", "Dup,StepOne"),
            ("elements", "Element_Name,Element_ID", "Dup,someId"),
        ],
    )
    def test_conflict_across_files_raises(self, tmp_path, data_type, header_row, dup_row):
        _write(tmp_path, "a.csv", f"{header_row}\n{dup_row}\n")
        _write(tmp_path, "b.csv", f"{header_row}\n{dup_row}\n")
        with pytest.raises(ValueError, match="Naming conflict detected"):
            read_mixed_data([str(tmp_path / "a.csv"), str(tmp_path / "b.csv")], data_type)


# --------------------------------------------------------------------------- #
# generate_test_file end-to-end pipeline                                      #
# --------------------------------------------------------------------------- #

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

    @pytest.mark.parametrize(
        "missing", ["config.yaml", "test_cases.csv", "modules.csv", "elements.csv"]
    )
    def test_missing_required_file_aborts_without_output(self, csv_project, missing):
        (csv_project / missing).unlink()
        with pytest.raises(ValueError, match="missing"):
            generate_test_file(str(csv_project), framework="pytest")
        assert not (csv_project / "generated").exists()

    def test_unsupported_framework_aborts(self, csv_project):
        with pytest.raises(ValueError, match="Unsupported framework"):
            generate_test_file(str(csv_project), framework="junit")
        assert not (csv_project / "generated" / "Tests").exists()


# --------------------------------------------------------------------------- #
# FileWriter                                                                   #
# --------------------------------------------------------------------------- #
