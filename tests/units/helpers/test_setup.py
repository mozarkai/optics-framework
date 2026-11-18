from __future__ import annotations

import io
import subprocess
import sys
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


MODULE = "optics_framework.helper.setup"


def _reqs(*names: str) -> list[InstallRequest]:
    """InstallRequests (no version) for the named engines, as the TUI builds them."""
    return [InstallRequest(engine=ALL_ENGINES[name]) for name in names]


class TestResolveEngines:

    def test_conflicting_versions_raise(self):
        with pytest.raises(SetupError, match="conflicting versions"):
            resolve_engines(["appium==4.2.0", "appium==5.0.0"])


@pytest.fixture
def installable_env():
    env = Environment(kind=EnvKind.VENV, python=sys.executable, prefix=sys.prefix,
                      has_pip=True, writable=True, creator="venv")
    with patch(f"{MODULE}.detect", return_value=env):
        yield env


@pytest.mark.usefixtures("installable_env")
class TestInstallExtras:
    def test_failure_falls_back_to_stdout(self):
        err = subprocess.CalledProcessError(1, "pip", output="stdout detail", stderr="")
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.run", side_effect=err):
            ok, message = install_extras(_reqs("Appium"), stream=False)
        assert ok is False
        assert "stdout detail" in message


# install_extras — streamed CLI path                                           #

def _popen_result(lines: list[str], returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.stdout = io.StringIO("".join(f"{line}\n" for line in lines))
    proc.returncode = returncode
    proc.wait.return_value = returncode
    return proc


@pytest.mark.usefixtures("installable_env")
class TestStreamedInstall:
    def test_playwright_browser_install_also_streams(self):
        procs = [_popen_result(["pip done"]), _popen_result(["browser downloaded"])]
        with patch(f"{MODULE}._installed_version", return_value=None), \
                patch(f"{MODULE}.subprocess.Popen", side_effect=procs) as popen:
            ok, _ = install_extras(_reqs("Playwright"))
        assert popen.call_count == 2
        assert popen.call_args_list[1].args[0][-1] == "chromium"
        assert ok is True


# Drift guard — setup.py tables must stay in sync with pyproject extras        #

def _pyproject_extras() -> dict:
    root = Path(__file__).resolve().parents[3]
    with open(root / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["tool"]["poetry"]["extras"]


class TestPyprojectParity:
    def test_every_pyproject_extra_installs_through_optics_setup(self):
        resolved, invalid = resolve_engines(list(_pyproject_extras()))
        assert invalid == []


class TestEngineInstallerApp:

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


def test_the_engine_list_names_every_extra_with_its_install_command(capsys):
    list_engines()
    out = capsys.readouterr().out
    assert "optics setup --install <name>" in out
    assert {"appium", "playwright", "mcp"} <= {line.split()[0] for line in out.splitlines() if line.startswith("  ")}
