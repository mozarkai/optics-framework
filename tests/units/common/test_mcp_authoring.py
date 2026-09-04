"""Unit tests for optics_framework.helper.mcp_authoring.

Recording buffer + canonical-CSV suite store. Pure/filesystem only (no fastmcp,
no device): the store is rooted at a tmp_path per test.
"""

import csv
import os

import pytest

from optics_framework.helper import mcp_authoring as A

pytestmark = pytest.mark.white_box


def _read(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --------------------------------------------------------------------------- #
# name / param helpers
# --------------------------------------------------------------------------- #
def test_title_slug_roundtrip():
    assert A.keyword_to_title("press_element") == "Press Element"
    assert A.title_to_slug("Press Element") == "press_element"


def test_sanitize_name_strips_unsafe():
    # Only chars outside [A-Za-z0-9_ -] are dropped (internal spaces are kept, like live.py).
    assert A.sanitize_name("Set-Alarm_22:00!") == "Set-Alarm_2200"
    assert A.sanitize_name("  Login  ") == "Login"


def test_format_step_params_positional_and_kwargs():
    # (name, default_str); None default_str means required (bare positional).
    specs = [("element", None), ("repeat", "1"), ("index", "None")]
    assert A.format_step_params(specs, {"element": "Login", "index": "2"}) == ["Login", "index=2"]
    assert A.format_step_params(specs, {"element": "Login", "repeat": "3"}) == ["Login", "repeat=3"]


def test_format_step_params_drops_values_equal_to_default():
    # The MCP boundary forwards every default; a value equal to its default is noise.
    specs = [("element", None), ("repeat", "1"), ("aoi_x", "0")]
    assert A.format_step_params(
        specs, {"element": "Login", "repeat": "1", "aoi_x": "0"}
    ) == ["Login"]


def test_variable_helpers():
    assert A.apply_variables(["22:00", "Save"], {"22:00": "time"}) == ["${time}", "Save"]
    assert A.resolve_variables(["text=${msg}", "${el}"], {"msg": "Hi", "el": "Button"}) == [
        "text=Hi",
        "Button",
    ]
    assert A.referenced_variables(["${a}", "x=${b}", "${a}"]) == ["a", "b"]


def test_resolve_variables_missing_raises():
    with pytest.raises(KeyError):
        A.resolve_variables(["${missing}"], {})


# --------------------------------------------------------------------------- #
# recording
# --------------------------------------------------------------------------- #
def test_session_recorder_lifecycle():
    rec = A.SessionRecorder()
    assert not rec.active
    rec.start()
    rec.append(A.RecordedStep("press_element", ["A"]))
    rec.append(A.RecordedStep("enter_text", ["B", "hi"]))
    assert rec.status()["step_count"] == 2
    rec.edit(0, "Swipe", ["left"])
    assert rec.steps[0].keyword == "swipe" and rec.steps[0].params == ["left"]
    rec.remove(0)
    assert rec.status()["step_count"] == 1
    rec.start()  # restart clears by default
    assert rec.status()["step_count"] == 0


def test_recorder_remove_out_of_range():
    rec = A.SessionRecorder()
    with pytest.raises(IndexError):
        rec.remove(0)


def test_recorder_registry_records_only_when_active():
    reg = A.RecorderRegistry()
    reg.record("s1", A.RecordedStep("press_element", ["A"]))  # no recorder yet -> no-op
    assert reg.peek("s1") is None
    reg.get("s1").start()
    reg.record("s1", A.RecordedStep("press_element", ["A"]))
    assert reg.get("s1").status()["step_count"] == 1
    reg.get("s1").stop()
    reg.record("s1", A.RecordedStep("swipe", ["x"]))  # inactive -> dropped
    assert reg.get("s1").status()["step_count"] == 1
    reg.discard("s1")
    assert reg.peek("s1") is None


# --------------------------------------------------------------------------- #
# suite store
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return A.SuiteStore(str(tmp_path))


def test_save_test_case_writes_canonical_csvs(tmp_path):
    store = _store(tmp_path)
    steps = [
        A.RecordedStep("launch_app", []),
        A.RecordedStep("enter_text", ["${El}", "22:00"]),
        A.RecordedStep("press_element", ["Save", "index=2"]),
    ]
    result = store.save_test_case("Set Alarm", steps, variables={"El": "text=Time", "time": "22:00"})
    assert result["step_count"] == 3

    modules = _read(store.modules_path)
    assert modules[0]["module_name"] == "Set Alarm"
    assert modules[0]["module_step"] == "Launch App"
    # the "22:00" literal became ${time}; the ${El} passthrough stays
    row = next(r for r in modules if r["module_step"] == "Enter Text")
    assert row["param_1"] == "${El}" and row["param_2"] == "${time}"
    press = next(r for r in modules if r["module_step"] == "Press Element")
    assert press["param_2"] == "index=2"

    cases = _read(store.test_cases_path)
    assert cases == [{"test_case": "Set Alarm", "test_step": "Set Alarm"}]

    elements = {r["Element_Name"]: r["Element_ID"] for r in _read(store.elements_path)}
    assert elements == {"El": "text=Time", "time": "22:00"}


def test_save_test_case_conflict_and_overwrite(tmp_path):
    store = _store(tmp_path)
    store.save_test_case("Login", [A.RecordedStep("press_element", ["A"])])
    with pytest.raises(A.SuiteConflictError):
        store.save_test_case("Login", [A.RecordedStep("press_element", ["B"])])
    # overwrite replaces the module rows rather than appending
    store.save_test_case("Login", [A.RecordedStep("press_element", ["B"])], overwrite=True)
    modules = [r for r in _read(store.modules_path) if r["module_name"] == "Login"]
    assert len(modules) == 1 and modules[0]["param_1"] == "B"


def test_save_test_case_rejects_empty(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.save_test_case("Empty", [])
    with pytest.raises(ValueError):
        store.save_test_case("!!!", [A.RecordedStep("press_element", ["A"])])


def test_list_get_resolve(tmp_path):
    store = _store(tmp_path)
    store.save_test_case(
        "Set Alarm",
        [A.RecordedStep("enter_text", ["${El}", "22:00"])],
        variables={"El": "text=Time", "time": "22:00"},
    )
    listed = store.list_test_cases()
    assert listed == [{"test_case": "Set Alarm", "modules": ["Set Alarm"], "step_count": 1}]

    got = store.get_test_case("Set Alarm")
    assert got["variables"] == {"El": "text=Time", "time": "22:00"}

    # override wins over the stored default; unspecified var falls back to default
    resolved = store.resolve_steps("Set Alarm", {"time": "07:30"})
    assert resolved == [("enter_text", ["text=Time", "07:30"])]


def test_special_char_values_roundtrip(tmp_path):
    # A value with a comma (csv-quoted), a backslash and a newline (escape_csv_value)
    # must replay exactly as recorded — same escape/unescape contract as the runner.
    store = _store(tmp_path)
    tricky = 'text=a,b\\c\nline2'
    store.save_test_case("Tricky", [A.RecordedStep("enter_text", ["${El}", tricky])],
                         variables={"El": "id=field"})
    resolved = store.resolve_steps("Tricky")
    assert resolved == [("enter_text", ["id=field", tricky])]


def test_get_and_delete_missing(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(A.SuiteNotFoundError):
        store.get_test_case("nope")
    with pytest.raises(A.SuiteNotFoundError):
        store.delete_test_case("nope")


def test_delete_removes_rows_and_suite_membership(tmp_path):
    store = _store(tmp_path)
    store.save_test_case("A", [A.RecordedStep("press_element", ["x"])])
    store.save_test_case("B", [A.RecordedStep("press_element", ["y"])])
    store.save_suite("S", ["A", "B"])
    removed = store.delete_test_case("A")
    assert removed["removed_test_case_rows"] == 1 and removed["removed_module_rows"] == 1
    assert [tc["test_case"] for tc in store.list_test_cases()] == ["B"]
    assert store.get_suite("S")["test_cases"] == ["B"]


def test_suites_crud_and_unknown_member(tmp_path):
    store = _store(tmp_path)
    store.save_test_case("A", [A.RecordedStep("press_element", ["x"])])
    with pytest.raises(A.SuiteNotFoundError):
        store.save_suite("S", ["A", "ghost"])
    store.save_suite("S", ["A"])
    assert store.list_suites() == [{"suite": "S", "test_cases": ["A"]}]
    with pytest.raises(A.SuiteNotFoundError):
        store.get_suite("missing")


def test_export_then_import_roundtrip(tmp_path):
    src_root = tmp_path / "ws"
    store = A.SuiteStore(str(src_root))
    store.save_test_case(
        "Set Alarm",
        [A.RecordedStep("enter_text", ["${El}", "22:00"])],
        variables={"El": "text=Time", "time": "22:00"},
    )
    dest = tmp_path / "exported"
    export = store.export_project(str(dest), "driver_sources: []\n")
    assert os.path.isfile(os.path.join(str(dest), "config.yaml"))
    assert any(name.endswith("modules.csv") for name in export["files"])

    other = A.SuiteStore(str(tmp_path / "ws2"))
    summary = other.import_project(str(dest))
    assert "modules" in summary
    assert [tc["test_case"] for tc in other.list_test_cases()] == ["Set Alarm"]
    # the imported module resolves the same way
    assert other.resolve_steps("Set Alarm", {"time": "07:30"}) == [
        ("enter_text", ["text=Time", "07:30"])
    ]


def test_import_empty_source_raises(tmp_path):
    store = A.SuiteStore(str(tmp_path / "ws"))
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError):
        store.import_project(str(empty))


def test_import_skips_optics_mcp_dir(tmp_path):
    # A store's own .optics_mcp/suites.json must not be re-imported as a project CSV.
    store = A.SuiteStore(str(tmp_path / "ws"))
    store.save_test_case("A", [A.RecordedStep("press_element", ["x"])])
    store.save_suite("S", ["A"])
    found = A._discover_project_csvs(str(tmp_path / "ws"))
    assert all(".optics_mcp" not in path for bucket in found.values() for path in bucket)
