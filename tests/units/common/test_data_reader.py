import pytest

from optics_framework.common.runner.data_reader import (
    CSVDataReader,
    YAMLDataReader,
    merge_dicts,
)


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestCSVDataReader:
    reader = CSVDataReader()

    def test_read_modules_skips_rows_missing_name_or_step(self, tmp_path):
        path = _write(tmp_path, "m.csv", "module_name,module_step,param_1\nM,Launch App,\n,Sleep,1\nM,,2\n")
        assert self.reader.read_modules(path) == {"M": [("Launch App", [])]}

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

    def test_parse_module_step_prefers_a_module_of_the_same_name(self):
        assert self.reader._parse_module_step("Sleep Well", {"Sleep Well"}) == ("Sleep Well", [])
        assert self.reader._parse_module_step("Sleep Well") == ("Sleep", ["Well"])

    def test_read_modules_takes_module_names_from_other_files(self, tmp_path):
        caller = _write(tmp_path, "a.yaml", "Modules:\n  - Caller:\n      - Sleep Well\n")
        defined = _write(tmp_path, "b.yaml", "Modules:\n  - Sleep Well:\n      - Sleep 1\n")
        names = self.reader.read_module_names(caller) | self.reader.read_module_names(defined)
        assert names == {"Caller", "Sleep Well"}
        assert self.reader.read_modules(caller, names) == {"Caller": [("Sleep Well", [])]}

    def test_read_elements_single_and_list_values(self, tmp_path):
        path = _write(
            tmp_path, "e.yaml",
            "Elements:\n  single: loginBtn\n  fallback:\n    - //a\n    - //b\n",
        )
        assert self.reader.read_elements(path) == {
            "single": ["loginBtn"],
            "fallback": ["//a", "//b"],
        }

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


class TestMergeDicts:
    def test_second_source_wins_on_duplicate(self):
        assert merge_dicts({"a": 1}, {"a": 2}, "modules") == {"a": 2}
