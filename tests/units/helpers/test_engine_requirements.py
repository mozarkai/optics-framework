"""Unit tests for ``optics_framework/helper/engine_requirements.py``.

The condition under test — a ``config.yaml`` entry is ``enabled: true`` while
its Python package was never installed — used to be reported three different
ways by three commands. ``TestOneConditionOneSeverity`` is the regression guard
for that: it drives doctor, generate and the runner gate through the same fake
and asserts each reports the severity its job warrants, in identical words.

Package presence is faked at ``engine_requirements.version``, so the suite never
needs an engine installed (or absent) to exercise either branch.
"""
from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError
from unittest.mock import patch

import pytest

from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.helper import doctor, generate
from optics_framework.helper import execute as execute_module
from optics_framework.helper.engine_requirements import (
    MissingEngine,
    enabled_keys,
    missing_engines,
    report,
)
from optics_framework.helper.setup import ALL_ENGINES, engine_for

pytestmark = pytest.mark.white_box

MODULE = "optics_framework.helper.engine_requirements"

EASYOCR = ALL_ENGINES["EasyOCR"]
PYTESSERACT = ALL_ENGINES["Pytesseract"]


def _absent(*packages: str):
    """Patch package lookup so only ``packages`` are missing."""
    def fake_version(package):
        if package in packages:
            raise PackageNotFoundError(package)
        return "1.0"
    return patch(f"{MODULE}.version", side_effect=fake_version)


def _yaml_config(**sections) -> dict:
    """A parsed config.yaml: ``section=[("key", True), ...]``."""
    return {
        name: [{key: {"enabled": enabled}} for key, enabled in entries]
        for name, entries in sections.items()
    }


# --------------------------------------------------------------------------- #
# enabled_keys                                                                 #
# --------------------------------------------------------------------------- #

class TestEnabledKeys:
    def test_collects_every_engine_section(self):
        data = _yaml_config(
            driver_sources=[("appium", True)],
            elements_sources=[("appium_page_source", True)],
            text_detection=[("easyocr", True)],
            image_detection=[("templatematch", True)],
            llm_models=[("gemini", True)],
        )
        assert enabled_keys(data) == [
            "appium", "appium_page_source", "easyocr", "templatematch", "gemini"]

    def test_skips_disabled_entries(self):
        data = _yaml_config(text_detection=[("easyocr", False), ("pytesseract", True)])
        assert enabled_keys(data) == ["pytesseract"]

    def test_reads_a_config_model_the_same_as_parsed_yaml(self):
        """The runners hold a ``Config``; doctor and generate hold a plain dict."""
        model = Config(text_detection=[{"easyocr": DependencyConfig(enabled=True)}])
        assert enabled_keys(model) == ["easyocr"]

    def test_config_model_defaults_enable_nothing(self):
        assert enabled_keys(Config()) == []

    def test_tolerates_malformed_entries(self):
        data = {"text_detection": ["easyocr", None, {"pytesseract": None}],
                "driver_sources": None}
        assert enabled_keys(data) == []

    def test_deduplicates_a_key_listed_twice(self):
        data = _yaml_config(
            text_detection=[("easyocr", True)], image_detection=[("easyocr", True)])
        assert enabled_keys(data) == ["easyocr"]


# --------------------------------------------------------------------------- #
# missing_engines                                                              #
# --------------------------------------------------------------------------- #

class TestMissingEngines:
    def test_installed_engine_is_not_reported(self):
        data = _yaml_config(text_detection=[("easyocr", True)])
        with _absent():
            assert missing_engines(data) == []

    def test_missing_engine_is_reported_with_its_package(self):
        data = _yaml_config(text_detection=[("easyocr", True)])
        with _absent("easyocr"):
            missing = missing_engines(data)
        assert [m.config_key for m in missing] == ["easyocr"]
        assert missing[0].packages == ("easyocr",)
        assert missing[0].engine is EASYOCR

    def test_disabled_engine_is_never_reported(self):
        data = _yaml_config(text_detection=[("easyocr", False)])
        with _absent("easyocr"):
            assert missing_engines(data) == []

    def test_only_the_absent_packages_of_an_extra_are_listed(self):
        """pytesseract's extra pulls pillow too; a partial install must show
        exactly what is missing, not the whole extra."""
        data = _yaml_config(text_detection=[("pytesseract", True)])
        with _absent("pillow"):
            missing = missing_engines(data)
        assert missing[0].packages == ("pillow",)

    @pytest.mark.parametrize(
        "key", ["templatematch", "remote_ocr", "remote_oir", "appium_page_source"])
    def test_keys_without_an_extra_are_ignored(self, key):
        """Bundled detectors and element sources install nothing of their own —
        flagging them would send the user to a `setup --install` that fails."""
        data = _yaml_config(image_detection=[(key, True)])
        with _absent("easyocr", "pytesseract", "appium-python-client"):
            assert missing_engines(data) == []

    def test_reports_every_missing_engine_in_config_order(self):
        data = _yaml_config(
            driver_sources=[("appium", True)], text_detection=[("easyocr", True)])
        with _absent("appium-python-client", "easyocr"):
            missing = missing_engines(data)
        assert [m.config_key for m in missing] == ["appium", "easyocr"]

    def test_reads_a_config_model(self):
        model = Config(text_detection=[{"easyocr": DependencyConfig(enabled=True)}])
        with _absent("easyocr"):
            assert [m.config_key for m in missing_engines(model)] == ["easyocr"]


# --------------------------------------------------------------------------- #
# Wording                                                                      #
# --------------------------------------------------------------------------- #

class TestWording:
    def test_hint_is_the_engine_backend_install_command(self):
        """One string, owned by the backend — so a renamed extra cannot leave a
        stale command behind in any of the four commands that print it."""
        item = MissingEngine("easyocr", EASYOCR, ("easyocr",))
        assert item.hint == EASYOCR.install_hint == "optics setup --install easyocr"

    def test_detail_names_config_yaml_and_the_absent_packages(self):
        item = MissingEngine("pytesseract", PYTESSERACT, ("pytesseract", "pillow"))
        assert "enabled in config.yaml" in item.detail
        assert "pytesseract, pillow" in item.detail

    def test_report_carries_each_engine_detail_hint_and_the_config_path(self):
        missing = [MissingEngine("easyocr", EASYOCR, ("easyocr",))]
        text = report(missing, "/projects/demo")
        assert missing[0].detail in text
        assert missing[0].hint in text
        assert os.path.join("/projects/demo", "config.yaml") in text

    def test_report_counts_the_engines(self):
        missing = [MissingEngine("easyocr", EASYOCR, ("easyocr",)),
                   MissingEngine("pytesseract", PYTESSERACT, ("pytesseract",))]
        assert report(missing, "/projects/demo").startswith("2 engines")
        assert report(missing[:1], "/projects/demo").startswith("1 engine")


class TestEngineFor:
    @pytest.mark.parametrize(
        "token", ["easyocr", "EasyOCR", "google-vision", "google_vision", "Appium"])
    def test_resolves_config_keys_and_display_names(self, token):
        assert engine_for(token) is not None

    @pytest.mark.parametrize("token", ["templatematch", "camera_screenshot", ""])
    def test_returns_none_for_tokens_with_no_extra(self, token):
        assert engine_for(token) is None


# --------------------------------------------------------------------------- #
# One condition, one severity, one wording (issue #497)                        #
# --------------------------------------------------------------------------- #

_CONFIG_WITH_MISSING_OCR = """\
driver_sources:
  - appium:
      enabled: true
      url: http://127.0.0.1:4723
      capabilities:
        deviceName: emu-1
        platformName: Android
        appPackage: com.example.app
        appActivity: com.example.MainActivity
elements_sources:
  - appium_find_element:
      enabled: true
text_detection:
  - easyocr:
      enabled: true
"""


def _project(tmp_path):
    (tmp_path / "test_cases").mkdir()
    (tmp_path / "modules").mkdir()
    (tmp_path / "test_cases" / "test_cases.csv").write_text(
        "test_case,test_step\nTC One,Launch App\n")
    (tmp_path / "modules" / "modules.csv").write_text(
        "module_name,module_step\nLaunch App,Launch App,\n")
    (tmp_path / "elements.csv").write_text("Element_Name,Element_ID\nBox,text=Box\n")
    (tmp_path / "config.yaml").write_text(_CONFIG_WITH_MISSING_OCR)
    return str(tmp_path)


@pytest.fixture
def condition():
    """The one ``MissingEngine`` every command below is looking at."""
    return MissingEngine("easyocr", EASYOCR, ("easyocr",))


class TestOneConditionOneSeverity:
    def test_doctor_warns_and_never_fails(self, tmp_path, condition):
        folder = _project(tmp_path)
        with _absent("easyocr"):
            rows = doctor.validate_project(folder)
        row = next(r for r in rows if r.name == "config: easyocr engine")
        assert row.status == "warn"
        assert row.detail == condition.detail
        assert row.hint == condition.hint
        assert not any(r.status == "fail" for r in rows)

    def test_doctor_no_longer_calls_the_project_runnable(self, tmp_path):
        folder = _project(tmp_path)
        with _absent("easyocr"):
            rows = doctor.validate_project(folder)
        assert not any(r.name == "config: project" for r in rows)

    def test_doctor_lists_it_among_the_blocking_hints(self, tmp_path, condition):
        """An engine the project enables is not an optional extra, so it belongs
        in the closing call-to-action rather than the "good enough" line."""
        folder = _project(tmp_path)
        with _absent("easyocr"), \
                patch.object(doctor, "check_mobile", return_value=[]), \
                patch.object(doctor, "check_web", return_value=[]):
            diagnosis = doctor.diagnose(folder)
        assert condition.hint in diagnosis.blocking
        assert diagnosis.ready is False

    def test_generate_warns_and_still_generates(self, tmp_path, caplog, condition):
        folder = _project(tmp_path)
        with _absent("easyocr"), caplog.at_level("WARNING", logger=generate.__name__):
            generate.generate_test_file(folder, framework="pytest")
        assert condition.detail in caplog.text
        assert condition.hint in caplog.text
        assert (tmp_path / "generated" / "Tests").exists()

    @pytest.mark.parametrize(
        "main", [execute_module.execute_main, execute_module.dryrun_main])
    def test_runners_stop_before_the_session(self, tmp_path, capsys, condition, main):
        folder = _project(tmp_path)
        with _absent("easyocr"), \
                patch.object(execute_module, "SessionManager") as session_manager:
            with pytest.raises(SystemExit) as exc:
                main(folder)
        assert exc.value.code == 1
        session_manager.assert_not_called()
        err = capsys.readouterr().err
        assert condition.detail in err
        assert condition.hint in err
        assert "E0601" not in err

    def test_runners_proceed_once_the_engine_is_installed(self, tmp_path):
        folder = _project(tmp_path)
        runner = execute_module.BaseRunner.__new__(execute_module.BaseRunner)
        runner.folder_path = folder
        runner.config = Config(
            text_detection=[{"easyocr": DependencyConfig(enabled=True)}])
        with _absent():
            runner._require_installed_engines()
