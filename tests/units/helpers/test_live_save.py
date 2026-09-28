"""Unit tests for the `optics live` `/save` workflow (``LiveController.save``).

These exercise the CSV and YAML serialisation directly, without a device or
session: a lightweight subclass overrides ``__init__`` to set only the attributes
``save`` touches (``folder_path``, ``_artifacts_dir``, ``recorded``, ``saved``,
plus a stub ``session`` holding the session's known elements). The output is
validated both structurally and by round-tripping through ``CSVDataReader`` /
``YAMLDataReader`` so it stays compatible with the batch runner.
"""
import csv
import os
from types import SimpleNamespace

import pytest
import yaml

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.models import ElementData
from optics_framework.common.runner.data_reader import CSVDataReader, YAMLDataReader
from optics_framework.helper import live, live_tui
from optics_framework.helper.execute import find_files
from optics_framework.helper.live import (
    LiveController,
    SaveConflictError,
    SaveFormat,
    SaveResult,
    keyword_to_title,
)

pytestmark = pytest.mark.white_box


class _Controller(LiveController):
    """LiveController with the heavy session __init__ bypassed.

    Intentionally does not call super().__init__() since we're unit-testing
    the save logic without a real session. Only the attributes that save()
    touches are initialized.
    """

    def __init__(self, folder: str, elements: ElementData | None = None):  # noqa: super-init-not-called
        self.folder_path = folder
        self._artifacts_dir = os.path.join(folder, "no_artifacts")  # absent -> no snapshot
        self.recorded = []
        self.saved = False
        # save() merges these into the project's tracked elements CSV; the flag
        # short-circuits ensure_elements_loaded's own project walk.
        self._elements_loaded = True
        self.session = SimpleNamespace(elements=elements if elements is not None else ElementData())


@pytest.fixture
def c(tmp_path):
    """A save-only LiveController rooted at an auto-cleaned temp dir."""
    return _Controller(str(tmp_path))


def _rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_first_save_writes_standard_files_and_clears_buffer(c):
    c.recorded = [("launch_app", []), ("press_element", ["${login_btn}", "index=0"])]

    result = c.save("Login Test", "login_module")

    assert isinstance(result, SaveResult)
    assert result.appended_module is False and result.appended_test_case is False
    assert result.step_count == 2
    # Fixed, standard file names in their respective folders.
    assert result.modules_path.endswith(os.path.join("modules", "modules.csv"))
    assert result.test_cases_path.endswith(os.path.join("test_cases", "test_cases.csv"))
    assert result.elements_path.endswith(os.path.join("elements", "elements.csv"))
    # Buffer is cleared so the next actions form the next module.
    assert c.recorded == []
    assert c.saved is True

    mods = _rows(result.modules_path)
    assert [m["module_name"] for m in mods] == ["login_module", "login_module"]
    assert mods[1]["module_step"] == "Press Element"
    assert mods[1]["param_1"] == "${login_btn}"
    assert _rows(result.test_cases_path) == [
        {"test_case": "Login Test", "test_step": "login_module"}
    ]
    # elements.csv is a header-only stub (no named elements from live).
    assert os.path.isfile(result.elements_path)
    assert _rows(result.elements_path) == []


def test_second_module_appends_and_reconciles_param_columns(c):
    c.recorded = [("enter_text", ["${user}", "bob"])]
    first = c.save("TC one", "mod_a")

    c.recorded = [("scroll", [])]  # fewer params than mod_a
    second = c.save("TC two", "mod_b")

    assert second.modules_path == first.modules_path  # same fixed file, appended
    mods = _rows(second.modules_path)
    assert [m["module_name"] for m in mods] == ["mod_a", "mod_b"]
    # param_1 header spans the widest row; the param-less row is padded empty.
    assert mods[0]["param_1"] == "${user}"
    assert mods[1]["param_1"] == ""
    tcs = _rows(second.test_cases_path)
    assert [(t["test_case"], t["test_step"]) for t in tcs] == [
        ("TC one", "mod_a"),
        ("TC two", "mod_b"),
    ]


def test_duplicate_module_name_raises_conflict(c):
    c.recorded = [("launch_app", [])]
    c.save("TC", "dup_module")

    c.recorded = [("scroll", [])]
    with pytest.raises(SaveConflictError) as exc:
        c.save("Other TC", "dup_module")
    assert ("module", "dup_module") in exc.value.conflicts
    # A rejected save leaves the buffer intact for a retry.
    assert c.recorded == [("scroll", [])]


def test_duplicate_test_case_name_raises_conflict(c):
    c.recorded = [("launch_app", [])]
    c.save("Shared TC", "mod_a")

    c.recorded = [("scroll", [])]
    with pytest.raises(SaveConflictError) as exc:
        c.save("Shared TC", "mod_b")
    assert ("test case", "Shared TC") in exc.value.conflicts


def test_allow_append_merges_into_existing_module(c):
    c.recorded = [("launch_app", [])]
    c.save("TC", "mod_a")

    c.recorded = [("scroll", [])]
    result = c.save("TC 2", "mod_a", allow_append=True)

    assert result.appended_module is True
    mod_rows = [m for m in _rows(result.modules_path) if m["module_name"] == "mod_a"]
    assert [m["module_step"] for m in mod_rows] == ["Launch App", "Scroll"]


def test_exact_duplicate_test_case_row_is_not_repeated(c):
    c.recorded = [("launch_app", [])]
    c.save("TC", "mod_a")

    c.recorded = [("scroll", [])]
    result = c.save("TC", "mod_a", allow_append=True)  # same (test_case, module) pair

    pairs = [(t["test_case"], t["test_step"]) for t in _rows(result.test_cases_path)]
    assert pairs.count(("TC", "mod_a")) == 1


def test_empty_buffer_is_refused(c):
    with pytest.raises(OpticsError) as exc:
        c.save("TC", "mod")
    assert exc.value.code == Code.E0501
    assert "Nothing recorded" in str(exc.value)


@pytest.mark.parametrize(
    "test_case,module,message",
    [("!!!", "mod", "Invalid test case"), ("TC", "@@@", "Invalid module")],
)
def test_invalid_names_are_refused(c, test_case, module, message):
    c.recorded = [("launch_app", [])]
    with pytest.raises(OpticsError) as exc:
        c.save(test_case, module)
    assert exc.value.code == Code.E0501
    assert message in str(exc.value)


def test_saved_files_round_trip_through_csv_reader(c):
    c.recorded = [("launch_app", []), ("enter_text", ["${user}", "hello, world"])]
    first = c.save("TC one", "mod_a")
    c.recorded = [("scroll", [])]
    c.save("TC two", "mod_b")

    reader = CSVDataReader()
    modules = reader.read_modules(first.modules_path)
    assert modules == {
        "mod_a": [("Launch App", []), ("Enter Text", ["${user}", "hello, world"])],
        "mod_b": [("Scroll", [])],
    }
    assert reader.read_test_cases(first.test_cases_path) == {
        "TC one": ["mod_a"],
        "TC two": ["mod_b"],
    }


def _seed_elements_csv(tmp_path, content: str) -> str:
    """Create the scaffold-convention ``test_data/elements.csv``; returns its path."""
    elements_dir = tmp_path / "test_data"
    elements_dir.mkdir()
    elements_path = elements_dir / "elements.csv"
    elements_path.write_text(content, encoding="utf-8")
    return str(elements_path)


def test_save_merges_new_elements_into_existing_test_data_csv(tmp_path):
    elements_path = _seed_elements_csv(
        tmp_path, "Element_Name,Element_ID\nHeading,//h1[@id='top']\n"
    )
    # "heading" differs only in case from the tracked "Heading" -> skipped;
    # Submit_Button is genuinely new -> appended.
    c = _Controller(str(tmp_path), ElementData(elements={
        "heading": ["//h1[@id='top']"],
        "Submit_Button": ["//button[@type='submit']"],
    }))
    c.recorded = [("launch_app", [])]

    result = c.save("TC", "mod")

    assert result.elements_path == elements_path
    # Only the new row was added; original content/formatting (LF endings) intact.
    with open(elements_path, encoding="utf-8") as fh:
        assert fh.read() == (
            "Element_Name,Element_ID\n"
            "Heading,//h1[@id='top']\n"
            "Submit_Button,//button[@type='submit']\n"
        )
    assert not (tmp_path / "elements").exists()  # no second elements folder scattered


def test_save_without_existing_elements_csv_creates_stub(c):
    c.recorded = [("launch_app", [])]

    result = c.save("TC", "mod")

    assert result.elements_path == os.path.join(c.folder_path, "elements", "elements.csv")
    assert os.path.isfile(result.elements_path)
    assert _rows(result.elements_path) == []  # header-only stub, as documented


def test_resave_appends_no_duplicate_element_rows(tmp_path):
    elements_path = _seed_elements_csv(tmp_path, "Element_Name,Element_ID\nHeading,//h1\n")
    c = _Controller(str(tmp_path), ElementData(elements={
        "Heading": ["//h1"],
        "Submit_Button": ["//button[@type='submit']"],
    }))
    c.recorded = [("launch_app", [])]
    first = c.save("TC", "mod")

    c.recorded = [("scroll", [])]
    second = c.save("TC", "mod", allow_append=True)  # same module -> conflict path opted in

    assert second.elements_path == first.elements_path == elements_path
    assert _rows(second.elements_path) == [
        {"Element_Name": "Heading", "Element_ID": "//h1"},
        {"Element_Name": "Submit_Button", "Element_ID": "//button[@type='submit']"},
    ]


# -- YAML --------------------------------------------------------------------------


def _yaml(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _seed(tmp_path, relpath: str, content: str) -> str:
    path = tmp_path / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def test_yaml_save_on_fresh_project_writes_the_yaml_layout(c):
    c.recorded = [("launch_app", []), ("press_element", ["${login_btn}", "index=0"])]

    result = c.save("Login Test", "login_module", file_format=SaveFormat.YAML)

    assert result.modules_path == os.path.join(c.folder_path, "modules", "modules.yaml")
    assert result.test_cases_path == os.path.join(c.folder_path, "test_cases", "test_cases.yaml")
    assert result.elements_path == os.path.join(c.folder_path, "elements", "elements.yaml")
    assert _yaml(result.modules_path) == {
        "Modules": [{"login_module": ["Launch App", "Press Element ${login_btn} index=0"]}]
    }
    assert _yaml(result.test_cases_path) == {"Test Cases": [{"Login Test": ["login_module"]}]}
    assert _yaml(result.elements_path) == {"Elements": {}}
    assert not os.path.exists(os.path.join(c.folder_path, "modules", "modules.csv"))
    assert c.recorded == []
    assert c.saved is True


def test_yaml_round_trip_matches_what_the_csv_path_produces(tmp_path):
    recorded = [
        ("launch_app", []),
        ("enter_text", ["${search}", "two words, and a comma"]),
        ("press_element", ["text=Sign in now", "event_name=tap"]),
        ("press_element", ["//input[@name='a b']"]),
        ("enter_text", ["${field}", "it's here"]),
        ("enter_text", ["${field}", '"quoted"']),
        ("enter_text", ["${field}", "a:b #c {d} [e] & *f"]),
    ]
    saved = {}
    for fmt in SaveFormat:
        folder = tmp_path / fmt.value
        folder.mkdir()
        ctrl = _Controller(str(folder))
        ctrl.recorded = list(recorded)
        saved[fmt] = ctrl.save("TC", "mod", file_format=fmt)

    csv_reader, yaml_reader = CSVDataReader(), YAMLDataReader()
    csv_result, yaml_result = saved[SaveFormat.CSV], saved[SaveFormat.YAML]
    expected = {"mod": [(keyword_to_title(f), p) for f, p in recorded]}
    assert csv_reader.read_modules(csv_result.modules_path) == expected
    assert yaml_reader.read_modules(yaml_result.modules_path) == expected
    assert yaml_reader.read_test_cases(yaml_result.test_cases_path) == (
        csv_reader.read_test_cases(csv_result.test_cases_path)
    )


def test_yaml_keeps_an_empty_param(c):
    c.recorded = [("enter_text", ["${field}", ""])]

    result = c.save("TC", "mod", file_format=SaveFormat.YAML)

    assert YAMLDataReader().read_modules(result.modules_path) == {
        "mod": [("Enter Text", ["${field}", ""])]
    }


def test_saved_yaml_suite_is_discovered_by_the_runner(c):
    c.recorded = [("launch_app", [])]

    result = c.save("TC", "mod", file_format=SaveFormat.YAML)

    test_cases, modules, elements, _api, _errors, _config = find_files(c.folder_path)
    assert test_cases == [result.test_cases_path]
    assert modules == [result.modules_path]
    assert elements == [result.elements_path]


def test_second_yaml_save_appends_a_new_module_and_test_case(c):
    c.recorded = [("launch_app", [])]
    c.save("TC one", "mod_a", file_format=SaveFormat.YAML)

    c.recorded = [("scroll", [])]
    result = c.save("TC two", "mod_b", file_format=SaveFormat.YAML)

    assert _yaml(result.modules_path)["Modules"] == [
        {"mod_a": ["Launch App"]},
        {"mod_b": ["Scroll"]},
    ]
    assert _yaml(result.test_cases_path)["Test Cases"] == [
        {"TC one": ["mod_a"]},
        {"TC two": ["mod_b"]},
    ]


def test_yaml_append_extends_the_existing_module_and_preserves_other_content(tmp_path):
    modules_path = _seed(
        tmp_path,
        "modules/modules.yaml",
        "Modules:\n"
        "  - kept_module:\n"
        "      - Launch App\n"
        "  - mod_a:\n"
        "      - Sleep 1\n"
        "Notes: hand-written\n",
    )
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]

    result = c.save("TC", "mod_a", allow_append=True)

    assert result.modules_path == modules_path
    assert result.appended_module is True
    assert _yaml(modules_path) == {
        "Modules": [{"kept_module": ["Launch App"]}, {"mod_a": ["Sleep 1", "Scroll"]}],
        "Notes": "hand-written",
    }


def test_yaml_append_does_not_repeat_a_listed_module_in_the_test_case(c):
    c.recorded = [("launch_app", [])]
    c.save("TC", "mod_a", file_format=SaveFormat.YAML)

    c.recorded = [("scroll", [])]
    result = c.save("TC", "mod_a", allow_append=True, file_format=SaveFormat.YAML)

    assert _yaml(result.test_cases_path)["Test Cases"] == [{"TC": ["mod_a"]}]
    assert _yaml(result.modules_path)["Modules"] == [{"mod_a": ["Launch App", "Scroll"]}]


@pytest.mark.parametrize(
    "seed_path, seed, expected",
    [
        ("suite/flows.yaml", "Modules:\n  - x:\n      - Launch App\n", SaveFormat.YAML),
        ("suite/cases.yml", "Test Cases:\n  - T:\n      - x\n", SaveFormat.YAML),
        ("suite/flows.csv", "module_name,module_step\nx,Launch App\n", SaveFormat.CSV),
        ("test_data/elements.yaml", "Elements:\n  e: //e\n", SaveFormat.CSV),
    ],
)
def test_format_follows_the_project_suite_files(tmp_path, seed_path, seed, expected):
    _seed(tmp_path, seed_path, seed)
    c = _Controller(str(tmp_path))
    c.recorded = [("launch_app", [])]

    result = c.save("TC", "mod")

    assert result.modules_path.endswith(f"modules.{expected.value}")


def test_mixed_project_keeps_saving_csv(tmp_path):
    _seed(tmp_path, "suite/flows.yaml", "Modules:\n  - x:\n      - Launch App\n")
    _seed(tmp_path, "suite/cases.csv", "test_case,test_step\nT,x\n")
    c = _Controller(str(tmp_path))
    c.recorded = [("launch_app", [])]

    assert c.save("TC", "mod").modules_path.endswith("modules.csv")


@pytest.mark.parametrize("fmt", list(SaveFormat))
def test_explicit_format_overrides_detection(tmp_path, fmt):
    if fmt is SaveFormat.CSV:
        _seed(tmp_path, "suite/flows.yaml", "Modules:\n  - x:\n      - Launch App\n")
    else:
        _seed(tmp_path, "suite/flows.csv", "module_name,module_step\nx,Launch App\n")
    c = _Controller(str(tmp_path))
    c.recorded = [("launch_app", [])]

    assert c.save("TC", "mod", file_format=fmt).modules_path.endswith(f"modules.{fmt.value}")


@pytest.mark.parametrize(
    "seed_path, seed, fmt, conflict",
    [
        ("modules/modules.csv", "module_name,module_step\nmod,Launch App\n",
         SaveFormat.YAML, ("module", "mod")),
        ("suite/flows.yaml", "Modules:\n  - mod:\n      - Launch App\n",
         SaveFormat.CSV, ("module", "mod")),
        ("suite/login.csv", "module_name,module_step\nmod,Launch App\n",
         SaveFormat.CSV, ("module", "mod")),
        ("test_cases/test_cases.yaml", "Test Cases:\n  - TC:\n      - other\n",
         SaveFormat.CSV, ("test case", "TC")),
    ],
)
def test_conflict_check_spans_every_csv_and_yaml_file(tmp_path, seed_path, seed, fmt, conflict):
    _seed(tmp_path, seed_path, seed)
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]

    with pytest.raises(SaveConflictError) as exc:
        c.save("TC", "mod", file_format=fmt)

    assert conflict in exc.value.conflicts
    assert c.recorded == [("scroll", [])]


def test_append_to_a_definition_in_another_file_is_refused(tmp_path):
    _seed(tmp_path, "modules/modules.csv", "module_name,module_step\nmod,Launch App\n")
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]

    with pytest.raises(OpticsError) as exc:
        c.save("TC", "mod", allow_append=True, file_format=SaveFormat.YAML)

    assert exc.value.code == Code.E0501
    assert os.path.join("modules", "modules.csv") in str(exc.value)
    assert not (tmp_path / "modules" / "modules.yaml").exists()
    assert c.recorded == [("scroll", [])]


@pytest.mark.parametrize(
    "fmt, target, seed",
    [
        (SaveFormat.CSV, "modules/modules.csv", "module_name,module_step\nmod,Launch App\n"),
        (SaveFormat.YAML, "modules/modules.yaml", "Modules:\n  - mod:\n      - Launch App\n"),
    ],
)
def test_append_is_refused_when_another_file_also_defines_the_name(tmp_path, fmt, target, seed):
    target_path = _seed(tmp_path, target, seed)
    _seed(tmp_path, "suite/login.csv", "module_name,module_step\nmod,Sleep 1\n")
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]

    with pytest.raises(OpticsError) as exc:
        c.save("TC", "mod", allow_append=True, file_format=fmt)

    assert os.path.join("suite", "login.csv") in str(exc.value)
    with open(target_path, encoding="utf-8") as fh:
        assert fh.read() == seed


def _fail_on_call(monkeypatch, failing_call: int):
    """Make the ``failing_call``-th file replacement raise, as a full disk would."""
    real = live._replace_file
    calls = []

    def replace(path, data):
        calls.append(path)
        if len(calls) == failing_call:
            raise OSError("No space left on device")
        real(path, data)

    monkeypatch.setattr(live, "_replace_file", replace)


@pytest.mark.parametrize(
    "fmt, seed",
    [
        (SaveFormat.CSV, "module_name,module_step\nold,Launch App\n"),
        (SaveFormat.YAML, "Modules:\n- old:\n  - Launch App\n"),
    ],
)
def test_a_failed_write_rolls_back_the_files_already_written(tmp_path, monkeypatch, fmt, seed):
    modules_path = _seed(tmp_path, f"modules/modules.{fmt.value}", seed)
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]
    _fail_on_call(monkeypatch, failing_call=2)

    with pytest.raises(OpticsError) as exc:
        c.save("TC", "new", file_format=fmt)

    assert "rolled back" in str(exc.value)
    with open(modules_path, encoding="utf-8") as fh:
        assert fh.read() == seed
    assert not (tmp_path / "test_cases" / f"test_cases.{fmt.value}").exists()
    assert c.recorded == [("scroll", [])]

    monkeypatch.undo()
    retried = c.save("TC", "new", file_format=fmt)
    assert retried.appended_module is False


def test_replace_file_never_truncates_the_original(tmp_path, monkeypatch):
    path = _seed(tmp_path, "modules/modules.csv", "module_name,module_step\nold,Launch App\n")

    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(live.os, "replace", fail)
    with pytest.raises(OSError):
        live._replace_file(path, b"new content")

    with open(path, encoding="utf-8") as fh:
        assert fh.read() == "module_name,module_step\nold,Launch App\n"
    assert os.listdir(os.path.dirname(path)) == ["modules.csv"]


def test_replace_file_keeps_the_existing_file_mode(tmp_path):
    path = _seed(tmp_path, "modules/modules.csv", "a\n")
    os.chmod(path, 0o640)

    live._replace_file(path, b"b\n")

    assert os.stat(path).st_mode & 0o777 == 0o640


@pytest.mark.parametrize("fmt", list(SaveFormat))
def test_save_through_a_symlinked_suite_file_updates_the_linked_file(tmp_path, fmt):
    header = (
        "module_name,module_step\nold,Launch App\n"
        if fmt is SaveFormat.CSV
        else "Modules:\n- old:\n  - Launch App\n"
    )
    shared = _seed(tmp_path, f"shared/modules.{fmt.value}", header)
    (tmp_path / "modules").mkdir()
    link = tmp_path / "modules" / f"modules.{fmt.value}"
    link.symlink_to(shared)
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]

    c.save("TC", "new", file_format=fmt)

    assert link.is_symlink()
    reader = CSVDataReader() if fmt is SaveFormat.CSV else YAMLDataReader()
    assert reader.read_modules(shared) == {
        "old": [("Launch App", [])],
        "new": [("Scroll", [])],
    }


def test_an_unrecognised_file_at_the_elements_stub_path_is_left_alone(tmp_path):
    content = "name,locator\nlogin,//button\n"
    stub_path = _seed(tmp_path, "elements/elements.csv", content)
    c = _Controller(str(tmp_path))
    c.recorded = [("launch_app", [])]

    result = c.save("TC", "mod", file_format=SaveFormat.CSV)

    assert result.elements_path == stub_path
    with open(stub_path, encoding="utf-8") as fh:
        assert fh.read() == content


def test_modules_and_test_cases_linked_to_one_yaml_file_keep_both_changes(tmp_path):
    shared = _seed(tmp_path, "shared/suite.yaml", "Modules: []\nTest Cases: []\n")
    for folder, name in (("modules", "modules.yaml"), ("test_cases", "test_cases.yaml")):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / name).symlink_to(shared)
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]

    c.save("TC", "mod", file_format=SaveFormat.YAML)

    assert _yaml(shared)["Modules"] == [{"mod": ["Scroll"]}]
    assert _yaml(shared)["Test Cases"] == [{"TC": ["mod"]}]


def test_csv_suite_files_linked_to_one_file_are_refused_before_writing(tmp_path):
    shared = _seed(tmp_path, "shared/suite.csv", "")
    for folder, name in (("modules", "modules.csv"), ("test_cases", "test_cases.csv")):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / name).symlink_to(shared)
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]

    with pytest.raises(OpticsError) as exc:
        c.save("TC", "mod", file_format=SaveFormat.CSV)

    assert "same file" in str(exc.value)
    with open(shared, encoding="utf-8") as fh:
        assert fh.read() == ""
    assert c.recorded == [("scroll", [])]


def test_rollback_removes_the_file_created_behind_a_dangling_link(tmp_path, monkeypatch):
    (tmp_path / "modules").mkdir()
    link = tmp_path / "modules" / "modules.csv"
    target = tmp_path / "shared" / "modules.csv"
    link.symlink_to(target)
    c = _Controller(str(tmp_path))
    c.recorded = [("scroll", [])]
    _fail_on_call(monkeypatch, failing_call=2)

    with pytest.raises(OpticsError):
        c.save("TC", "mod", file_format=SaveFormat.CSV)

    assert link.is_symlink()
    assert not target.exists()


def test_yaml_elements_merge_into_the_existing_file_with_fallback_lists(tmp_path):
    elements_path = _seed(tmp_path, "test_data/elements.yaml", "Elements:\n  Heading: //h1\n")
    c = _Controller(str(tmp_path), ElementData(elements={
        "heading": ["//h1"],
        "login_button": ["loginBtn", "//button[@id='login']"],
        "title": ["//h2"],
    }))
    c.recorded = [("launch_app", [])]

    result = c.save("TC", "mod", file_format=SaveFormat.YAML)

    assert result.elements_path == elements_path
    assert _yaml(elements_path) == {"Elements": {
        "Heading": "//h1",
        "login_button": ["loginBtn", "//button[@id='login']"],
        "title": "//h2",
    }}
    assert YAMLDataReader().read_elements(elements_path) == {
        "Heading": ["//h1"],
        "login_button": ["loginBtn", "//button[@id='login']"],
        "title": ["//h2"],
    }
    assert not (tmp_path / "elements").exists()


def test_unchanged_elements_file_is_not_rewritten(tmp_path):
    content = "# kept as written\nElements:\n  Heading: //h1\n"
    elements_path = _seed(tmp_path, "test_data/elements.yaml", content)
    c = _Controller(str(tmp_path), ElementData(elements={"Heading": ["//h1"]}))
    c.recorded = [("launch_app", [])]

    c.save("TC", "mod", file_format=SaveFormat.YAML)

    with open(elements_path, encoding="utf-8") as fh:
        assert fh.read() == content


def test_elements_kept_in_the_modules_file_are_merged_without_losing_the_module(tmp_path):
    modules_path = _seed(
        tmp_path, "modules/modules.yaml", "Modules:\n  - old:\n      - Launch App\nElements: {}\n"
    )
    c = _Controller(str(tmp_path), ElementData(elements={"btn": ["//button"]}))
    c.recorded = [("scroll", [])]

    result = c.save("TC", "new")

    assert result.elements_path == modules_path
    assert _yaml(modules_path) == {
        "Modules": [{"old": ["Launch App"]}, {"new": ["Scroll"]}],
        "Elements": {"btn": "//button"},
    }


def test_step_yaml_cannot_express_fails_before_writing(c):
    c.recorded = [("launch_app", []), ("enter_text", ["${f}", "both \" and ' quotes"])]

    with pytest.raises(OpticsError) as exc:
        c.save("TC", "mod", file_format=SaveFormat.YAML)

    assert exc.value.code == Code.E0501
    assert "save this module as CSV" in str(exc.value)
    assert os.listdir(c.folder_path) == []
    assert len(c.recorded) == 2


@pytest.mark.parametrize(
    "content, message",
    [
        ("Modules: [unclosed\n", "invalid YAML"),
        ("- just\n- a list\n", "not a mapping"),
        ("Modules:\n  name: not-a-list\n", "'Modules' is not a list"),
    ],
)
def test_malformed_yaml_target_fails_without_writing(tmp_path, content, message):
    modules_path = _seed(tmp_path, "modules/modules.yaml", content)
    c = _Controller(str(tmp_path))
    c.recorded = [("launch_app", [])]

    with pytest.raises(OpticsError) as exc:
        c.save("TC", "mod", file_format=SaveFormat.YAML)

    assert message in str(exc.value)
    with open(modules_path, encoding="utf-8") as fh:
        assert fh.read() == content
    assert not (tmp_path / "test_cases").exists()


# -- /save command parsing ---------------------------------------------------------


def _save_tui(monkeypatch):
    calls: list[tuple] = []

    def save(test_case, module_name, *, allow_append, file_format):
        calls.append((test_case, module_name, allow_append, file_format))
        return SaveResult(
            modules_path="m", test_cases_path="t", elements_path="e", artifacts_path=None,
            test_case=test_case, module_name=module_name, step_count=1,
            appended_module=False, appended_test_case=False,
        )

    tui = live_tui.LiveTUI(SimpleNamespace(saved=True, recorded=[], save=save))
    messages: list[str] = []
    monkeypatch.setattr(tui, "_info", lambda msg, raw="": messages.append(msg))
    return tui, calls, messages


@pytest.mark.parametrize(
    "arg, file_format",
    [
        ("TC mod", None),
        ("TC mod yaml", SaveFormat.YAML),
        ("TC mod YML", SaveFormat.YAML),
        ("TC mod csv", SaveFormat.CSV),
    ],
)
def test_save_command_passes_the_requested_format(monkeypatch, arg, file_format):
    tui, calls, _messages = _save_tui(monkeypatch)

    tui._cmd_save(arg)

    assert calls == [("TC", "mod", False, file_format)]


@pytest.mark.parametrize("arg", ["TC", "TC mod xml", "TC mod yaml extra"])
def test_save_command_rejects_a_bad_call_with_usage(monkeypatch, arg):
    tui, calls, messages = _save_tui(monkeypatch)

    tui._cmd_save(arg)

    assert calls == []
    assert messages == ["Usage: /save <test_case> <module_name> [csv|yaml]"]


def test_append_confirmation_is_tied_to_the_format(monkeypatch):
    appends: list[tuple] = []

    def save(test_case, module_name, *, allow_append, file_format):
        appends.append((file_format, allow_append))
        if not allow_append:
            raise SaveConflictError([("module", module_name)])
        return SaveResult(
            modules_path="m", test_cases_path="t", elements_path="e", artifacts_path=None,
            test_case=test_case, module_name=module_name, step_count=1,
            appended_module=True, appended_test_case=False,
        )

    tui = live_tui.LiveTUI(SimpleNamespace(saved=True, recorded=[], save=save))
    monkeypatch.setattr(tui, "_info", lambda msg, raw="": None)

    tui._cmd_save("TC mod csv")
    tui._cmd_save("TC mod yaml")
    tui._cmd_save("TC mod yaml")

    assert appends == [
        (SaveFormat.CSV, False),
        (SaveFormat.YAML, False),
        (SaveFormat.YAML, True),
    ]
