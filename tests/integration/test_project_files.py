"""optics dry_run on a project folder whose files are written wrong: the run stops and names the file to fix."""
import subprocess  # nosec B404
import sys

import pytest

pytestmark = pytest.mark.black_box

CONFIG = """driver_sources:
  - appium:
      enabled: true
      url: "http://127.0.0.1:4723"
      capabilities:
        platformName: Mac
        automationName: Xa11y
        app: com.apple.calculator
elements_sources:
  - appium_find_element:
      enabled: true
"""


def dry_run(folder):
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "optics_framework.helper.cli", "dry_run", str(folder), "--no-use-printer"],
        capture_output=True, text=True, timeout=120,
    )


@pytest.fixture
def project(tmp_path):
    (tmp_path / "config.yaml").write_text(CONFIG)
    (tmp_path / "test_cases.csv").write_text("test_case,test_step\nAdd,Press Seven\n")
    (tmp_path / "modules.csv").write_text("module_name,module_step,param_1\nPress Seven,Press Element,${Seven}\n")
    return tmp_path


def test_an_elements_file_without_a_locator_column_stops_the_run(project):
    (project / "elements.csv").write_text("Element_Name,Element_Locator\nSeven,//button[@id='Seven']\n")
    result = dry_run(project)
    assert result.returncode != 0
    assert "elements.csv" in result.stderr
    assert "Element_ID" in result.stderr
    assert not (project / "execution_output" / "junit_output.xml").exists()


@pytest.mark.parametrize(("name", "content"), [
    (None, None),
    ("products.csv", b"element_name,price\nmilk,2\n"),
    ("export.csv", b"\xff\xfe\x00binary"),
], ids=["locator_column", "data_file_with_element_name", "binary_csv"])
def test_an_elements_file_resolves_beside_other_csv_files(project, name, content):
    (project / "elements.csv").write_text("Element_Name,Element_ID\nSeven,//button[@id='Seven']\n")
    if name:
        (project / name).write_bytes(content)
    assert dry_run(project).returncode == 0


def test_a_step_without_the_params_its_keyword_needs_fails(project):
    (project / "elements.csv").write_text("Element_Name,Element_ID\nSeven,//button[@id='Seven']\n")
    (project / "modules.csv").write_text("module_name,module_step,param_1\nPress Seven,Press Element,\n")
    assert dry_run(project).returncode != 0
    junit = (project / "execution_output" / "junit_output.xml").read_text()
    assert "E0403" in junit
    assert "'element'" in junit


def test_a_passed_dry_run_says_what_it_did_not_check(project):
    (project / "elements.csv").write_text("Element_Name,Element_ID\nSeven,//button[@id='Seven']\n")
    result = dry_run(project)
    assert result.returncode == 0
    assert "does not check locators" in result.stdout


def test_a_value_that_holds_a_reference_is_substituted_once(project):
    (project / "elements.csv").write_text("Element_Name,Element_ID\nsnippet,${literal}\n")
    (project / "modules.csv").write_text("module_name,module_step,param_1\nPress Seven,Enter Text Direct,prefix ${snippet}\n")
    assert dry_run(project).returncode == 0
