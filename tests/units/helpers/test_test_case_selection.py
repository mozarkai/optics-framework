"""Test case selection: ``--include`` / ``--exclude`` on ``optics execute`` / ``optics dry_run``.

Three layers: ``select_test_cases`` (pure name matching), ``BaseRunner`` (command line
overrides config.yaml; strict on the command line, lenient for config.yaml), and the
argparse wiring in ``cli.py``. ``SessionManager`` / ``ExecutionEngine`` are patched,
so no driver or device is touched.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from optics_framework.common.error import Code, OpticsError
from optics_framework.helper import cli
from optics_framework.helper import execute as execute_module
from optics_framework.helper.execute import (
    BaseRunner,
    RunnerArgs,
    filter_test_cases,
    normalize_test_case_name,
    select_test_cases,
)

pytestmark = pytest.mark.white_box


CASES = {
    "Suite Setup": ["Prepare"],
    "Setup": ["Launch"],
    "Login Test": ["Log In"],
    "Checkout Test": ["Buy"],
    "Search Test": ["Search"],
    "Teardown": ["Close"],
    "Suite Teardown": ["Clean"],
}


def names(selection):
    return list(selection.test_cases)


class TestNormalize:
    @pytest.mark.parametrize("raw", [
        "Login Test", "login test", "LOGIN TEST", "lOgIn TeSt",
        "  Login Test  ", "Login   Test", "Login\tTest", "\nLogin Test\n",
    ])
    def test_case_and_whitespace_variants_collapse(self, raw):
        assert normalize_test_case_name(raw) == "login test"

    def test_casefold_handles_non_ascii(self):
        assert normalize_test_case_name("STRASSE") == normalize_test_case_name("Straße")

    def test_non_string_entries_from_yaml(self):
        assert normalize_test_case_name(123) == "123"


class TestSelectTestCases:
    def test_no_selection_keeps_everything(self):
        result = select_test_cases(CASES)
        assert names(result) == list(CASES)
        assert result.unmatched == [] and result.always_run == []

    def test_include_keeps_named_case_and_all_setup_teardown(self):
        result = select_test_cases(CASES, include=["Login Test"])
        assert names(result) == [
            "Suite Setup", "Setup", "Login Test", "Teardown", "Suite Teardown",
        ]

    @pytest.mark.parametrize("requested", [
        "login test", "LOGIN TEST", "LoGiN tEsT", "  Login   Test ",
    ])
    def test_include_ignores_case_and_whitespace(self, requested):
        assert "Login Test" in names(select_test_cases(CASES, include=[requested]))
        assert "Checkout Test" not in names(select_test_cases(CASES, include=[requested]))

    def test_project_names_with_stray_whitespace_still_match(self):
        cases = {"Add Contact  ": ["a"], " View Contact": ["b"]}
        assert names(select_test_cases(cases, include=["add contact"])) == ["Add Contact  "]
        assert names(select_test_cases(cases, exclude=["view contact"])) == ["Add Contact  "]

    def test_whole_name_only_no_substring_or_prefix(self):
        result = select_test_cases(CASES, include=["Login"])
        assert "Login Test" not in names(result)
        assert result.unmatched == ["Login"]

    def test_no_pattern_matching(self):
        result = select_test_cases(CASES, include=["*Test", "Log.n Test"])
        assert result.unmatched == ["*Test", "Log.n Test"]

    def test_exclude_drops_named_cases(self):
        result = select_test_cases(CASES, exclude=["checkout test", "SEARCH TEST"])
        assert "Checkout Test" not in names(result)
        assert "Search Test" not in names(result)
        assert "Login Test" in names(result)

    def test_selection_keeps_project_order_not_request_order(self):
        result = select_test_cases(CASES, include=["Search Test", "Login Test"])
        regular = [n for n in names(result) if n.endswith("Test")]
        assert regular == ["Login Test", "Search Test"]

    def test_duplicate_requests_are_harmless(self):
        result = select_test_cases(CASES, include=["Login Test", "login test", "Nope", "NOPE"])
        assert [n for n in names(result) if n.endswith("Test")] == ["Login Test"]
        assert result.unmatched == ["Nope"]

    def test_unmatched_reported_in_request_order(self):
        result = select_test_cases(CASES, exclude=["Zed", "Login Test", "Alpha"])
        assert result.unmatched == ["Zed", "Alpha"]

    def test_excluding_setup_is_reported_and_ignored(self):
        result = select_test_cases(CASES, exclude=["setup", "Suite Teardown"])
        assert "Setup" in names(result) and "Suite Teardown" in names(result)
        assert result.always_run == ["Setup", "Suite Teardown"]
        assert result.unmatched == []

    def test_including_setup_is_not_flagged(self):
        result = select_test_cases(CASES, include=["Setup", "Login Test"])
        assert result.always_run == [] and result.unmatched == []

    def test_project_names_differing_only_by_case_are_both_selected(self):
        cases = {"Login": ["a"], "LOGIN": ["b"], "Other": ["c"]}
        assert names(select_test_cases(cases, include=["login"])) == ["Login", "LOGIN"]
        assert names(select_test_cases(cases, exclude=["login"])) == ["Other"]

    def test_both_lists_rejected(self):
        with pytest.raises(OpticsError) as exc:
            select_test_cases(CASES, include=["Login Test"], exclude=["Search Test"])
        assert exc.value.code == Code.E0403

    def test_empty_lists_mean_no_selection(self):
        assert names(select_test_cases(CASES, include=[], exclude=[])) == list(CASES)

    def test_filter_test_cases_keeps_its_contract(self):
        assert filter_test_cases(CASES, include=["login test"]) == (
            select_test_cases(CASES, include=["login test"]).test_cases
        )
        with pytest.raises(OpticsError):
            filter_test_cases(CASES, include=["a"], exclude=["b"])


class TestRunnerArgs:
    def test_defaults_to_no_selection(self, tmp_path):
        args = RunnerArgs(folder_path=str(tmp_path))
        assert args.include is None and args.exclude is None

    def test_both_rejected(self, tmp_path):
        with pytest.raises(OpticsError) as exc:
            RunnerArgs(folder_path=str(tmp_path), include=["a"], exclude=["b"])
        assert exc.value.code == Code.E0403

    @pytest.mark.parametrize("field", ["include", "exclude"])
    def test_empty_list_rejected(self, tmp_path, field):
        with pytest.raises(OpticsError):
            RunnerArgs(folder_path=str(tmp_path), **{field: []})

    @pytest.mark.parametrize("blank", ["", "   ", "\t"])
    def test_blank_name_rejected(self, tmp_path, blank):
        with pytest.raises(OpticsError):
            RunnerArgs(folder_path=str(tmp_path), include=["Login Test", blank])


_ENABLED_CONFIG = """\
driver_sources:
  - selenium:
      enabled: true
      capabilities: {}
elements_sources:
  - selenium_find_element:
      enabled: true
      capabilities: {}
"""


def _make_project(tmp_path, test_cases, extra_config=""):
    (tmp_path / "test_cases").mkdir()
    (tmp_path / "modules").mkdir()
    rows = "".join(f"{name},Step\n" for name in test_cases)
    (tmp_path / "test_cases" / "test_cases.csv").write_text("test_case,test_step\n" + rows)
    (tmp_path / "modules" / "modules.csv").write_text(
        "module_name,module_step\nStep,Sleep,1\n"
    )
    (tmp_path / "config.yaml").write_text(_ENABLED_CONFIG + extra_config)


@pytest.fixture
def no_session():
    with patch.object(execute_module, "SessionManager") as manager, \
            patch.object(execute_module, "ExecutionEngine"):
        yield manager


def _runner(tmp_path, **selection):
    return BaseRunner(RunnerArgs(folder_path=str(tmp_path), **selection))


def _queue(runner):
    node, out = runner.execution_queue, []
    while node:
        out.append(node.name)
        node = node.next
    return out


class TestRunnerSelection:
    def test_cli_include_runs_only_that_case(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Login Test", "Checkout Test"])
        runner = _runner(tmp_path, include=["LOGIN test"])
        assert _queue(runner) == ["Login Test"]
        assert "Selected 1 of 2 test cases: Login Test" in capsys.readouterr().out

    def test_cli_exclude(self, tmp_path, no_session):
        _make_project(tmp_path, ["Login Test", "Checkout Test", "Search Test"])
        runner = _runner(tmp_path, exclude=["checkout test"])
        assert _queue(runner) == ["Login Test", "Search Test"]

    def test_setup_and_teardown_wrap_each_selected_case(self, tmp_path, no_session):
        _make_project(tmp_path, ["Setup", "Login Test", "Checkout Test", "Teardown"])
        runner = _runner(tmp_path, include=["Checkout Test"])
        assert _queue(runner) == ["Setup", "Checkout Test", "Teardown"]

    def test_cli_include_overrides_yaml_exclude(self, tmp_path, no_session):
        _make_project(tmp_path, ["Login Test", "Checkout Test"],
                      'exclude:\n  - "Login Test"\n')
        runner = _runner(tmp_path, include=["Login Test"])
        assert _queue(runner) == ["Login Test"]
        assert runner.config.include == ["Login Test"]
        assert runner.config.exclude is None

    def test_cli_exclude_overrides_yaml_include(self, tmp_path, no_session):
        _make_project(tmp_path, ["Login Test", "Checkout Test", "Search Test"],
                      'include:\n  - "Login Test"\n')
        runner = _runner(tmp_path, exclude=["Search Test"])
        assert _queue(runner) == ["Login Test", "Checkout Test"]

    def test_yaml_selection_applies_without_cli_flags(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Login Test", "Checkout Test"],
                      'include:\n  - "checkout TEST"\n')
        runner = _runner(tmp_path)
        assert _queue(runner) == ["Checkout Test"]
        assert "Selected" not in capsys.readouterr().out

    def test_yaml_unknown_name_warns_and_keeps_running(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Login Test", "Checkout Test"],
                      'exclude:\n  - "Logn Test"\n')
        runner = _runner(tmp_path)
        assert _queue(runner) == ["Login Test", "Checkout Test"]
        err = capsys.readouterr().err
        assert "Warning: `exclude` in config.yaml names 'Logn Test'" in err
        no_session.assert_called_once()

    def test_cli_unknown_name_exits_before_session(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Login Test", "Checkout Test"])
        with pytest.raises(SystemExit) as exc:
            _runner(tmp_path, include=["Login Test", "Chekout Tst"])
        assert exc.value.code == 2
        no_session.assert_not_called()
        err = capsys.readouterr().err
        assert "No test case named 'Chekout Tst'" in err
        assert "Did you mean 'Checkout Test'?" in err
        assert "  Login Test\n  Checkout Test" in err
        assert "Unexpected error" not in err

    def test_cli_unknown_name_without_close_match_has_no_suggestion(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Login Test"])
        with pytest.raises(SystemExit):
            _runner(tmp_path, exclude=["zzzzzz"])
        assert "Did you mean" not in capsys.readouterr().err

    def test_cli_unknown_lists_at_most_twenty_names(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, [f"Case {i:02d}" for i in range(25)])
        with pytest.raises(SystemExit):
            _runner(tmp_path, include=["nope"])
        err = capsys.readouterr().err
        assert "  Case 19" in err and "  Case 20" not in err
        assert "… and 5 more" in err

    def test_cli_exclude_everything_exits(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Setup", "Login Test", "Checkout Test"])
        with pytest.raises(SystemExit) as exc:
            _runner(tmp_path, exclude=["login test", "checkout test"])
        assert exc.value.code == 2
        assert "Nothing to run: --exclude skips every test case" in capsys.readouterr().err
        no_session.assert_not_called()

    def test_cli_include_only_setup_exits(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Suite Setup", "Login Test"])
        with pytest.raises(SystemExit) as exc:
            _runner(tmp_path, include=["suite setup"])
        assert exc.value.code == 2
        assert "--include names only setup/teardown" in capsys.readouterr().err

    def test_cli_excluding_setup_prints_note(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Setup", "Login Test", "Checkout Test"])
        runner = _runner(tmp_path, exclude=["SETUP", "Checkout Test"])
        assert _queue(runner) == ["Setup", "Login Test"]
        err = capsys.readouterr().err
        assert "'Setup' is a setup/teardown test case and always runs; --exclude does not skip it." in err


def _run_cli(argv):
    with patch.object(cli.sys, "argv", ["optics", *argv]):
        cli.main()


class TestCliFlags:
    @pytest.mark.parametrize("command, target", [
        ("execute", "execute_main"), ("dry_run", "dryrun_main"),
    ])
    def test_no_flags_forward_none(self, command, target):
        with patch.object(cli, target) as main:
            _run_cli([command, "proj"])
        _, kwargs = main.call_args
        assert kwargs["include"] is None and kwargs["exclude"] is None

    @pytest.mark.parametrize("command, target", [
        ("execute", "execute_main"), ("dry_run", "dryrun_main"),
    ])
    def test_include_takes_several_names_verbatim(self, command, target):
        with patch.object(cli, target) as main:
            _run_cli([command, "proj", "--include", "Login Test", "  checkout TEST "])
        _, kwargs = main.call_args
        assert kwargs["include"] == ["Login Test", "  checkout TEST "]
        assert kwargs["exclude"] is None

    def test_repeated_flag_extends(self):
        with patch.object(cli, "execute_main") as main:
            _run_cli(["execute", "proj", "--exclude", "A", "--exclude", "B", "C"])
        assert main.call_args.kwargs["exclude"] == ["A", "B", "C"]

    def test_equals_form(self):
        with patch.object(cli, "execute_main") as main:
            _run_cli(["execute", "proj", "--include=Login Test"])
        assert main.call_args.kwargs["include"] == ["Login Test"]

    def test_other_options_after_names(self):
        with patch.object(cli, "execute_main") as main:
            _run_cli(["execute", "proj", "--include", "A", "B", "--no-use-printer"])
        assert main.call_args.kwargs["include"] == ["A", "B"]
        assert main.call_args.kwargs["use_printer"] is False

    @pytest.mark.parametrize("argv", [
        ["execute", "proj", "--include", "A", "--exclude", "B"],
        ["dry_run", "proj", "--exclude", "B", "--include", "A"],
        ["execute", "proj", "--include", "   "],
        ["execute", "proj", "--include"],
        ["execute", "--include", "A", "proj"],
    ])
    def test_usage_errors_exit_2_without_running(self, argv):
        with patch.object(cli, "execute_main") as run, patch.object(cli, "dryrun_main") as dry:
            with pytest.raises(SystemExit) as exc:
                _run_cli(argv)
        assert exc.value.code == 2
        run.assert_not_called()
        dry.assert_not_called()


_APPIUM_CONFIG = """\
driver_sources:
  - appium:
      enabled: true
      url: "http://127.0.0.1:4723"
      capabilities:
        platformName: Android
        automationName: UiAutomator2
elements_sources:
  - appium_find_element:
      enabled: true
"""


class TestEndToEndDryRun:
    """Real dry run through the CLI: parse → filter → DryRunExecutor (no device)."""

    def test_dry_run_validates_only_selected_case(self, tmp_path, capsys):
        _make_project(tmp_path, ["Good Test", "Broken Test"])
        (tmp_path / "config.yaml").write_text(_APPIUM_CONFIG)
        (tmp_path / "test_cases" / "test_cases.csv").write_text(
            "test_case,test_step\nGood Test,Step\nBroken Test,Bad Step\n"
        )
        (tmp_path / "modules" / "modules.csv").write_text(
            "module_name,module_step\nStep,Sleep,1\nBad Step,No Such Keyword\n"
        )
        with pytest.raises(SystemExit) as exc:
            _run_cli(["dry_run", str(tmp_path), "--no-use-printer"])
        assert exc.value.code == 1
        _run_cli(["dry_run", str(tmp_path), "--no-use-printer", "--include", "good test"])
        _run_cli(["dry_run", str(tmp_path), "--no-use-printer", "--exclude", "BROKEN TEST"])
        assert capsys.readouterr().out.count("Selected 1 of 2 test cases: Good Test") == 2


_NO_DRIVER_CONFIG = """\
driver_sources:
  - selenium:
      enabled: false
elements_sources:
  - selenium_find_element:
      enabled: false
"""


class TestListTestCases:
    def test_lists_every_case_one_per_line_without_a_session(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Login Test  ", "Checkout Test"])
        execute_module.list_test_cases_main(str(tmp_path))
        out, err = capsys.readouterr()
        assert out == "Login Test\nCheckout Test\n"
        assert err == ""
        no_session.assert_not_called()

    def test_needs_no_enabled_driver(self, tmp_path, capsys):
        _make_project(tmp_path, ["Login Test"])
        (tmp_path / "config.yaml").write_text(_NO_DRIVER_CONFIG)
        with patch.object(execute_module, "SessionManager") as manager:
            execute_module.list_test_cases_main(str(tmp_path))
        manager.assert_not_called()
        assert capsys.readouterr().out == "Login Test\n"

    def test_setup_and_teardown_go_to_stderr(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Suite Setup", "Login Test", "Teardown"])
        execute_module.list_test_cases_main(str(tmp_path))
        out, err = capsys.readouterr()
        assert out == "Login Test\n"
        assert "Always run: Suite Setup, Teardown" in err

    def test_honours_cli_selection(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Login Test", "Checkout Test", "Search Test"])
        execute_module.list_test_cases_main(str(tmp_path), exclude=["CHECKOUT test"])
        out, err = capsys.readouterr()
        assert out == "Login Test\nSearch Test\n"
        assert "Selected 2 of 3 test cases" in err
        assert "Selected 2 of 3 test cases:" not in out

    def test_honours_yaml_selection(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, ["Login Test", "Checkout Test"], 'include:\n  - "checkout test"\n')
        execute_module.list_test_cases_main(str(tmp_path))
        out, err = capsys.readouterr()
        assert out == "Checkout Test\n"
        assert "Selected 1 of 2 test cases" in err

    def test_unknown_name_still_exits_2(self, tmp_path, no_session):
        _make_project(tmp_path, ["Login Test"])
        with pytest.raises(SystemExit) as exc:
            execute_module.list_test_cases_main(str(tmp_path), include=["Logn Test"])
        assert exc.value.code == 2

    def test_truncated_error_points_at_list(self, tmp_path, no_session, capsys):
        _make_project(tmp_path, [f"Case {i:02d}" for i in range(21)])
        with pytest.raises(SystemExit):
            _runner(tmp_path, include=["nope"])
        assert "… and 1 more (--list prints them all)" in capsys.readouterr().err

    @pytest.mark.parametrize("command", ["execute", "dry_run"])
    def test_flag_lists_instead_of_running(self, command):
        with patch.object(cli, "list_test_cases_main") as lister, \
                patch.object(cli, "execute_main") as run, \
                patch.object(cli, "dryrun_main") as dry:
            _run_cli([command, "proj", "--include", "A", "B", "--list"])
        lister.assert_called_once_with("proj", include=["A", "B"], exclude=None)
        run.assert_not_called()
        dry.assert_not_called()

    def test_without_flag_does_not_list(self):
        with patch.object(cli, "list_test_cases_main") as lister, \
                patch.object(cli, "execute_main"):
            _run_cli(["execute", "proj"])
        lister.assert_not_called()

    def test_end_to_end_through_cli(self, tmp_path, capsys):
        _make_project(tmp_path, ["Setup", "Login Test", "Checkout Test"])
        (tmp_path / "config.yaml").write_text(_NO_DRIVER_CONFIG)
        _run_cli(["execute", str(tmp_path), "--exclude", "login test", "--list"])
        out, err = capsys.readouterr()
        assert out == "Checkout Test\n"
        assert "Always run: Setup" in err
