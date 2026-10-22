"""Tests for common/runner/data_reader.py — the CSV and YAML data readers.

Covers CSV escape/unescape (element IDs and module params), the CSV and YAML
readers for test cases / modules / elements, error-definition parsing, YAML API
data parsing + merge, and the merge_dicts duplicate-key helper.
"""
import pytest

from optics_framework.common.models import ApiData
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

    def test_equals_locators_stay_positional(self):
        pos, kw = DataReader.split_params_by_signature(
            self._method, ["text=Example Domain", "css=.btn", "xpath=//a[@id='x']"])
        assert pos == ["text=Example Domain", "css=.btn", "xpath=//a[@id='x']"]
        assert kw == {}

    def test_real_keyword_args_are_split_off(self):
        pos, kw = DataReader.split_params_by_signature(
            self._method, ["text=OK", "event_name=tap", "5"])
        assert pos == ["text=OK", "5"]
        assert kw == {"event_name": "tap"}

    def test_params_without_equals_are_untouched(self):
        pos, kw = DataReader.split_params_by_signature(
            self._method, ["${Heading}", "https://example.com"])
        assert pos == ["${Heading}", "https://example.com"]
        assert kw == {}

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

    def test_read_modules_collects_params(self, tmp_path):
        path = _write(tmp_path, "m.csv", "module_name,module_step,param_1,param_2\nM,Enter Text,${f},hi\n")
        assert self.reader.read_modules(path) == {"M": [("Enter Text", ["${f}", "hi"])]}

    def test_read_modules_skips_rows_missing_name_or_step(self, tmp_path):
        path = _write(tmp_path, "m.csv", "module_name,module_step,param_1\nM,Launch App,\n,Sleep,1\nM,,2\n")
        assert self.reader.read_modules(path) == {"M": [("Launch App", [])]}

    def test_read_module_names_does_not_parse_steps(self, tmp_path, monkeypatch):
        """The names pass reads the column only, so read_modules parses and warns once, later."""
        monkeypatch.setattr(
            CSVDataReader,
            "read_modules",
            lambda self, file_path, module_names=None: pytest.fail("names pass parsed the file"),
        )
        path = _write(tmp_path, "m.csv", "module_name,module_step\nM,Launch App\n,Bad,row\n")
        assert self.reader.read_module_names(path) == {"M"}

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
            # The catalogue ends the keyword name, so a first param that is a plain value is
            # no longer swallowed into it.
            ("Swipe 1000 300 up", ("Swipe", ["1000", "300", "up"])),
            ("Sleep 5", ("Sleep", ["5"])),
            ("Swipe By Percentage 50 50 20", ("Swipe By Percentage", ["50", "50", "20"])),
            # The longest run wins, or "Enter Text hi" would parse as "Enter".
            ("Enter Text hi", ("Enter Text", ["hi"])),
            # A facade alias the runtime map lacks is a catalogue name too, or the line
            # is dispatched to "Press Element" with "With Index ..." as its params.
            ("Press Element With Index ${el} 2", ("Press Element With Index", ["${el}", "2"])),
            # Slug form is a keyword too, whatever the params look like.
            ("press_element ${btn}", ("press_element", ["${btn}"])),
            ("enter_text ${f} hello", ("enter_text", ["${f}", "hello"])),
            ("swipe_by_percentage 50 50 20", ("swipe_by_percentage", ["50", "50", "20"])),
            ("swipe 1000 300 up", ("swipe", ["1000", "300", "up"])),
            # A name the catalogue does not know still reports the name alone, so the runner's
            # "did you mean" hint has something to work with.
            ("Press Elemnt ${btn}", ("Press Elemnt", ["${btn}"])),
            # Failing both, the line is returned whole — how a step referencing another module
            # has always reached the runner.
            ("Login Flow", ("Login Flow", [])),
        ],
    )
    def test_parse_module_step(self, step, expected):
        assert self.reader._parse_module_step(step) == expected

    @pytest.mark.parametrize(
        "step, expected",
        [
            # The only way a param can hold a space: a value written entirely in quotes.
            ('Enter Text ${f} text="two words"', ("Enter Text", ["${f}", "text=two words"])),
            ("Enter Text ${f} text='two words'", ("Enter Text", ["${f}", "text=two words"])),
            ('Enter Text ${f} "two words"', ("Enter Text", ["${f}", "two words"])),
            # A locator's own quotes are inside the value, so they stay — this is what
            # `shlex.split` would have eaten.
            (
                'Press Element //button[@id="save"] repeat=2',
                ("Press Element", ['//button[@id="save"]', "repeat=2"]),
            ),
            ('Press Element //*[@text="a b"]', ("Press Element", ['//*[@text="a b"]'])),
            # Unquoted lines split as they always have.
            ("Enter Text ${f} plain words here", ("Enter Text", ["${f}", "plain", "words", "here"])),
            # An apostrophe is not an unbalanced quote, so a double-quoted value keeps its
            # space, and the other quote character inside a value is not special either.
            (
                'Enter Text ${f} text="Bob\'s file"',
                ("Enter Text", ["${f}", "text=Bob's file"]),
            ),
            (
                "Enter Text ${f} text='say \"hi\" now'",
                ("Enter Text", ["${f}", 'text=say "hi" now']),
            ),
            # An unbalanced quote falls back rather than dropping the quote and splitting.
            ('Enter Text ${f} text="abc', ("Enter Text", ["${f}", 'text="abc'])),
            ("Enter Text ${f} it's fine", ("Enter Text", ["${f}", "it's", "fine"])),
            # A value gives its quotes up whatever is inside it, not only when it holds a
            # space. An editor writing every param as `name="value"` depends on this: the
            # keyword must be handed `2`, never the three characters `"2"`.
            ('Press Element ${el} index="2"', ("Press Element", ["${el}", 'index=2'])),
            ('Enter Text ${f} text="hello"', ("Enter Text", ["${f}", "text=hello"])),
            ('Press Element "Login"', ("Press Element", ["Login"])),
            # And the quotes are what carry the spaces, so they survive the split intact.
            ('Enter Text ${f} text=" "', ("Enter Text", ["${f}", "text= "])),
            ('Enter Text ${f} text=""', ("Enter Text", ["${f}", "text="])),
            # A `${...}` inside a quoted value does not end the keyword name, and neither
            # does one inside a `name=value`: the split is on tokens, not on the `${`.
            (
                "Press Element text=Login timeout=${t}",
                ("Press Element", ["text=Login", "timeout=${t}"]),
            ),
            ('Enter Text hello text="${x}"', ("Enter Text", ["hello", "text=${x}"])),
            # A literal param before a variable one is still a param.
            (
                "Swipe By Percentage 50 50 ${dur}",
                ("Swipe By Percentage", ["50", "50", "${dur}"]),
            ),
        ],
    )
    def test_parse_module_step_honours_quoted_params(self, step, expected):
        assert self.reader._parse_module_step(step) == expected

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

    @pytest.mark.parametrize(
        "step, expected",
        [
            # The guard only fires on a bare word, and only when the catalogue says so.
            # Nothing continues `scroll down`, and `scroll` holds one param fine.
            ("Scroll down", ("Scroll", ["down"])),
            ("Swipe up", ("Swipe", ["up"])),
            # Nothing continues `press element login`, and `press element` takes ten.
            ("Press Element Login", ("Press Element", ["Login"])),
            ("Press Keycode ENTER", ("Press Keycode", ["ENTER"])),
            # A variadic keyword has no count to exceed.
            ("Run Loop MyModule 3", ("Run Loop", ["MyModule", "3"])),
            # The longer keyword spelt correctly is matched whole, params and all.
            (
                "Enter Text Using Keyboard hello",
                ("Enter Text Using Keyboard", ["hello"]),
            ),
            ("Enter Text Direct hello", ("Enter Text Direct", ["hello"])),
            # Too many params, but the first is not a word, so this stays an arity error
            # for the runner to report rather than an unknown keyword.
            ("Sleep 5 extra", ("Sleep", ["5", "extra"])),
        ],
    )
    def test_a_bare_word_param_is_still_a_param(self, step, expected):
        assert self.reader._parse_module_step(step) == expected

    def test_read_modules_keeps_a_quoted_value_without_a_space(self, tmp_path):
        """Through the public reader, because this is the form the yaml editor writes."""
        path = _write(
            tmp_path,
            "m.yaml",
            'Modules:\n  - M:\n      - Press Element ${btn} index="2"\n',
        )
        assert self.reader.read_modules(path) == {
            "M": [("Press Element", ["${btn}", "index=2"])]
        }

    def test_read_modules_keeps_a_quoted_space(self, tmp_path):
        path = _write(
            tmp_path,
            "m.yaml",
            'Modules:\n  - M:\n      - Enter Text ${f} text="two words"\n',
        )
        assert self.reader.read_modules(path) == {
            "M": [("Enter Text", ["${f}", "text=two words"])]
        }

    def test_parse_module_step_prefers_a_module_of_the_same_name(self):
        """A module may be named after a keyword's first word without being that keyword."""
        assert self.reader._parse_module_step("Sleep Well", {"Sleep Well"}) == ("Sleep Well", [])
        assert self.reader._parse_module_step("Sleep Well") == ("Sleep", ["Well"])

    def test_read_modules_keeps_a_literal_first_param(self, tmp_path):
        """The whole point, through the public reader: the params survive the round trip."""
        path = _write(
            tmp_path,
            "m.yaml",
            "Modules:\n  - M:\n      - Swipe 1000 300 up\n      - Press Element ${btn}\n",
        )
        assert self.reader.read_modules(path) == {
            "M": [("Swipe", ["1000", "300", "up"]), ("Press Element", ["${btn}"])]
        }

    def test_read_modules_takes_module_names_from_other_files(self, tmp_path):
        """A suite is split across files, so the name a step references may be defined in
        another one — without the project-wide set it would read as a keyword call."""
        caller = _write(tmp_path, "a.yaml", "Modules:\n  - Caller:\n      - Sleep Well\n")
        defined = _write(tmp_path, "b.yaml", "Modules:\n  - Sleep Well:\n      - Sleep 1\n")
        names = self.reader.read_module_names(caller) | self.reader.read_module_names(defined)
        assert names == {"Caller", "Sleep Well"}
        assert self.reader.read_modules(caller, names) == {"Caller": [("Sleep Well", [])]}

    def test_read_module_names_does_not_parse_steps(self, tmp_path, monkeypatch):
        """The names pass reads the keys only, so read_modules parses and warns once, later."""
        monkeypatch.setattr(
            YAMLDataReader,
            "read_modules",
            lambda self, file_path, module_names=None: pytest.fail("names pass parsed the file"),
        )
        path = _write(
            tmp_path, "m.yaml", "Modules:\n  - Empty:\n  - M:\n      - Swipe 1000 300 up\n"
        )
        assert self.reader.read_module_names(path) == {"Empty", "M"}

    def test_read_modules_lets_a_step_reference_a_module_named_like_a_keyword(self, tmp_path):
        """Module names come from the whole file, so one defined below is still recognised."""
        path = _write(
            tmp_path,
            "m.yaml",
            "Modules:\n  - Caller:\n      - Sleep Well\n  - Sleep Well:\n      - Sleep 1\n",
        )
        assert self.reader.read_modules(path) == {
            "Caller": [("Sleep Well", [])],
            "Sleep Well": [("Sleep", ["1"])],
        }

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


class TestMergeDicts:
    def test_merges_disjoint_keys(self):
        assert merge_dicts({"a": 1}, {"b": 2}, "modules") == {"a": 1, "b": 2}

    def test_second_source_wins_on_duplicate(self):
        assert merge_dicts({"a": 1}, {"a": 2}, "modules") == {"a": 2}
