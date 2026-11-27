from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib
from textual.widgets import Static

from optics_framework.helper import setup
from optics_framework.helper.environment import Environment, EnvKind
from optics_framework.helper.setup import (
    ALL_ENGINES,
    EngineInstallerApp,
    InstallRequest,
    SetupError,
    install_extras,
    list_engines,
    resolve_engines,
)


def _reqs(*names: str) -> list[InstallRequest]:
    """InstallRequests (no version) for the named engines, as the TUI builds them."""
    return [InstallRequest(engine=ALL_ENGINES[name]) for name in names]


class TestResolveEngines:

    def test_conflicting_versions_raise(self):
        with pytest.raises(SetupError, match="conflicting versions"):
            resolve_engines(["appium==4.2.0", "appium==5.0.0"])


@pytest.fixture
def venv(tool_folder, monkeypatch, tmp_path):
    """A writable venv whose python is the fake installer tool."""
    env = Environment(kind=EnvKind.VENV, python=str(tool_folder / "python"), prefix=str(tmp_path),
                      has_pip=True, writable=True, creator="venv")
    monkeypatch.setattr(setup, "detect", lambda: env)
    return env


@pytest.mark.usefixtures("venv")
class TestInstallExtras:
    def test_a_failed_install_reports_what_the_installer_printed(self, tools):
        tools("stdout detail", status=1)
        ok, message = install_extras(_reqs("Appium"), stream=False)
        assert ok is False
        assert "stdout detail" in message

    def test_playwright_also_installs_its_browser(self, tools, capfd):
        ran = tools("installed\n")
        ok, _ = install_extras(_reqs("Playwright"))
        assert ok is True
        assert [line.split()[:4] for line in ran()] == [["python", "-m", "pip", "install"],
                                                        ["python", "-m", "playwright", "install"]]
        assert re.search(r"--disable-pip-version-check optics-framework\[[^\]]*\bplaywright\b", ran()[0])
        assert ran()[1] == "python -m playwright install --with-deps chromium"
        assert capfd.readouterr().out.count("installed") == 2


def _pyproject_extras() -> dict:
    root = Path(__file__).resolve().parents[3]
    with open(root / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)["tool"]["poetry"]["extras"]


class TestPyprojectParity:
    def test_every_pyproject_extra_installs_through_optics_setup(self):
        _, invalid = resolve_engines(list(_pyproject_extras()))
        assert invalid == []


@pytest.mark.usefixtures("venv")
class TestEngineInstallerApp:
    async def test_install_runs_in_a_worker_and_reports_the_result(self, tools):
        ran = tools("installed\n")
        app = EngineInstallerApp()
        async with app.run_test() as pilot:
            await pilot.click("#action_appium")
            await pilot.click("#install")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "Engine packages installed" in str(app.query_one("#status", Static).render())
        assert [line.split()[:4] for line in ran()] == [["python", "-m", "pip", "install"]]


def test_the_engine_list_names_every_extra_with_its_install_command(capsys):
    list_engines()
    out = capsys.readouterr().out
    assert "optics setup --install <name>" in out
    assert {"appium", "playwright", "mcp"} <= {line.split()[0] for line in out.splitlines() if line.startswith("  ")}
