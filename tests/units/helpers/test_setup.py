"""Unit tests for the engine-setup helper (``optics_framework/helper/setup.py``).

Covers the pure token-resolution layer (``_norm``, ``_alias_index``,
``resolve_engines``) and the ``install_extras`` install path (subprocess mocked —
no real pip/network). A drift guard keeps the engine/bundle tables in sync with
the extras declared in ``pyproject.toml``.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path
from unittest.mock import call, patch

import pytest

from optics_framework.helper.setup import (
    ALL_ENGINES,
    DISTRIBUTION_NAME,
    InstallRequest,
    SetupError,
    _BUNDLES,
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
    def test_noop_on_empty(self, capsys):
        with patch(f"{MODULE}.subprocess.run") as run:
            install_extras([])
        run.assert_not_called()
        assert "No engines selected" in capsys.readouterr().out

    def test_installs_version_pinned_spec(self):
        engines = _reqs("Appium")
        with patch(f"{MODULE}._installed_version", return_value="1.2.3"), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(engines)
        run.assert_called_once_with(
            [sys.executable, "-m", "pip", "install", f"{DISTRIBUTION_NAME}[appium]==1.2.3"],
            capture_output=True, text=True, check=True, shell=False,
        )

    def test_unpinned_when_version_unknown(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(_reqs("Appium"))
        args = run.call_args.args[0]
        assert args[-1] == f"{DISTRIBUTION_NAME}[appium]"

    def test_extras_sorted_and_deduped_in_spec(self):
        engines = _reqs("Selenium", "Appium", "Selenium")
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(engines)
        spec = run.call_args.args[0][-1]
        assert spec == f"{DISTRIBUTION_NAME}[appium,selenium]"

    def test_version_override_appends_pinned_package(self):
        reqs = [InstallRequest(engine=ALL_ENGINES["Appium"], version="==4.2.0")]
        with patch(f"{MODULE}._installed_version", return_value="1.2.3"), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(reqs)
        assert run.call_args.args[0] == [
            sys.executable, "-m", "pip", "install",
            f"{DISTRIBUTION_NAME}[appium]==1.2.3", "appium-python-client==4.2.0",
        ]

    def test_no_version_appends_nothing(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(_reqs("Appium"))
        # extras spec only, no trailing concrete package.
        assert run.call_args.args[0][-1] == f"{DISTRIBUTION_NAME}[appium]"

    def test_playwright_triggers_browser_install(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(_reqs("Playwright"))
        assert run.call_count == 2
        assert run.call_args_list[1] == call(
            [sys.executable, "-m", "playwright", "install", "--with-deps", "chromium"],
            capture_output=True, text=True, check=True, shell=False,
        )

    def test_non_playwright_skips_browser_install(self):
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run") as run:
            install_extras(_reqs("Appium"))
        assert run.call_count == 1

    def test_failure_prints_captured_stderr(self, capsys):
        err = subprocess.CalledProcessError(1, "pip", stderr="boom: could not resolve")
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run", side_effect=err):
            install_extras(_reqs("Appium"))
        out = capsys.readouterr().out
        assert "Installation failed" in out
        assert "boom: could not resolve" in out

    def test_failure_falls_back_to_stdout(self, capsys):
        err = subprocess.CalledProcessError(1, "pip", output="stdout detail", stderr="")
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run", side_effect=err):
            install_extras(_reqs("Appium"))
        assert "stdout detail" in capsys.readouterr().out


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
