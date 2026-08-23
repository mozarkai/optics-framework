"""Unit tests for the onboarding helpers (``optics_framework/helper/onboarding.py``).

The first-run marker lives under HOME, so every filesystem test points HOME at
a tmp_path. Output-producing functions are rendered through a Console writing
to an in-memory buffer; no real terminal is touched.
"""
from __future__ import annotations

import io
import json
import os
from unittest.mock import patch

import pytest
from rich.console import Console

from optics_framework.helper import onboarding
from optics_framework.helper.version import VERSION

pytestmark = pytest.mark.white_box

MODULE = "optics_framework.helper.onboarding"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point HOME at a fresh temp dir so the real ~/.optics is never touched.

    OPTICS_HOME is cleared so an ambient value on the test runner can't
    redirect the marker away from the isolated HOME and invalidate the
    HOME-based assertions below."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("OPTICS_HOME", raising=False)
    return tmp_path


def _capture(func, *args, **kwargs) -> str:
    """Render a printing helper into a string via a wide fixed-width console
    (fixed width so panel borders never wrap mid-phrase)."""
    buf = io.StringIO()
    console = Console(file=buf, width=200)
    with patch(f"{MODULE}._console", console):
        func(*args, **kwargs)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# First-run marker                                                             #
# --------------------------------------------------------------------------- #

class TestIsFirstRun:
    def test_true_when_marker_missing(self):
        assert onboarding.is_first_run() is True

    def test_false_after_mark_onboarded(self):
        onboarding.mark_onboarded()
        assert onboarding.is_first_run() is False

    def test_marker_is_version_stamped_json(self):
        onboarding.mark_onboarded()
        marker = os.path.join(os.path.expanduser("~"), ".optics", ".onboarded")
        with open(marker, encoding="utf-8") as fh:
            assert json.load(fh) == {"version": VERSION}

    def test_true_when_marker_corrupt(self):
        marker_dir = os.path.join(os.path.expanduser("~"), ".optics")
        os.makedirs(marker_dir)
        with open(os.path.join(marker_dir, ".onboarded"), "w", encoding="utf-8") as fh:
            fh.write("not-json{")
        assert onboarding.is_first_run() is True

    @pytest.mark.parametrize("payload", ["null", "5", "[]", '"onboarded"'])
    def test_only_a_json_object_counts_as_onboarded(self, payload):
        # mark_onboarded writes {"version": ...}; any other parseable JSON is
        # not a real marker and must not suppress first-run guidance.
        marker_dir = os.path.join(os.path.expanduser("~"), ".optics")
        os.makedirs(marker_dir)
        with open(os.path.join(marker_dir, ".onboarded"), "w", encoding="utf-8") as fh:
            fh.write(payload)
        assert onboarding.is_first_run() is True


class TestMarkOnboarded:
    def test_creates_optics_dir(self):
        home = os.path.expanduser("~")
        assert not os.path.exists(os.path.join(home, ".optics"))
        onboarding.mark_onboarded()
        assert os.path.isfile(os.path.join(home, ".optics", ".onboarded"))

    def test_never_raises_when_home_not_writable(self):
        # A *file* sitting where ~/.optics must be a directory makes both
        # makedirs and open fail with OSError — mark_onboarded must swallow it.
        home = os.path.expanduser("~")
        with open(os.path.join(home, ".optics"), "w", encoding="utf-8") as fh:
            fh.write("blocker")
        onboarding.mark_onboarded()  # must not raise
        assert onboarding.is_first_run() is True


class TestOpticsHomeOverride:
    """OPTICS_HOME redirects the marker off the default ~/.optics — needed for
    read-only HOME, containers, CI."""

    def test_marker_lands_in_optics_home(self, tmp_path, monkeypatch):
        custom = tmp_path / "optics-state"
        monkeypatch.setenv("OPTICS_HOME", str(custom))
        onboarding.mark_onboarded()
        assert os.path.isfile(os.path.join(str(custom), ".onboarded"))
        assert onboarding.is_first_run() is False

    def test_optics_home_wins_over_home(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        custom = tmp_path / "optics-state"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("OPTICS_HOME", str(custom))
        onboarding.mark_onboarded()
        assert os.path.exists(os.path.join(str(custom), ".onboarded"))
        assert not os.path.exists(os.path.join(str(home), ".optics"))

    def test_falls_back_to_home_when_optics_home_unset(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.delenv("OPTICS_HOME", raising=False)
        onboarding.mark_onboarded()
        assert os.path.isfile(os.path.join(str(tmp_path), ".optics", ".onboarded"))


# --------------------------------------------------------------------------- #
# Welcome banner                                                               #
# --------------------------------------------------------------------------- #

class TestWelcome:
    def test_shows_golden_path_commands(self):
        out = _capture(onboarding.welcome)
        assert "optics quickstart" in out
        assert "optics doctor" in out
        assert "optics --help" in out

    def test_describes_optics_in_one_line(self):
        out = _capture(onboarding.welcome)
        assert "phones" in out or "browsers" in out

    def test_first_run_prepends_greeting(self):
        out = _capture(onboarding.welcome, first_run=True)
        assert "First time here?" in out

    def test_no_greeting_without_first_run(self):
        out = _capture(onboarding.welcome, first_run=False)
        assert "First time here?" not in out

    def test_banner_carries_version(self):
        out = _capture(onboarding.welcome)
        assert f"v{VERSION}" in out


# --------------------------------------------------------------------------- #
# Next steps                                                                   #
# --------------------------------------------------------------------------- #

class TestPrintNextSteps:
    def test_unconfigured_offers_configure_then_dry_run(self):
        out = _capture(onboarding.print_next_steps, "/tmp/demo", configured=False)
        assert "optics configure /tmp/demo" in out
        assert "optics dry_run /tmp/demo" in out
        assert "optics execute" not in out

    def test_configured_skips_configure(self):
        out = _capture(onboarding.print_next_steps, "/tmp/demo", configured=True)
        assert "optics configure" not in out
        assert "optics dry_run /tmp/demo" in out
        assert "optics execute /tmp/demo" in out
