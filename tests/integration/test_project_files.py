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


def test_an_elements_file_with_a_locator_column_resolves_its_elements(project):
    (project / "elements.csv").write_text("Element_Name,Element_ID\nSeven,//button[@id='Seven']\n")
    assert dry_run(project).returncode == 0


def test_a_data_file_with_an_element_name_column_is_left_alone(project):
    (project / "elements.csv").write_text("Element_Name,Element_ID\nSeven,//button[@id='Seven']\n")
    (project / "products.csv").write_text("element_name,price\nmilk,2\n")
    assert dry_run(project).returncode == 0


def test_a_csv_that_is_not_text_is_skipped(project):
    (project / "elements.csv").write_text("Element_Name,Element_ID\nSeven,//button[@id='Seven']\n")
    (project / "export.csv").write_bytes(b"\xff\xfe\x00binary")
    assert dry_run(project).returncode == 0
