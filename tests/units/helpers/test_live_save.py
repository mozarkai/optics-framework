import csv
import os
from types import SimpleNamespace

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.common.models import ElementData
from optics_framework.helper.live import LiveController, SaveConflictError


class _Controller(LiveController):
    def __init__(self, folder: str, elements: ElementData | None = None):
        self.folder_path = folder
        self._artifacts_dir = os.path.join(folder, "no_artifacts")  # absent -> no snapshot
        self.recorded = []
        self.saved = False
        # save() merges these into the project's tracked elements CSV; the flag
        # short-circuits ensure_elements_loaded's own project walk.
        self._elements_loaded = True
        self._agent_elements = set()
        self._secret_names = set()
        self.session = SimpleNamespace(elements=elements if elements is not None else ElementData())


@pytest.fixture
def c(tmp_path):
    return _Controller(str(tmp_path))


def _rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_duplicate_test_case_name_raises_conflict(c):
    c.recorded = [("launch_app", [])]
    c.save("Shared TC", "mod_a")

    c.recorded = [("scroll", [])]
    with pytest.raises(SaveConflictError) as exc:
        c.save("Shared TC", "mod_b")
    assert ("test case", "Shared TC") in exc.value.conflicts


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


def _seed_elements_csv(tmp_path, content: str) -> str:
    """Create the scaffold-convention ``test_data/elements.csv``; returns its path."""
    elements_dir = tmp_path / "test_data"
    elements_dir.mkdir()
    elements_path = elements_dir / "elements.csv"
    elements_path.write_text(content, encoding="utf-8")
    return str(elements_path)


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
