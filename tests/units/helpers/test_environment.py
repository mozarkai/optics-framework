"""Unit tests for install-environment detection
(``optics_framework/helper/environment.py``).

``plan_install`` is pure, so its decision table is exercised directly on
constructed ``Environment`` values. ``detect`` reads the live interpreter, so
those tests fake one: a directory shape under ``tmp_path`` plus the handful of
interpreter attributes the module reads.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from optics_framework.helper import environment
from optics_framework.helper.environment import (
    EnvKind,
    Environment,
    detect,
    describe,
    plan_install,
    removal_command,
)

pytestmark = pytest.mark.white_box

SPEC = "optics-framework[appium]==1.10.3"
PINNED = "appium-python-client==5.0.0"


def _venv(path: Path, creator: str = "venv") -> Path:
    """A venv-shaped directory, stamped by ``creator`` the way that tool does."""
    path.mkdir(parents=True, exist_ok=True)
    lines = ["home = /usr/bin"]
    if creator != "venv":
        lines.append(f"{creator} = 1.2.3")
    (path / "pyvenv.cfg").write_text("\n".join(lines), encoding="utf-8")
    return path


def _env(kind: EnvKind, **overrides) -> Environment:
    fields = {
        "python": "/p/bin/python",
        "prefix": "/p",
        "has_pip": True,
        "writable": True,
        "uv": "/usr/bin/uv",
    }
    fields.update(overrides)
    return Environment(kind=kind, **fields)


@pytest.fixture
def interpreter(monkeypatch, tmp_path):
    """Build a fake interpreter and run ``detect()`` against it."""
    def _make(prefix: Path, *, base: Path | None = None, pip: bool = True,
              uv: str | None = "/usr/bin/uv", stdlib: Path | None = None,
              writable: bool = True,
              environ: dict[str, str] | None = None) -> Environment:
        monkeypatch.setattr(environment.sys, "prefix", str(prefix))
        monkeypatch.setattr(environment.sys, "base_prefix", str(base or prefix))
        monkeypatch.setattr(environment.sys, "executable", f"{prefix}/bin/python")
        monkeypatch.setattr(environment, "find_spec",
                            lambda name: object() if pip else None)
        monkeypatch.setattr(environment.shutil, "which", lambda name: uv)
        monkeypatch.delenv("UV", raising=False)
        for variable in environment._ACTIVE_MANAGER_VARS:
            monkeypatch.delenv(variable, raising=False)
        for name, value in (environ or {}).items():
            monkeypatch.setenv(name, value)

        purelib = tmp_path / "purelib"
        purelib.mkdir(exist_ok=True)
        stdlib_dir = stdlib if stdlib else tmp_path / "stdlib"
        stdlib_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            environment.sysconfig, "get_path",
            lambda name, *a, **k: str(purelib if name == "purelib" else stdlib_dir))
        monkeypatch.setattr(environment.os, "access", lambda *a: writable)
        return detect()
    return _make


class TestDetect:
    def test_stdlib_venv_is_a_plain_venv(self, interpreter, tmp_path):
        env = interpreter(_venv(tmp_path / ".venv"), base=tmp_path / "base")
        assert env.kind is EnvKind.VENV
        assert env.creator == "venv"
        assert env.manager is None

    def test_uv_venv_is_recognised_without_pip(self, interpreter, tmp_path):
        env = interpreter(_venv(tmp_path / ".venv", creator="uv"),
                          base=tmp_path / "base", pip=False)
        assert env.kind is EnvKind.VENV
        assert env.creator == "uv"
        assert env.has_pip is False

    def test_venv_from_marked_base_is_not_system_managed(self, interpreter, tmp_path):
        """PEP 668 binds the base interpreter only.

        A venv inherits its base's stdlib directory, so the marker is visible
        from inside one; treating that as externally managed would refuse
        installs into every healthy venv built from a Homebrew, Debian or
        uv-managed Python."""
        stdlib = tmp_path / "stdlib"
        stdlib.mkdir()
        (stdlib / "EXTERNALLY-MANAGED").write_text(
            "[externally-managed]\nError=use apt install\n", encoding="utf-8")
        env = interpreter(_venv(tmp_path / ".venv"), base=tmp_path / "base",
                          stdlib=stdlib)
        assert env.kind is EnvKind.VENV
        assert env.pep668_error is None

    def test_marked_base_interpreter_is_system_managed(self, interpreter, tmp_path):
        stdlib = tmp_path / "stdlib"
        stdlib.mkdir()
        (stdlib / "EXTERNALLY-MANAGED").write_text(
            "[externally-managed]\nError=use apt install python3-x\n",
            encoding="utf-8")
        base = tmp_path / "usr"
        base.mkdir()
        env = interpreter(base, stdlib=stdlib)
        assert env.kind is EnvKind.SYSTEM_MANAGED
        assert "apt install python3-x" in env.pep668_error

    def test_unmarked_base_interpreter_is_plain_system(self, interpreter, tmp_path):
        base = tmp_path / "usr"
        base.mkdir()
        env = interpreter(base)
        assert env.kind is EnvKind.SYSTEM
        assert env.pep668_error is None

    @pytest.mark.parametrize("marker, manager", [
        ("uv-receipt.toml", "uv"),
        ("pipx_metadata.json", "pipx"),
    ])
    def test_tool_environments_are_recognised(self, interpreter, tmp_path,
                                              marker, manager):
        prefix = _venv(tmp_path / "tool")
        (prefix / marker).write_text("", encoding="utf-8")
        env = interpreter(prefix, base=tmp_path / "base", pip=False)
        assert env.kind is EnvKind.TOOL
        assert env.manager == manager

    def test_uvx_cache_environment_is_a_tool_environment(self, interpreter, tmp_path):
        prefix = _venv(tmp_path / "archive-v0" / "abc123", creator="uv")
        env = interpreter(prefix, base=tmp_path / "base", pip=False)
        assert env.kind is EnvKind.TOOL
        assert env.manager == "uv"

    @pytest.mark.parametrize("lockfile, manager", [
        ("uv.lock", "uv"),
        ("poetry.lock", "poetry"),
        ("pdm.lock", "pdm"),
        ("Pipfile.lock", "pipenv"),
    ])
    def test_lockfile_beside_venv_marks_a_project(self, interpreter, tmp_path,
                                                  lockfile, manager):
        project = tmp_path / "project"
        project.mkdir()
        (project / lockfile).write_text("", encoding="utf-8")
        env = interpreter(_venv(project / ".venv"), base=tmp_path / "base")
        assert env.kind is EnvKind.PROJECT
        assert env.manager == manager

    def test_active_manager_variable_marks_an_out_of_tree_project(
            self, interpreter, tmp_path):
        """Poetry parks its venvs outside the project by default, so no
        lockfile sits beside them."""
        env = interpreter(_venv(tmp_path / "cached-venv"), base=tmp_path / "base",
                          environ={"POETRY_ACTIVE": "1"})
        assert env.kind is EnvKind.PROJECT
        assert env.manager == "poetry"

    def test_conda_prefix_is_recognised(self, interpreter, tmp_path):
        prefix = tmp_path / "conda-env"
        (prefix / "conda-meta").mkdir(parents=True)
        env = interpreter(prefix, base=tmp_path / "base")
        assert env.kind is EnvKind.CONDA

    def test_uv_env_var_wins_over_path_lookup(self, interpreter, tmp_path):
        """uv exports UV under ``uv run``/``uvx``, naming the exact binary in
        use rather than whichever one PATH happens to resolve."""
        env = interpreter(_venv(tmp_path / ".venv"), base=tmp_path / "base",
                          uv="/on/path/uv", environ={"UV": "/exact/uv"})
        assert env.uv == "/exact/uv"


class TestPlanInstall:
    def test_venv_with_pip_installs_directly(self):
        plan = plan_install(_env(EnvKind.VENV), [SPEC])
        assert plan.command == ["/p/bin/python", "-m", "pip", "install",
                                "--disable-pip-version-check", SPEC]

    def test_venv_without_pip_installs_through_uv(self):
        plan = plan_install(_env(EnvKind.VENV, has_pip=False), [SPEC])
        assert plan.command == [
            "/usr/bin/uv", "pip", "install", "--python", "/p/bin/python", SPEC]

    def test_venv_without_pip_or_uv_refuses(self):
        plan = plan_install(_env(EnvKind.VENV, has_pip=False, uv=None), [SPEC])
        assert plan.command is None
        assert "python3 -m venv" in plan.manual

    def test_read_only_prefix_refuses(self):
        plan = plan_install(_env(EnvKind.VENV, writable=False), [SPEC])
        assert plan.command is None

    def test_uv_tool_environment_refuses_with_tool_install(self):
        plan = plan_install(_env(EnvKind.TOOL, manager="uv", has_pip=False), [SPEC])
        assert plan.command is None
        assert "uv tool install" in plan.manual

    def test_uv_tool_companions_use_with_flag(self):
        """``uv tool install`` takes one package; extras ride on ``--with``."""
        plan = plan_install(
            _env(EnvKind.TOOL, manager="uv", has_pip=False), [SPEC, PINNED])
        assert f"--with {PINNED}" in plan.manual

    def test_pipx_companions_use_inject(self):
        plan = plan_install(
            _env(EnvKind.TOOL, manager="pipx", has_pip=False), [SPEC, PINNED])
        assert "pipx install --force" in plan.manual
        assert f"pipx inject optics-framework {PINNED}" in plan.manual

    @pytest.mark.parametrize("manager, command", [
        ("uv", "uv add"),
        ("poetry", "poetry add"),
        ("pdm", "pdm add"),
        ("pipenv", "pipenv install"),
    ])
    def test_project_refuses_with_its_managers_add_command(self, manager, command):
        plan = plan_install(_env(EnvKind.PROJECT, manager=manager), [SPEC])
        assert plan.command is None
        assert plan.manual.strip().startswith(command)

    def test_system_managed_refuses_quoting_the_markers_own_error(self):
        plan = plan_install(
            _env(EnvKind.SYSTEM_MANAGED, pep668_error="use apt install"), [SPEC])
        assert plan.command is None
        assert plan.note == "use apt install"

    def test_conda_refuses(self):
        plan = plan_install(_env(EnvKind.CONDA, manager="conda"), [SPEC])
        assert plan.command is None
        assert "numpy" in plan.note

    def test_unmarked_system_interpreter_installs_directly(self):
        plan = plan_install(_env(EnvKind.SYSTEM), [SPEC])
        assert plan.command == ["/p/bin/python", "-m", "pip", "install",
                                "--disable-pip-version-check", SPEC]

    def test_manual_commands_quote_extras_brackets(self):
        """An unquoted ``pkg[extra]`` is glob syntax in zsh and fails on paste."""
        plan = plan_install(_env(EnvKind.PROJECT, manager="uv"), [SPEC])
        assert f"'{SPEC}'" in plan.manual


class TestDescribe:
    @pytest.mark.parametrize("env", [
        _env(EnvKind.VENV, creator="venv"),
        _env(EnvKind.VENV, has_pip=False),
        _env(EnvKind.SYSTEM),
    ])
    def test_installable_environment_gets_no_row(self, env):
        assert describe(env) is None

    def test_managed_environment_warns_and_hints_the_real_command(self):
        status, detail, hint = describe(
            _env(EnvKind.TOOL, manager="uv", has_pip=False))
        assert status == "warn"
        assert "uv tool install" in hint


class TestRemovalCommand:
    """A removal has the same ownership problem as an install: run it with the
    wrong tool and it either edits another environment or is undone."""

    STALE = "opencv-python"

    def test_venv_with_pip_uninstalls_directly(self):
        assert removal_command(_env(EnvKind.VENV), self.STALE) == (
            "pip uninstall -y opencv-python")

    def test_unmarked_system_interpreter_uninstalls_directly(self):
        assert removal_command(_env(EnvKind.SYSTEM), self.STALE) == (
            "pip uninstall -y opencv-python")

    def test_venv_without_pip_removes_through_uv(self):
        assert removal_command(
            _env(EnvKind.VENV, has_pip=False), self.STALE) == (
            "uv pip uninstall --python /p/bin/python opencv-python")

    def test_venv_without_pip_or_uv_still_names_pip(self):
        """Nothing better exists to suggest, and `describe` already carries a
        row saying this environment takes no changes at all."""
        assert removal_command(
            _env(EnvKind.VENV, has_pip=False, uv=None), self.STALE) == (
            "pip uninstall -y opencv-python")

    def test_uv_tool_environment_names_its_interpreter(self):
        assert removal_command(
            _env(EnvKind.TOOL, manager="uv", has_pip=False), self.STALE) == (
            "uv pip uninstall --python /p/bin/python opencv-python")

    def test_pipx_tool_environment_goes_through_its_own_pip(self):
        """A pipx venv has a pip, but it is not the pip on PATH."""
        assert removal_command(
            _env(EnvKind.TOOL, manager="pipx"), self.STALE) == (
            "pipx runpip optics-framework uninstall -y opencv-python")

    @pytest.mark.parametrize("manager,command", [
        ("uv", "uv sync"),
        ("poetry", "poetry sync"),
        ("pdm", "pdm sync --clean"),
        ("pipenv", "pipenv clean"),
    ])
    def test_lock_managed_environment_prunes_instead(self, manager, command):
        assert removal_command(
            _env(EnvKind.PROJECT, manager=manager), self.STALE) == command


class TestRecoveryCommandIsRunnableWhereItIsRead:
    """The posix form is covered by the refusal tests above, which run on
    posix; only the platform swap needs its own."""

    def test_windows_hint(self, monkeypatch):
        monkeypatch.setattr(environment.os, "name", "nt")
        plan = plan_install(_env(EnvKind.CONDA, manager="conda"), [SPEC])
        assert "Scripts\\Activate.ps1" in plan.manual
        assert "bin/activate" not in plan.manual
