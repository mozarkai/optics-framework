"""Unit tests for the engine-setup helper (``optics_framework/helper/setup.py``).

Covers the pure token-resolution layer (``_norm``, ``_alias_index``,
``resolve_engines``) and the ``install_extras`` install path (subprocess mocked —
no real pip/network). A drift guard keeps the engine/bundle tables in sync with
the extras declared in ``pyproject.toml``.
"""
from __future__ import annotations

import asyncio
import io
import re
import subprocess
import sys
import threading
import tomllib
from pathlib import Path
from unittest.mock import call, MagicMock, patch

import pytest

from textual.widgets import Static

from optics_framework.helper.setup import (
    ALL_ENGINES,
    DISTRIBUTION_NAME,
    EngineInstallerApp,
    InstallRequest,
    SetupError,
    _BUNDLES,
    _TAIL_LINES,
    _alias_index,
    _norm,
    _split_token,
    install_extras,
    resolve_engines,
)

pytestmark = pytest.mark.white_box

MODULE = "optics_framework.helper.setup"


def _reqs(*names: str) -> list[InstallRequest]:
    """InstallRequests (no version) for the named engines, as the TUI builds them."""
    return [InstallRequest(engine=ALL_ENGINES[name]) for name in names]


# --------------------------------------------------------------------------- #
# _norm                                                                        #
# --------------------------------------------------------------------------- #

class TestNorm:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Appium", "appium"),
            ("  Appium  ", "appium"),
            ("Google Vision", "google_vision"),
            ("google-vision", "google_vision"),
            ("GOOGLE_VISION", "google_vision"),
            ("Google-Vision", "google_vision"),
        ],
    )
    def test_normalises_case_space_and_hyphen(self, raw, expected):
        assert _norm(raw) == expected

    def test_hyphen_and_space_collapse_to_same_token(self):
        assert _norm("google vision") == _norm("google-vision")


# --------------------------------------------------------------------------- #
# _alias_index                                                                 #
# --------------------------------------------------------------------------- #

class TestAliasIndex:
    def test_maps_display_name_extra_and_aliases(self):
        index = _alias_index()
        gvision = ALL_ENGINES["Google Vision"]
        # display name, extra, and each explicit alias all resolve to one engine.
        assert index["google_vision"] is gvision
        assert index["googlevision"] is gvision
        assert index[_norm(gvision.name)] is gvision
        assert index[_norm(gvision.extra)] is gvision

    def test_every_engine_reachable_by_extra(self):
        index = _alias_index()
        for engine in ALL_ENGINES.values():
            assert index[_norm(engine.extra)] is engine

    def test_all_keys_are_normalised(self):
        index = _alias_index()
        assert all(key == _norm(key) for key in index)


# --------------------------------------------------------------------------- #
# resolve_engines                                                              #
# --------------------------------------------------------------------------- #

class TestResolveEngines:
    def test_resolves_display_name(self):
        resolved, invalid = resolve_engines(["Appium"])
        assert [r.engine.extra for r in resolved] == ["appium"]
        assert invalid == []

    def test_resolves_extra_and_config_key_case_insensitively(self):
        resolved, invalid = resolve_engines(["APPIUM", "google-vision"])
        assert [r.engine.extra for r in resolved] == ["appium", "google-vision"]
        assert invalid == []

    def test_reports_unknown_tokens_as_invalid(self):
        resolved, invalid = resolve_engines(["appium", "not-a-driver"])
        assert [r.engine.extra for r in resolved] == ["appium"]
        assert invalid == ["not-a-driver"]

    def test_deduplicates_across_alias_forms(self):
        resolved, invalid = resolve_engines(["Appium", "appium"])
        assert [r.engine.extra for r in resolved] == ["appium"]
        assert invalid == []

    @pytest.mark.parametrize(
        "bundle, expected_extras",
        [
            ("mobile", ["appium"]),
            ("web", ["selenium", "playwright"]),
            ("vision", ["easyocr", "pytesseract", "google-vision"]),
        ],
    )
    def test_bundle_expands_to_member_engines(self, bundle, expected_extras):
        resolved, invalid = resolve_engines([bundle])
        assert [r.engine.extra for r in resolved] == expected_extras
        assert invalid == []

    def test_all_bundle_expands_to_every_engine(self):
        resolved, invalid = resolve_engines(["all"])
        assert {r.engine.extra for r in resolved} == {e.extra for e in ALL_ENGINES.values()}
        assert invalid == []

    def test_bundle_is_case_insensitive(self):
        resolved, _ = resolve_engines(["WEB"])
        assert [r.engine.extra for r in resolved] == ["selenium", "playwright"]

    def test_bundle_and_member_dedupe(self):
        # "web" pulls selenium+playwright; the explicit "selenium" must not repeat.
        resolved, invalid = resolve_engines(["web", "selenium"])
        assert [r.engine.extra for r in resolved] == ["selenium", "playwright"]
        assert invalid == []

    def test_empty_input(self):
        assert resolve_engines([]) == ([], [])

    def test_version_specifier_is_parsed_onto_request(self):
        resolved, invalid = resolve_engines(["appium==4.2.0"])
        assert [(r.engine.extra, r.version) for r in resolved] == [("appium", "==4.2.0")]
        assert invalid == []

    def test_range_specifier_preserved_verbatim(self):
        resolved, _ = resolve_engines(["easyocr>=1.7,<2.0"])
        assert resolved[0].version == ">=1.7,<2.0"

    def test_version_on_bundle_is_invalid(self):
        resolved, invalid = resolve_engines(["all==1.0"])
        assert resolved == []
        assert invalid == ["all==1.0"]

    def test_explicit_version_wins_over_bare_duplicate(self):
        # bundle brings selenium bare; explicit token then pins it.
        resolved, _ = resolve_engines(["web", "selenium==4.20"])
        selenium = next(r for r in resolved if r.engine.extra == "selenium")
        assert selenium.version == "==4.20"

    def test_conflicting_versions_raise(self):
        with pytest.raises(SetupError, match="conflicting versions"):
            resolve_engines(["appium==4.2.0", "appium==5.0.0"])

    def test_same_version_twice_is_deduped(self):
        resolved, invalid = resolve_engines(["appium==5.0.0", "appium==5.0.0"])
        assert [(r.engine.extra, r.version) for r in resolved] == [("appium", "==5.0.0")]
        assert invalid == []

    @pytest.mark.parametrize("token", ["appium=4.3.0", "appium=>4.3", "appium=="])
    def test_malformed_specifier_raises(self, token):
        with pytest.raises(SetupError, match="invalid version"):
            resolve_engines([token])


# --------------------------------------------------------------------------- #
# _split_token                                                                 #
# --------------------------------------------------------------------------- #

class TestSplitToken:
    @pytest.mark.parametrize(
        "token, name, spec",
        [
            ("appium", "appium", None),
            ("appium==4.2.0", "appium", "==4.2.0"),
            ("google-vision==3.5", "google-vision", "==3.5"),
            ("easyocr>=1.7,<2.0", "easyocr", ">=1.7,<2.0"),
            ("appium ~= 4.2", "appium", "~= 4.2"),
        ],
    )
    def test_splits_name_from_specifier(self, token, name, spec):
        assert _split_token(token) == (name, spec)


# --------------------------------------------------------------------------- #
# install_extras                                                               #
# --------------------------------------------------------------------------- #

class TestInstallExtras:
    """Capture mode (``stream=False``, the TUI path): subprocess mocked —
    no real pip/network. The streamed CLI variants live in TestStreamedInstall."""

    def test_returns_false_on_empty(self):
        with patch(f"{MODULE}.subprocess.run") as run:
            ok, message = install_extras([], stream=False)
        run.assert_not_called()
        assert ok is False
        assert "No engines selected" in message

    def test_success_returns_true_and_message(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run"):
            ok, message = install_extras(_reqs("Appium"), stream=False)
        assert ok is True
        assert "installed successfully" in message.lower()

    def test_installs_version_pinned_spec(self):
        engines = _reqs("Appium")
        with patch(f"{MODULE}._installed_version", return_value="1.2.3"), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(engines, stream=False)
        run.assert_called_once_with(
            [sys.executable, "-m", "pip", "install", f"{DISTRIBUTION_NAME}[appium]==1.2.3"],
            capture_output=True, text=True, check=True, shell=False,
        )

    def test_unpinned_when_version_unknown(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(_reqs("Appium"), stream=False)
        args = run.call_args.args[0]
        assert args[-1] == f"{DISTRIBUTION_NAME}[appium]"

    def test_extras_sorted_and_deduped_in_spec(self):
        engines = _reqs("Selenium", "Appium", "Selenium")
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(engines, stream=False)
        spec = run.call_args.args[0][-1]
        assert spec == f"{DISTRIBUTION_NAME}[appium,selenium]"

    def test_version_override_appends_pinned_package(self):
        reqs = [InstallRequest(engine=ALL_ENGINES["Appium"], version="==4.2.0")]
        with patch(f"{MODULE}._installed_version", return_value="1.2.3"), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(reqs, stream=False)
        assert run.call_args.args[0] == [
            sys.executable, "-m", "pip", "install",
            f"{DISTRIBUTION_NAME}[appium]==1.2.3", "appium-python-client==4.2.0",
        ]

    def test_no_version_appends_nothing(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(_reqs("Appium"), stream=False)
        # extras spec only, no trailing concrete package.
        assert run.call_args.args[0][-1] == f"{DISTRIBUTION_NAME}[appium]"

    def test_playwright_triggers_browser_install(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(_reqs("Playwright"), stream=False)
        assert run.call_count == 2
        assert run.call_args_list[1] == call(
            [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
            capture_output=True, text=True, check=True, shell=False,
        )

    def test_non_playwright_skips_browser_install(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(_reqs("Appium"), stream=False)
        assert run.call_count == 1

    def test_failure_returns_message_with_stderr(self):
        err = subprocess.CalledProcessError(1, "pip", stderr="boom: could not resolve")
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run", side_effect=err):
            ok, message = install_extras(_reqs("Appium"), stream=False)
        assert ok is False
        assert "Installation failed" in message
        assert "boom: could not resolve" in message

    def test_failure_falls_back_to_stdout(self):
        err = subprocess.CalledProcessError(1, "pip", output="stdout detail", stderr="")
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run", side_effect=err):
            ok, message = install_extras(_reqs("Appium"), stream=False)
        assert ok is False
        assert "stdout detail" in message


# --------------------------------------------------------------------------- #
# install_extras — streamed CLI path                                           #
# --------------------------------------------------------------------------- #

def _popen_result(lines: list[str], returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


class TestStreamedInstall:
    """Stream mode (default, the ``optics setup --install`` CLI path): output is
    echoed live and a short tail is quoted on failure."""

    def test_streams_output_and_returns_success(self, capsys):
        proc = _popen_result(["Collecting appium-python-client", "Downloading appium..."])
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.Popen", return_value=proc) as popen:
            ok, message = install_extras(_reqs("Appium"))
        assert popen.call_args.args[0] == [
            sys.executable, "-m", "pip", "install", f"{DISTRIBUTION_NAME}[appium]",
        ]
        assert popen.call_args.kwargs == dict(
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, shell=False,
        )
        captured = capsys.readouterr()
        assert "Collecting appium-python-client" in captured.out
        assert "Downloading appium..." in captured.out
        assert ok is True
        assert message == "Engines installed successfully!"

    def test_filters_requirement_already_satisfied_noise(self, capsys):
        proc = _popen_result([
            "Requirement already satisfied: certifi in /venv (2024.1)",
            "Collecting appium-python-client",
        ])
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.Popen", return_value=proc):
            ok, _ = install_extras(_reqs("Appium"))
        out = capsys.readouterr().out
        assert "Requirement already satisfied" not in out
        assert "Collecting appium-python-client" in out
        assert ok is True

    def test_version_pin_reaches_pip_argv(self):
        reqs = [InstallRequest(engine=ALL_ENGINES["Appium"], version="==5.0.0")]
        proc = _popen_result([])
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.Popen", return_value=proc) as popen:
            ok, _ = install_extras(reqs)
        argv = popen.call_args.args[0]
        assert f"{DISTRIBUTION_NAME}[appium]" in argv
        assert "appium-python-client==5.0.0" in argv
        assert ok is True

    def test_playwright_browser_install_also_streams(self):
        procs = [_popen_result(["pip done"]), _popen_result(["browser downloaded"])]
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.Popen", side_effect=procs) as popen:
            ok, _ = install_extras(_reqs("Playwright"))
        assert popen.call_count == 2
        assert popen.call_args_list[1].args[0][-1] == "chromium"
        assert ok is True

    def test_failure_quotes_tail_and_reports_failure(self, capsys):
        lines = [f"line-{n}" for n in range(_TAIL_LINES + 5)]
        proc = _popen_result(lines, returncode=1)
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.Popen", return_value=proc):
            ok, message = install_extras(_reqs("Appium"))
        captured = capsys.readouterr()
        assert all(f"line-{n}\n" in captured.out for n in range(len(lines)))
        assert ok is False
        assert "Installation failed" in message
        # Only the buffered tail is quoted — early lines are not retained.
        assert f"line-{len(lines) - 1}" in message
        assert "line-0" not in message


# --------------------------------------------------------------------------- #
# Drift guard — setup.py tables must stay in sync with pyproject extras        #
# --------------------------------------------------------------------------- #

def _pyproject_extras() -> dict:
    root = Path(__file__).resolve().parents[3]
    with open(root / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["tool"]["poetry"]["extras"]


class TestPyprojectParity:
    def test_every_engine_extra_declared_in_pyproject(self):
        extras = _pyproject_extras()
        for engine in ALL_ENGINES.values():
            assert engine.extra in extras, f"{engine.extra} missing from pyproject extras"

    def test_every_bundle_declared_in_pyproject(self):
        extras = _pyproject_extras()
        for bundle in _BUNDLES:
            assert bundle in extras, f"bundle '{bundle}' missing from pyproject extras"

    def test_bundle_membership_matches_pyproject_packages(self):
        extras = _pyproject_extras()
        for bundle, engines in _BUNDLES.items():
            declared = set(extras[bundle])
            expanded = {pkg for engine in engines for pkg in engine.packages}
            assert expanded == declared, (
                f"bundle '{bundle}' expands to {expanded} "
                f"but pyproject declares {declared}"
            )


# --------------------------------------------------------------------------- #
# EngineInstallerApp (TUI)                                                     #
# --------------------------------------------------------------------------- #

class TestEngineInstallerApp:
    """The picker must give visible feedback and stay responsive.

    install runs in a thread worker (not inline on the event loop), the outcome
    is shown in a Static/notification (not a swallowed print), and the checkbox
    list lives in a scroll container so the buttons stay reachable.
    """

    async def test_list_is_scrollable_and_status_present(self):
        from textual.containers import VerticalScroll
        app = EngineInstallerApp()
        async with app.run_test():
            # The engine list lives in a scroll container so the buttons below it
            # stay reachable on a short terminal.
            assert app.query(VerticalScroll)
            assert app.query_one("#status", Static) is not None
            assert app.query_one("#install") is not None

    async def test_no_selection_notifies_and_skips_install(self):
        app = EngineInstallerApp()
        with patch(f"{MODULE}.install_extras") as inst:
            async with app.run_test() as pilot:
                await pilot.click("#install")
                await pilot.pause()
        inst.assert_not_called()

    async def test_install_runs_in_worker_and_reports_result(self):
        app = EngineInstallerApp()
        with patch(
            f"{MODULE}.install_extras",
            return_value=(True, "Engines installed successfully!"),
        ) as inst:
            async with app.run_test() as pilot:
                app.selected_engines = {"Appium": ALL_ENGINES["Appium"]}
                await pilot.click("#install")
                await app.workers.wait_for_complete()
                await pilot.pause()
                status = app.query_one("#status", Static)
                assert "installed successfully" in str(status.render()).lower()
        inst.assert_called_once()

    async def test_failure_result_shown_and_install_reenabled(self):
        from textual.widgets import Button
        app = EngineInstallerApp()
        with patch(
            f"{MODULE}.install_extras",
            return_value=(False, "Installation failed: boom"),
        ):
            async with app.run_test() as pilot:
                app.selected_engines = {"Appium": ALL_ENGINES["Appium"]}
                await pilot.click("#install")
                await app.workers.wait_for_complete()
                await pilot.pause()
                status = app.query_one("#status", Static)
                assert "failed" in str(status.render()).lower()
                # Install re-enabled so the user can retry after a failure.
                assert app.query_one("#install", Button).disabled is False

    async def test_install_ticker_shows_elapsed_time_then_final_status(self):
        release = threading.Event()

        def slow_install(requests, stream=False):
            release.wait(timeout=10)
            return True, "Engines installed successfully!"

        app = EngineInstallerApp()
        with patch(f"{MODULE}.install_extras", side_effect=slow_install) as inst:
            async with app.run_test() as pilot:
                app.selected_engines = {"Appium": ALL_ENGINES["Appium"]}
                await pilot.click("#install")
                # Let the 1 Hz ticker fire at least once mid-install.
                await asyncio.sleep(1.3)
                status = str(app.query_one("#status", Static).render())
                assert re.search(r"Installing Appium… \d+s", status)
                release.set()
                await app.workers.wait_for_complete()
                await pilot.pause()
                status = str(app.query_one("#status", Static).render())
                assert "installed successfully" in status.lower()
        assert inst.call_args.kwargs == {"stream": False}


class TestKeyboardEscape:
    """The picker must not be a keyboard trap (regression: q, Esc-less
    Ctrl+C did nothing — only the on-screen Quit button or Ctrl+Q worked)."""

    def test_q_and_ctrl_c_bindings_map_to_quit(self):
        bindings = {}
        for binding in EngineInstallerApp.BINDINGS:
            key, action = binding[:2]
            bindings[key] = action
        assert bindings.get("q") == "quit"
        assert bindings.get("ctrl+c") == "quit"
        from textual.app import App
        default_keys = {b.key for b in App.BINDINGS}
        assert "ctrl+q" in default_keys

    async def test_footer_is_composed_so_binding_is_visible(self):
        from textual.widgets import Footer
        app = EngineInstallerApp()
        async with app.run_test():
            assert app.query_one(Footer) is not None

    @pytest.mark.parametrize("key", ["q", "ctrl+c"])
    async def test_key_quits_the_app(self, key):
        app = EngineInstallerApp()
        async with app.run_test() as pilot:
            await pilot.press(key)
            await pilot.pause()
        assert not app.is_running
