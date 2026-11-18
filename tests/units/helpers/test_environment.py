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


SPEC = "optics-framework[appium]==1.10.3"
PINNED = "appium-python-client==5.0.0"


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


    def test_unmarked_base_interpreter_is_plain_system(self, interpreter, tmp_path):
        base = tmp_path / "usr"
        base.mkdir()
        env = interpreter(base)
        assert env.kind is EnvKind.SYSTEM
        assert env.pep668_error is None


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

    def test_pipx_companions_use_inject(self):
        plan = plan_install(
            _env(EnvKind.TOOL, manager="pipx", has_pip=False), [SPEC, PINNED])
        assert "pipx install --force" in plan.manual
        assert f"pipx inject optics-framework {PINNED}" in plan.manual

    def test_conda_refuses(self):
        plan = plan_install(_env(EnvKind.CONDA, manager="conda"), [SPEC])
        assert plan.command is None
        assert "numpy" in plan.note
