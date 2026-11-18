"""Tests for common/runner/data_reader.py — the CSV and YAML data readers.

Covers CSV escape/unescape (element IDs and module params), the CSV and YAML
readers for test cases / modules / elements, error-definition parsing, YAML API
data parsing + merge, and the merge_dicts duplicate-key helper.
"""
import pytest

from optics_framework.common.runner.data_reader import (
    CSVDataReader,
    DataReader,
    YAMLDataReader,
    merge_dicts,
)
from optics_framework.common.utils import escape_csv_value, unescape_csv_value


def _write(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestSplitParamsBySignature:
    @staticmethod
    def _method(element, timeout="10", rule="all", event_name=None):
        """Stand-in with a validate_element-like signature (no ``text`` param)."""

    def test_unreadable_signature_falls_back_to_keyword(self):
        # A non-introspectable target keeps the historical behaviour: a
        # ``key=value`` token is treated as a keyword argument.
        pos, kw = DataReader.split_params_by_signature(object(), ["text=x"])
        assert pos == []
        assert kw == {"text": "x"}


class TestCSVDataReader:
    reader = CSVDataReader()

    def test_read_test_cases_groups_and_skips_blank(self, tmp_path):
        path = _write(tmp_path, "tc.csv", "test_case,test_step\nT1,M1\nT1,M2\n,skip\nT2,\n")
        assert self.reader.read_test_cases(path) == {"T1": ["M1", "M2"]}

    def test_read_modules_skips_rows_missing_name_or_step(self, tmp_path):
        path = _write(tmp_path, "m.csv", "module_name,module_step,param_1\nM,Launch App,\n,Sleep,1\nM,,2\n")
        assert self.reader.read_modules(path) == {"M": [("Launch App", [])]}

    def test_read_elements_none_path_returns_empty(self):
        assert self.reader.read_elements(None) == {}

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

    @pytest.mark.parametrize(
        "step, expected",
        [
            # A misspelt keyword whose first words spell a shorter one must not dispatch to
            # the shorter one: the params would be wrong and no error would be raised.
            # `swipe by` continues `swipe by percentage`, so `Swipe` is not the answer.
            ("Swipe By Percent ${x} ${y}", ("Swipe By Percent", ["${x}", "${y}"])),
            (
                "Enter Text Using Keybord ${f} hi",
                ("Enter Text Using Keybord", ["${f}", "hi"]),
            ),
            (
                "Press Element With Indx ${el} 2",
                ("Press Element With Indx", ["${el}", "2"]),
            ),
            # Nothing continues `scroll to`, but `scroll` takes two params and this leaves
            # it three — so the words are part of a name, not params.
            ("Scroll To Element foo", ("Scroll To Element foo", [])),
            # The fallback split ends the name at the token holding the `${...}`, not at
            # the `${` itself, or half a param is read as part of the keyword's name and
            # the "did you mean" hint has nothing usable to work with.
            ('Slep text="${x}"', ("Slep", ["text=${x}"])),
            ("Slep ${f} timeout=${t}", ("Slep", ["${f}", "timeout=${t}"])),
        ],
    )
    def test_a_misspelt_keyword_is_not_dispatched_to_its_shorter_prefix(self, step, expected):
        assert self.reader._parse_module_step(step) == expected

    def test_parse_module_step_prefers_a_module_of_the_same_name(self):
        """A module may be named after a keyword's first word without being that keyword."""
        assert self.reader._parse_module_step("Sleep Well", {"Sleep Well"}) == ("Sleep Well", [])
        assert self.reader._parse_module_step("Sleep Well") == ("Sleep", ["Well"])

    def test_read_modules_takes_module_names_from_other_files(self, tmp_path):
        """A suite is split across files, so the name a step references may be defined in
        another one — without the project-wide set it would read as a keyword call."""
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

    def test_read_elements_none_path_returns_empty(self):
        assert self.reader.read_elements(None) == {}

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


class TestEscapeCsvValue:

    @pytest.mark.parametrize("bad", [None, 123])
    def test_escape_rejects_non_string(self, bad):
        with pytest.raises(TypeError, match="expects str, got"):
            escape_csv_value(bad)

    @pytest.mark.parametrize("bad", [None, 123])
    def test_unescape_rejects_non_string(self, bad):
        with pytest.raises(TypeError, match="expects str, got"):
            unescape_csv_value(bad)


class TestEscapeUnescapeInverses:

    @pytest.mark.parametrize("raw", ["a\nb", "a\tb", "a\rb", "a\\nc", '//*[@d="A\nB"]', ""])
    def test_unescape_of_escape_is_identity(self, raw):
        assert unescape_csv_value(escape_csv_value(raw)) == raw


class TestMergeDicts:

    def test_second_source_wins_on_duplicate(self):
        assert merge_dicts({"a": 1}, {"a": 2}, "modules") == {"a": 2}
