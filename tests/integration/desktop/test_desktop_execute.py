"""The optics CLI on a desktop project: init, configure, doctor, list, dry_run, execute and generate."""

import os
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

from optics_framework.helper import doctor
from optics_framework.helper.project_config import (
    render_project_config,
    write_project_config,
)
from tests.integration.desktop.support import driver_config, junit, run_optics, runs, started_fresh, write_project

pytestmark = pytest.mark.black_box


def _configured_sample(tmp_path, host, desktop):
    made = run_optics("init", "notes", "--path", str(tmp_path), "--template", "desktop_editor")
    assert made.returncode == 0, made.stdout + made.stderr
    folder = str(tmp_path / "notes")
    write_project_config(folder, started_fresh(render_project_config(
        {"platform": "desktop", "platform_name": desktop.platform, "app": desktop.editor, "appium_url": host})))
    return folder


def test_the_desktop_sample_runs_once_configured(tmp_path, host, desktop):
    folder = _configured_sample(tmp_path, host, desktop)

    assert [r for r in doctor.validate_project(folder) if r.status == "fail"] == []
    checked = run_optics("doctor", folder, "--check")
    assert checked.returncode == 0, checked.stdout + checked.stderr
    ran = run_optics("execute", folder)
    assert ran.returncode == 0, ran.stdout + junit(Path(folder))
    assert not runs(host, desktop.editor_name)


def test_a_csv_suite_loops_branches_and_edits(tmp_path, host, desktop):
    folder = write_project(tmp_path, driver_config(host, desktop.editor), {
        "test_cases/test_cases.csv": "test_case,test_step\nNotes,Write\nNotes,Edit\nNotes,Close\n",
        "test_data/elements.csv": f"Element_Name,Element_ID\nEditor,{desktop.field}\n",
        "modules/modules.csv": "\n".join([
            "module_name,module_step,param_1,param_2,param_3,param_4,param_5",
            "Write,Launch App",
            "Write,Run Loop,Type Word,${word},one|two|three",
            "Write,Evaluate,${count},1 + 2",
            "Write,Condition,${count} == 3,Type Yes,Type No",
            "Write,Assert Text,${Editor},onetwothreeyes",
            "Write,Get Text,${Editor},store_as=typed",
            "Write,Assert Equality,${typed},onetwothreeyes",
            "Type Word,Enter Text,${Editor},${word}",
            "Type Yes,Enter Text,${Editor},yes",
            "Type No,Enter Text,${Editor},no",
            f"Edit,Press Keycode,{desktop.select_all}",
            "Edit,Enter Text Using Keyboard,<backspace>",
            "Edit,Enter Text,${Editor},again",
            f"Edit,Press Keycode,{desktop.find}",
            "Edit,Press Keycode,Escape",
            "Edit,Enter Text,${Editor},<backspace>",
            "Edit,Assert Text,${Editor},agai",
            "Close,Close And Terminate App",
        ]) + "\n",
    })

    ran = run_optics("execute", folder)
    assert ran.returncode == 0, ran.stdout + junit(tmp_path)
    assert not runs(host, desktop.editor_name)


def test_a_yaml_suite_runs_with_fallback_locators(tmp_path, host, desktop):
    folder = write_project(tmp_path, driver_config(host, desktop.editor), {
        "suite.yaml": "\n".join([
            "Test Cases:",
            "  - Notes:",
            "      - Write",
            "Modules:",
            "  - Write:",
            "      - Launch App",
            '      - Enter Text ${Editor} text="from yaml"',
            '      - Assert Text ${Editor} expected="from yaml"',
            "      - Close And Terminate App",
            "Elements:",
            "  Editor:",
            '    - //button[@name="No such field"]',
            f"    - {desktop.field}",
        ]) + "\n",
    })

    ran = run_optics("execute", folder)
    assert ran.returncode == 0, ran.stdout + junit(tmp_path)


def test_dry_run_passes_a_good_suite_and_fails_an_unknown_keyword(tmp_path, host, desktop):
    good = _configured_sample(tmp_path / "good", host, desktop)
    bad = write_project(tmp_path / "bad", driver_config(host, desktop.editor), {
        "test_cases/test_cases.csv": "test_case,test_step\nNotes,Write\n",
        "modules/modules.csv": "module_name,module_step,param_1\nWrite,Launch App\nWrite,Type Into The Void,x\n",
    })

    assert run_optics("dry_run", good).returncode == 0
    assert run_optics("dry_run", bad).returncode != 0
    assert runs(host, desktop.editor_name) is False


def test_list_names_the_keywords_a_desktop_suite_uses():
    listed = run_optics("list")

    assert listed.returncode == 0, listed.stderr
    for keyword in ("press_keycode", "enter_text", "launch_other_app", "get_interactive_elements", "run_loop"):
        assert keyword in listed.stdout, keyword


def test_generated_pytest_code_runs_the_suite(tmp_path, host, desktop):
    folder = _configured_sample(tmp_path, host, desktop)

    generated = run_optics("generate", folder, "--output", "test_notes.py")
    assert generated.returncode == 0, generated.stdout + generated.stderr
    output = Path(folder, "generated", "Tests", "test_notes.py")
    ran = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", str(output)],
                         capture_output=True, text=True, timeout=600, cwd=folder, check=False,
                         env={**os.environ, "PYTHONPATH": ""})  # nosec B603
    assert ran.returncode == 0, ran.stdout[-3000:]


def test_a_desktop_project_without_an_app_stops_with_e0104(tmp_path, host, desktop):
    config = driver_config(host, desktop.editor)
    del config["driver_sources"][0]["appium"]["capabilities"]["app"]
    folder = write_project(tmp_path, config, {
        "test_cases/test_cases.csv": "test_case,test_step\nNotes,Write\n",
        "modules/modules.csv": "module_name,module_step,param_1\nWrite,Launch App\n",
    })

    assert any(r.status == "fail" and "appName" in r.detail for r in doctor.validate_project(folder))
    ran = run_optics("execute", folder)
    assert ran.returncode != 0
    assert "E0104" in ran.stdout + ran.stderr + junit(tmp_path)


def test_a_project_names_its_app_in_appium_options(tmp_path, host, desktop):
    config = driver_config(host, desktop.editor)
    caps = config["driver_sources"][0]["appium"]["capabilities"]
    caps["appium:options"] = {"app": caps.pop("app")}
    folder = write_project(tmp_path, config, {
        "test_cases/test_cases.csv": "test_case,test_step\nNotes,Write\n",
        "modules/modules.csv": "\n".join([
            "module_name,module_step,param_1,param_2",
            "Write,Launch App",
            f"Write,Enter Text,{desktop.field},options",
            f"Write,Assert Text,{desktop.field},options",
            "Write,Close And Terminate App",
        ]) + "\n",
    })

    ran = run_optics("execute", folder)
    assert ran.returncode == 0, ran.stdout + junit(tmp_path)
