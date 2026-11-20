from __future__ import annotations

import io
from unittest.mock import patch

import pytest
from rich.console import Console

from optics_framework.helper import onboarding
from tests.conftest import set_home


MODULE = "optics_framework.helper.onboarding"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
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


class TestPrintNextSteps:
    @pytest.mark.parametrize("configured", [False, True])
    def test_configure_comes_first_until_the_project_is_configured(self, configured):
        out = _capture(onboarding.print_next_steps, "/tmp/demo", configured=configured)
        assert "optics dry_run /tmp/demo" in out
        assert ("optics configure /tmp/demo" in out) is not configured
        assert ("optics execute /tmp/demo" in out) is configured
