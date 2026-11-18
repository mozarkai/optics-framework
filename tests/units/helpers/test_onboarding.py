"""Unit tests for the onboarding helpers (``optics_framework/helper/onboarding.py``).

The first-run marker lives under the user's home directory, so every filesystem
test redirects that home at a tmp_path. Output-producing functions are rendered
through a Console writing to an in-memory buffer; no real terminal is touched.
"""
from __future__ import annotations

import io
import os
from unittest.mock import patch

import pytest
from rich.console import Console

from optics_framework.helper import onboarding
from tests.conftest import set_home

pytestmark = pytest.mark.white_box

MODULE = "optics_framework.helper.onboarding"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Point the home directory at a fresh temp dir so the real ~/.optics is
    never touched.

    OPTICS_HOME is cleared so an ambient value on the test runner can't
    redirect the marker away from the isolated home and invalidate the
    home-based assertions below."""
    set_home(monkeypatch, tmp_path)
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


class TestMarkOnboarded:

    def test_never_raises_when_home_not_writable(self):
        # A *file* sitting where ~/.optics must be a directory makes both
        # makedirs and open fail with OSError — mark_onboarded must swallow it.
        home = os.path.expanduser("~")
        with open(os.path.join(home, ".optics"), "w", encoding="utf-8") as fh:
            fh.write("blocker")
        onboarding.mark_onboarded()  # must not raise
        assert onboarding.is_first_run() is True


# --------------------------------------------------------------------------- #
# Welcome banner                                                               #
# --------------------------------------------------------------------------- #

class TestWelcome:


    def test_first_run_prepends_greeting(self):
        out = _capture(onboarding.welcome, first_run=True)
        assert "First time here?" in out


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
