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
    plan_install,
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

    def test_uvx_cache_environment_is_a_tool_environment(self, interpreter, tmp_path):
        prefix = _venv(tmp_path / "archive-v0" / "abc123", creator="uv")
        env = interpreter(prefix, base=tmp_path / "base", pip=False)
        assert env.kind is EnvKind.TOOL
        assert env.manager == "uv"


class TestPlanInstall:

    def test_venv_without_pip_installs_through_uv(self):
        plan = plan_install(_env(EnvKind.VENV, has_pip=False), [SPEC])
        assert plan.command == [
            "/usr/bin/uv", "pip", "install", "--python", "/p/bin/python", SPEC]

    def test_venv_without_pip_or_uv_refuses(self):
        plan = plan_install(_env(EnvKind.VENV, has_pip=False, uv=None), [SPEC])
        assert plan.command is None
        # The hint itself is platform-specific (python3 vs python, bin/activate
        # vs Scripts\Activate.ps1); what matters is that it tells you to build a
        # venv rather than handing back a command.
        assert "-m venv .venv" in plan.manual

    def test_read_only_prefix_refuses(self):
        plan = plan_install(_env(EnvKind.VENV, writable=False), [SPEC])
        assert plan.command is None

    def test_uv_tool_environment_refuses_with_tool_install(self):
        plan = plan_install(_env(EnvKind.TOOL, manager="uv", has_pip=False), [SPEC])
        assert plan.command is None
        assert "uv tool install" in plan.manual

    def test_pipx_companions_use_inject(self):
        plan = plan_install(
            _env(EnvKind.TOOL, manager="pipx", has_pip=False), [SPEC, PINNED])
        assert "pipx install --force" in plan.manual
        assert f"pipx inject optics-framework {PINNED}" in plan.manual

    def test_system_managed_refuses_quoting_the_markers_own_error(self):
        plan = plan_install(
            _env(EnvKind.SYSTEM_MANAGED, pep668_error="use apt install"), [SPEC])
        assert plan.command is None
        assert plan.note == "use apt install"

    def test_conda_refuses(self):
        plan = plan_install(_env(EnvKind.CONDA, manager="conda"), [SPEC])
        assert plan.command is None
        assert "numpy" in plan.note
