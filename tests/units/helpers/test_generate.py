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
    CSVDataReader,
    FileWriter,
    PytestGenerator,
    RobotGenerator,
    YAMLDataReader,
    detect_file_type,
    find_all_files,
    find_files,
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

class TestCSVDataReader:
    def test_read_test_cases_groups_steps_by_case(self, csv_project):
        cases = CSVDataReader().read_test_cases(str(csv_project / "test_cases.csv"))
        assert cases == {"Login Test": ["Login Module", "Verify Module"]}

    def test_read_modules_pairs_keyword_with_params(self, csv_project):
        modules = CSVDataReader().read_modules(str(csv_project / "modules.csv"))
        assert modules["Login Module"] == [
            ("Launch App", []),
            ("Enter Text", ["${username_field}", "testuser"]),
            ("Sleep", ["3000"]),
        ]
        assert modules["Verify Module"] == [("Validate Element", ["${login_button}"])]

    def test_read_modules_preserves_numeric_params_as_strings(self, csv_project):
        modules = CSVDataReader().read_modules(str(csv_project / "modules.csv"))
        (_, sleep_params) = modules["Login Module"][2]
        assert sleep_params == ["3000"]
        assert isinstance(sleep_params[0], str)

    def test_read_elements_maps_name_to_id(self, csv_project):
        elements = CSVDataReader().read_elements(str(csv_project / "elements.csv"))
        assert elements == {
            "username_field": "//input[@id='user']",
            "login_button": "loginBtn",
        }

    def test_read_modules_unescapes_backslash_sequences(self, tmp_path):
        # A one-line CSV param carrying an escaped newline must round-trip to a real
        # newline; an escaped backslash must stay a literal backslash (not a newline).
        _write(
            tmp_path,
            "modules.csv",
            "module_name,module_step,param_1\n"
            r"M,Press Element,a\nb" + "\n"
            r"M,Enter Text,c\\nd" + "\n",
        )
        modules = CSVDataReader().read_modules(str(tmp_path / "modules.csv"))
        assert modules["M"][0] == ("Press Element", ["a\nb"])
        assert modules["M"][1] == ("Enter Text", ["c\\nd"])

    def test_read_modules_skips_blank_step_rows(self, tmp_path):
        _write(
            tmp_path,
            "modules.csv",
            "module_name,module_step,param_1\nM,Launch App,\nM,,\nM,Sleep,1\n",
        )
        modules = CSVDataReader().read_modules(str(tmp_path / "modules.csv"))
        assert modules["M"] == [("Launch App", []), ("Sleep", ["1"])]


class TestYAMLDataReader:
    def test_read_test_cases_list_of_dicts(self, yaml_project):
        cases = YAMLDataReader().read_test_cases(str(yaml_project / "test_cases.yaml"))
        assert cases == {"Login Test": ["Login Module", "Verify Module"]}

    def test_read_test_cases_dict_form(self, tmp_path):
        _write(tmp_path, "tc.yaml", "Test Cases:\n  Login Test:\n    - Login Module\n")
        cases = YAMLDataReader().read_test_cases(str(tmp_path / "tc.yaml"))
        assert cases == {"Login Test": ["Login Module"]}

    def test_read_modules_parses_inline_steps(self, yaml_project):
        modules = YAMLDataReader().read_modules(str(yaml_project / "modules.yaml"))
        assert modules["Login Module"] == [
            ("Launch App", []),
            ("Enter Text", ["${username_field}", "testuser"]),
            ("Sleep", ["3000"]),
        ]

    def test_read_modules_dict_form(self, tmp_path):
        # Modules can also be a mapping (not a list of single-key dicts).
        _write(tmp_path, "m.yaml", "Modules:\n  Login Module:\n    - Sleep 1\n")
        modules = YAMLDataReader().read_modules(str(tmp_path / "m.yaml"))
        assert modules == {"Login Module": [("Sleep", ["1"])]}

    def test_read_elements(self, yaml_project):
        elements = YAMLDataReader().read_elements(str(yaml_project / "elements.yaml"))
        assert elements == {
            "username_field": "//input[@id='user']",
            "login_button": "loginBtn",
        }

    def test_empty_file_returns_empty(self, tmp_path):
        _write(tmp_path, "empty.yaml", "")
        assert YAMLDataReader().read_modules(str(tmp_path / "empty.yaml")) == {}
        assert YAMLDataReader().read_test_cases(str(tmp_path / "empty.yaml")) == {}
        assert YAMLDataReader().read_elements(str(tmp_path / "empty.yaml")) == {}


# --------------------------------------------------------------------------- #
# _parse_step longest-match keyword splitting                                 #
# --------------------------------------------------------------------------- #

_KEYWORD_REGISTRY = set(PytestGenerator().keyword_registry)


@pytest.mark.parametrize(
    "step, expected",
    [
        ("Sleep 3", ("Sleep", ["3"])),
        ("Enter Text ${el} hello", ("Enter Text", ["${el}", "hello"])),
        # Longest-match: "Enter Text Using Keyboard" must win over "Enter Text".
        ("Enter Text Using Keyboard foo", ("Enter Text Using Keyboard", ["foo"])),
        ("Launch App", ("Launch App", [])),
        # An unknown multi-word keyword is not in the registry, so it mis-splits on
        # the first whitespace — documented behaviour worth pinning.
        ("Frobnicate Widget now", ("Frobnicate", ["Widget", "now"])),
    ],
)
def test_parse_step(step, expected):
    assert YAMLDataReader()._parse_step(step, _KEYWORD_REGISTRY) == expected


# --------------------------------------------------------------------------- #
# Registry drift guard: every generator keyword must be recognised by the YAML #
# reader's own keyword set, otherwise multi-word keywords silently mis-split.  #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("keyword", sorted(PytestGenerator().keyword_registry))
def test_yaml_reader_recognises_every_generator_keyword(tmp_path, keyword):
    _write(tmp_path, "m.yaml", f"Modules:\n  - M:\n      - {keyword} arg\n")
    modules = YAMLDataReader().read_modules(str(tmp_path / "m.yaml"))
    assert modules["M"][0][0] == keyword, (
        f"'{keyword}' is in the generator registry but not the YAML reader's "
        f"keyword set — multi-word steps will mis-split (generate.py:136 vs :199)."
    )


# --------------------------------------------------------------------------- #
# _resolve_params (shared by both generators)                                 #
# --------------------------------------------------------------------------- #

class TestResolveParams:
    @pytest.mark.parametrize(
        "framework, expected",
        [("pytest", "ELEMENTS['login_button']"), ("robot", "${ELEMENTS.login_button}")],
    )
    def test_element_reference(self, framework, expected):
        out = PytestGenerator()._resolve_params(
            ["${login_button}"], {"login_button": "loginBtn"}, framework
        )
        assert out == [expected]

    def test_missing_element_raises(self):
        with pytest.raises(ValueError, match="Element 'ghost' not found"):
            PytestGenerator()._resolve_params(["${ghost}"], {}, "pytest")

    @pytest.mark.parametrize("framework", ["pytest", "robot"])
    def test_keyword_argument_passthrough(self, framework):
        assert PytestGenerator()._resolve_params(["index=2"], {}, framework) == ["index=2"]

    def test_literal_is_quoted_for_pytest_only(self):
        assert PytestGenerator()._resolve_params(["hello"], {}, "pytest") == ["'hello'"]
        assert PytestGenerator()._resolve_params(["hello"], {}, "robot") == ["hello"]


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

    def test_elements_dict_rendered(self, code):
        assert "ELEMENTS = {" in code
        assert "'username_field': '//input[@id='user']'," in code

    def test_module_function_and_calls(self, code):
        assert "def login_module(optics: Optics) -> None:" in code
        assert "optics.launch_app()" in code
        assert "optics.enter_text(ELEMENTS['username_field'], 'testuser')" in code
        assert "optics.sleep('3000')" in code  # numeric preserved as string literal

    def test_test_function_invokes_modules(self, code):
        assert "def test_login_test(optics):" in code
        assert "    login_module(optics)" in code

    def test_unknown_keyword_falls_back_to_snake_case(self):
        code = PytestGenerator().generate(
            {"T": ["M"]}, {"M": [("Custom Widget Tap", [])]}, {}, {}
        )
        assert "optics.custom_widget_tap()" in code


class TestRobotGenerator:
    @pytest.fixture
    def code(self):
        return RobotGenerator().generate(
            SAMPLE_TEST_CASES, SAMPLE_MODULES, SAMPLE_ELEMENTS, SAMPLE_CONFIG
        )

    def test_sections_present(self, code):
        for section in ("*** Settings ***", "*** Variables ***",
                        "*** Test Cases ***", "*** Keywords ***"):
            assert section in code

    def test_test_case_setup_teardown(self, code):
        assert "Login Test" in code
        assert "[Setup]    Setup Optics" in code
        assert "[Teardown]    Quit Optics" in code

    def test_module_keyword_and_steps(self, code):
        assert "Launch App" in code
        assert "Enter Text    ${ELEMENTS.username_field}    testuser" in code
        assert "Sleep    3000" in code

    def test_transform_config_structure(self):
        transformed = RobotGenerator()._transform_config_structure(
            {"driver_sources": ["d"], "elements_sources": ["e"], "text_detection": ["t"]}
        )
        assert transformed["driver_config"] == ["d"]
        assert transformed["element_source_config"] == ["e"]
        assert transformed["text_config"] == ["t"]
        assert transformed["project_path"] == "${EXECDIR}"
        # Empty optional sections are omitted, not rendered as [].
        assert "image_config" not in transformed

    def test_escape_json_for_robot(self):
        escaped = RobotGenerator()._escape_json_for_robot('{"a":"b\\c"}')
        assert escaped == '{\\"a\\":\\"b\\\\c\\"}'


# --------------------------------------------------------------------------- #
# File-type detection                                                         #
# --------------------------------------------------------------------------- #

class TestDetectFileType:
    @pytest.mark.parametrize(
        "name, content, expected",
        [
            ("test_cases.csv", CSV_TEST_CASES, ("csv", "test_cases")),
            ("modules.csv", CSV_MODULES, ("csv", "modules")),
            ("elements.csv", CSV_ELEMENTS, ("csv", "elements")),
            ("test_cases.yaml", YAML_TEST_CASES, ("yaml", "test_cases")),
            ("modules.yaml", YAML_MODULES, ("yaml", "modules")),
            ("elements.yaml", YAML_ELEMENTS, ("yaml", "elements")),
            ("config.yaml", CONFIG_YAML, ("yaml", "config")),
        ],
    )
    def test_detects_type(self, tmp_path, name, content, expected):
        assert detect_file_type(str(_write(tmp_path, name, content))) == expected

    def test_unknown_extension_returns_none(self, tmp_path):
        assert detect_file_type(str(_write(tmp_path, "notes.txt", "hi"))) is None

    def test_missing_file_returns_none(self, tmp_path):
        assert detect_file_type(str(tmp_path / "nope.csv")) is None


class TestFindFiles:
    def test_find_all_files_buckets_by_content_type(self, csv_project):
        found = find_all_files(str(csv_project))
        assert found["test_cases"] == [str(csv_project / "test_cases.csv")]
        assert found["modules"] == [str(csv_project / "modules.csv")]
        assert found["elements"] == [str(csv_project / "elements.csv")]
        assert found["config"] == [str(csv_project / "config.yaml")]

    @pytest.mark.parametrize("project, ext", [("csv_project", "csv"), ("yaml_project", "yaml")])
    def test_find_files_returns_single_paths(self, request, project, ext):
        folder = request.getfixturevalue(project)
        tc, mod, el, cfg = find_files(str(folder))
        assert tc.endswith(f"test_cases.{ext}")
        assert mod.endswith(f"modules.{ext}")
        assert el.endswith(f"elements.{ext}")
        assert cfg.endswith("config.yaml")


# --------------------------------------------------------------------------- #
# read_mixed_data merge + conflict detection                                  #
# --------------------------------------------------------------------------- #

class TestReadMixedData:
    def test_merges_across_files(self, tmp_path):
        _write(tmp_path, "a.csv", "module_name,module_step,param_1\nA,Sleep,1\n")
        _write(tmp_path, "b.csv", "module_name,module_step,param_1\nB,Sleep,2\n")
        merged = read_mixed_data(
            [str(tmp_path / "a.csv"), str(tmp_path / "b.csv")], "modules"
        )
        assert set(merged) == {"A", "B"}

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
    def test_pipeline_from_csv(self, csv_project, framework):
        generate_test_file(str(csv_project), framework=framework)
        code = _generated_output(csv_project, framework)
        assert "Login Module" in code or "login_module" in code
        assert "3000" in code  # numeric param preserved end-to-end

    @pytest.mark.parametrize("framework", ["pytest", "robot"])
    def test_pipeline_from_yaml(self, yaml_project, framework):
        generate_test_file(str(yaml_project), framework=framework)
        code = _generated_output(yaml_project, framework)
        assert "Verify Module" in code or "verify_module" in code

    def test_mixed_csv_and_yaml_sources(self, tmp_path):
        # test cases from YAML, modules + elements from CSV.
        _write(tmp_path, "test_cases.yaml", YAML_TEST_CASES)
        _write(tmp_path, "modules.csv", CSV_MODULES)
        _write(tmp_path, "elements.csv", CSV_ELEMENTS)
        _write(tmp_path, "config.yaml", CONFIG_YAML)
        generate_test_file(str(tmp_path), framework="pytest")
        code = _generated_output(tmp_path, "pytest")
        assert "def test_login_test(optics):" in code

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
    def test_missing_required_file_aborts_without_output(self, csv_project, caplog, missing):
        (csv_project / missing).unlink()
        generate_test_file(str(csv_project), framework="pytest")
        assert not (csv_project / "generated").exists()
        assert "Error" in caplog.text or "Missing" in caplog.text

    def test_unsupported_framework_aborts(self, csv_project, caplog):
        generate_test_file(str(csv_project), framework="junit")
        assert not (csv_project / "generated" / "Tests").exists()
        assert "Unsupported framework" in caplog.text


# --------------------------------------------------------------------------- #
# FileWriter                                                                   #
# --------------------------------------------------------------------------- #

class TestFileWriter:
    def test_write_emits_code_and_requirements(self, tmp_path):
        FileWriter().write(str(tmp_path), "test_x.py", "print('hi')\n", "pytest")
        assert (tmp_path / "Tests" / "test_x.py").read_text() == "print('hi')\n"
        requirements = (tmp_path / "requirements.txt").read_text()
        assert "optics-framework" in requirements
        assert "pytest" in requirements

    def test_write_selects_robotframework_requirement(self, tmp_path):
        FileWriter().write(str(tmp_path), "test_x.robot", "x", "robot")
        requirements = (tmp_path / "requirements.txt").read_text()
        assert "robotframework" in requirements

    def test_copy_input_templates_noop_when_absent(self, tmp_path):
        (tmp_path / "generated").mkdir()
        FileWriter().copy_input_templates(str(tmp_path), str(tmp_path / "generated"))
        assert not (tmp_path / "generated" / "Tests" / "input_templates").exists()
