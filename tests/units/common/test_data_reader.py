"""Tests for common/runner/data_reader.py — the CSV and YAML data readers.

Covers CSV escape/unescape (element IDs and module params), the CSV and YAML
readers for test cases / modules / elements, error-definition parsing, YAML API
data parsing + merge, and the merge_dicts duplicate-key helper.
"""
import pytest

from optics_framework.common.models import ApiData
from optics_framework.common.runner.data_reader import (
    CSVDataReader,
    YAMLDataReader,
    merge_dicts,
)
from optics_framework.common.utils import escape_csv_value, unescape_csv_value


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


# --------------------------------------------------------------------------- #
# CSVDataReader                                                                #
# --------------------------------------------------------------------------- #

class TestCSVDataReader:
    reader = CSVDataReader()

    def test_read_test_cases_groups_and_skips_blank(self, tmp_path):
        path = _write(tmp_path, "tc.csv", "test_case,test_step\nT1,M1\nT1,M2\n,skip\nT2,\n")
        assert self.reader.read_test_cases(path) == {"T1": ["M1", "M2"]}

    def test_read_modules_collects_params(self, tmp_path):
        path = _write(tmp_path, "m.csv", "module_name,module_step,param_1,param_2\nM,Enter Text,${f},hi\n")
        assert self.reader.read_modules(path) == {"M": [("Enter Text", ["${f}", "hi"])]}

    def test_read_modules_skips_rows_missing_name_or_step(self, tmp_path):
        path = _write(tmp_path, "m.csv", "module_name,module_step,param_1\nM,Launch App,\n,Sleep,1\nM,,2\n")
        assert self.reader.read_modules(path) == {"M": [("Launch App", [])]}

    def test_read_elements_supports_multiple_ids_for_fallback(self, tmp_path):
        path = _write(
            tmp_path, "e.csv",
            "Element_Name,Element_ID_xpath,Element_ID\nlogin,//button,loginBtn\n",
        )
        assert self.reader.read_elements(path) == {"login": ["//button", "loginBtn"]}

    def test_read_elements_none_path_returns_empty(self):
        assert self.reader.read_elements(None) == {}

    def test_read_elements_unescapes_newline_in_id(self, tmp_path):
        path = _write(
            tmp_path, "e.csv",
            'Element_Name,Element_ID\n"icici","//node[@desc=""I\\nBank""]"\n',
        )
        assert self.reader.read_elements(path)["icici"] == ['//node[@desc="I\nBank"]']

    def test_read_modules_unescapes_newline_in_param(self, tmp_path):
        path = _write(tmp_path, "m.csv", 'module_name,module_step,param_1\nm1,Get Text,"//*[@d=""A\\nB""]"\n')
        assert self.reader.read_modules(path)["m1"] == [("Get Text", ['//*[@d="A\nB"]'])]

    def test_read_error_definitions_parses_and_skips_incomplete(self, tmp_path):
        path = _write(
            tmp_path, "err.csv",
            "error_code,match_string,description,severity\n"
            "E1,Session expired,Auth error,high\n"
            ",no code,skipped,low\n"
            "E2,,skipped too,low\n",
        )
        result = self.reader.read_error_definitions(path)
        assert result == {
            "E1": {"match_string": "Session expired", "description": "Auth error", "severity": "high"}
        }


# --------------------------------------------------------------------------- #
# YAMLDataReader                                                               #
# --------------------------------------------------------------------------- #

class TestYAMLDataReader:
    reader = YAMLDataReader()

    def test_read_file_swallows_malformed_yaml(self, tmp_path):
        path = _write(tmp_path, "bad.yaml", "key: [unclosed\n  : :\n")
        assert self.reader.read_file(path) == {}

    def test_read_test_cases(self, tmp_path):
        path = _write(tmp_path, "tc.yaml", "Test Cases:\n  - Login:\n      - M1\n      - M2\n")
        assert self.reader.read_test_cases(path) == {"Login": ["M1", "M2"]}

    def test_read_modules_splits_on_variable(self, tmp_path):
        path = _write(tmp_path, "m.yaml", "Modules:\n  - M:\n      - Press Element ${btn}\n      - Sleep\n")
        assert self.reader.read_modules(path) == {
            "M": [("Press Element", ["${btn}"]), ("Sleep", [])]
        }

    @pytest.mark.parametrize(
        "step, expected",
        [
            ("Press Element ${btn}", ("Press Element", ["${btn}"])),
            ("Enter Text ${f} hello", ("Enter Text", ["${f}", "hello"])),
            ("Sleep", ("Sleep", [])),
            ("", ("", [])),
        ],
    )
    def test_parse_module_step(self, step, expected):
        assert self.reader._parse_module_step(step) == expected

    def test_read_elements_single_and_list_values(self, tmp_path):
        path = _write(
            tmp_path, "e.yaml",
            "Elements:\n  single: loginBtn\n  fallback:\n    - //a\n    - //b\n",
        )
        assert self.reader.read_elements(path) == {
            "single": ["loginBtn"],
            "fallback": ["//a", "//b"],
        }

    def test_read_elements_none_path_returns_empty(self):
        assert self.reader.read_elements(None) == {}

    def test_read_api_data_parses_collection(self, tmp_path):
        path = _write(
            tmp_path, "api.yaml",
            "api:\n"
            "  collections:\n"
            "    col1:\n"
            "      name: C1\n"
            "      base_url: http://x\n"
            "      apis:\n"
            "        a1:\n"
            "          name: A1\n"
            "          endpoint: /a\n"
            "          request:\n"
            "            method: GET\n",
        )
        api_data = self.reader.read_api_data(path)
        assert isinstance(api_data, ApiData)
        assert api_data.collections["col1"].base_url == "http://x"

    def test_read_api_data_invalid_structure_raises(self, tmp_path):
        path = _write(tmp_path, "api.yaml", "api:\n  collections:\n    col1:\n      missing: required\n")
        with pytest.raises(ValueError, match="Invalid API data structure"):
            self.reader.read_api_data(path)

    def test_read_api_data_merges_into_existing(self, tmp_path):
        first = _write(
            tmp_path, "a.yaml",
            "api:\n  collections:\n    c1:\n      name: C1\n      base_url: http://x\n"
            "      apis:\n        a1:\n          name: A1\n          endpoint: /a\n          request:\n            method: GET\n",
        )
        second = _write(
            tmp_path, "b.yaml",
            "api:\n  collections:\n    c2:\n      name: C2\n      base_url: http://y\n"
            "      apis:\n        a2:\n          name: A2\n          endpoint: /b\n          request:\n            method: POST\n",
        )
        existing = self.reader.read_api_data(first)
        merged = self.reader.read_api_data(second, existing_api_data=existing)
        assert set(merged.collections) == {"c1", "c2"}


# --------------------------------------------------------------------------- #
# escape/unescape helpers (used by the readers and by output round-trips)      #
# --------------------------------------------------------------------------- #

class TestEscapeCsvValue:
    @pytest.mark.parametrize(
        "raw, escaped",
        [
            ("a\nb", "a\\nb"),
            ("a\tb", "a\\tb"),
            ("a\rb", "a\\rb"),
            ("a\\b", "a\\\\b"),
            ("a\\nc", "a\\\\nc"),  # backslash escaped first, so backslash+n != newline
            ("", ""),
        ],
    )
    def test_escape(self, raw, escaped):
        assert escape_csv_value(raw) == escaped

    @pytest.mark.parametrize("bad", [None, 123])
    def test_escape_rejects_non_string(self, bad):
        with pytest.raises(TypeError, match="expects str, got"):
            escape_csv_value(bad)

    @pytest.mark.parametrize("bad", [None, 123])
    def test_unescape_rejects_non_string(self, bad):
        with pytest.raises(TypeError, match="expects str, got"):
            unescape_csv_value(bad)


class TestEscapeUnescapeInverses:
    @pytest.mark.parametrize(
        "escaped",
        ['//*[@desc="A\\nB"]', "a\\\\nc", "I\\nIcici Bank Limited", "a\\tb\\rc", "plain"],
    )
    def test_escape_of_unescape_is_identity(self, escaped):
        assert escape_csv_value(unescape_csv_value(escaped)) == escaped

    @pytest.mark.parametrize("raw", ["a\nb", "a\tb", "a\rb", "a\\nc", '//*[@d="A\nB"]', ""])
    def test_unescape_of_escape_is_identity(self, raw):
        assert unescape_csv_value(escape_csv_value(raw)) == raw


# --------------------------------------------------------------------------- #
# merge_dicts                                                                  #
# --------------------------------------------------------------------------- #

class TestMergeDicts:
    def test_merges_disjoint_keys(self):
        assert merge_dicts({"a": 1}, {"b": 2}, "modules") == {"a": 1, "b": 2}

    def test_second_source_wins_on_duplicate(self):
        assert merge_dicts({"a": 1}, {"a": 2}, "modules") == {"a": 2}
