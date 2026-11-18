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
from unittest.mock import MagicMock, patch

import pytest

from textual.widgets import Static

from optics_framework.helper.environment import EnvKind, Environment
from optics_framework.helper.setup import (
    ALL_ENGINES,
    EngineInstallerApp,
    list_engines,
    InstallRequest,
    SetupError,
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


# --------------------------------------------------------------------------- #
# _alias_index                                                                 #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# resolve_engines                                                              #
# --------------------------------------------------------------------------- #

class TestResolveEngines:


    def test_version_on_bundle_is_invalid(self):
        resolved, invalid = resolve_engines(["all==1.0"])
        assert resolved == []
        assert invalid == ["all==1.0"]

    def test_conflicting_versions_raise(self):
        with pytest.raises(SetupError, match="conflicting versions"):
            resolve_engines(["appium==4.2.0", "appium==5.0.0"])


# --------------------------------------------------------------------------- #
# _split_token                                                                 #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# install_extras                                                               #
# --------------------------------------------------------------------------- #

@pytest.fixture
def installable_env():
    """Pin the install environment to a plain venv with pip.

    ``install_extras`` now asks ``plan_install`` for its command, so without
    this the assertions below would describe whichever environment the suite
    happens to run in — a uv venv or a lock-managed checkout refuses outright.
    """
    env = Environment(kind=EnvKind.VENV, python=sys.executable, prefix=sys.prefix,
                      has_pip=True, writable=True, creator="venv")
    with patch(f"{MODULE}.detect", return_value=env):
        yield env


@pytest.mark.usefixtures("installable_env")
class TestInstallExtras:
    """Capture mode (``stream=False``, the TUI path): subprocess mocked —
    no real pip/network. The streamed CLI variants live in TestStreamedInstall."""

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


@pytest.mark.usefixtures("installable_env")
class TestStreamedInstall:
    """Stream mode (default, the ``optics setup --install`` CLI path): output is
    echoed live and a short tail is quoted on failure."""

    def test_playwright_browser_install_also_streams(self):
        procs = [_popen_result(["pip done"]), _popen_result(["browser downloaded"])]
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.Popen", side_effect=procs) as popen:
            ok, _ = install_extras(_reqs("Playwright"))
        assert popen.call_count == 2
        assert popen.call_args_list[1].args[0][-1] == "chromium"
        assert ok is True


# --------------------------------------------------------------------------- #
# Drift guard — setup.py tables must stay in sync with pyproject extras        #
# --------------------------------------------------------------------------- #

def _pyproject_extras() -> dict:
    root = Path(__file__).resolve().parents[3]
    with open(root / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["tool"]["poetry"]["extras"]


class TestPyprojectParity:

    def test_every_pyproject_extra_installs_through_optics_setup(self):
        resolved, invalid = resolve_engines(list(_pyproject_extras()))
        assert invalid == []


# --------------------------------------------------------------------------- #
# EngineInstallerApp (TUI)                                                     #
# --------------------------------------------------------------------------- #

class TestEngineInstallerApp:
    """The picker must give visible feedback and stay responsive.

    install runs in a thread worker (not inline on the event loop), the outcome
    is shown in a Static/notification (not a swallowed print), and the checkbox
    list lives in a scroll container so the buttons stay reachable.
    """

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
            return_value=(True, "Engine packages installed."),
        ) as inst:
            async with app.run_test() as pilot:
                app.selected_engines = {"Appium": ALL_ENGINES["Appium"]}
                await pilot.click("#install")
                await app.workers.wait_for_complete()
                await pilot.pause()
                status = app.query_one("#status", Static)
                assert "Engine packages installed" in str(status.render())
        inst.assert_called_once()

    async def test_install_ticker_shows_elapsed_time_then_final_status(self):
        release = threading.Event()

        def slow_install(requests, stream=False):
            release.wait(timeout=10)
            return True, "Engine packages installed."

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
                assert "Engine packages installed" in status
        assert inst.call_args.kwargs == {"stream": False}


def test_the_engine_list_names_every_extra_with_its_install_command(capsys):
    list_engines()
    out = capsys.readouterr().out
    assert "optics setup --install <name>" in out
    assert {"appium", "playwright", "mcp"} <= {line.split()[0] for line in out.splitlines() if line.startswith("  ")}
