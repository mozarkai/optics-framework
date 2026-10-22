"""Unit tests for the `optics live` `/save` workflow (``LiveController.save``).

These exercise the CSV serialisation directly, without a device or session: a
lightweight subclass overrides ``__init__`` to set only the attributes ``save``
touches (``folder_path``, ``_artifacts_dir``, ``recorded``, ``saved``, plus a
stub ``session`` holding the session's known elements). The output is validated
both structurally and by round-tripping through ``CSVDataReader`` so it stays
compatible with the batch runner.
"""
import csv
import os
from types import SimpleNamespace

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.models import ElementData
from optics_framework.common.runner.data_reader import CSVDataReader
from optics_framework.helper.live import LiveController, SaveConflictError, SaveResult

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
