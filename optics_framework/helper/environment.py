"""Where an engine install is allowed to write.

*Can* we write here and *may* we are independent questions: uv tool and pipx
environments ship no pip, lock-managed project venvs undo anything installed
behind their manager's back, and PEP 668 base interpreters refuse outright — and
the environments without pip are disproportionately the ones that forbid
mutation, so "fall back to uv when pip is missing" answers the wrong one.

Classification uses stdlib signals only, no subprocesses, so ``abort`` can
import this while the rest of the package is still failing to load.
"""
import configparser
import os
import shlex
import shutil
import sys
import sysconfig
from dataclasses import dataclass
from enum import StrEnum
from importlib.util import find_spec
from pathlib import Path

_PEP668_FALLBACK = (
    "This interpreter is managed by your operating system or Python installer, "
    "so it refuses package installs.")

_LOCKFILES = {
    "uv.lock": "uv",
    "poetry.lock": "poetry",
    "pdm.lock": "pdm",
    "Pipfile.lock": "pipenv",
}

_ACTIVE_MANAGER_VARS = {
    "POETRY_ACTIVE": "poetry",
    "PIPENV_ACTIVE": "pipenv",
    "PDM_PROJECT_ROOT": "pdm",
}

_ADD_COMMANDS = {
    "uv": "uv add",
    "poetry": "poetry add",
    "pdm": "pdm add",
    "pipenv": "pipenv install",
}

# Stands in for a real requirement when the caller only wants to know whether
# an environment refuses installs, so the rendered command reads as a template.
_PLACEHOLDER_SPEC = "optics-framework[<engine>]"

_MAKE_VENV_POSIX = ("python3 -m venv .venv && . .venv/bin/activate && "
                    "pip install optics-framework")
_MAKE_VENV_WINDOWS = ("python -m venv .venv; .venv\\Scripts\\Activate.ps1; "
                      "pip install optics-framework")


def _make_venv_hint() -> str:
    """The "put it in a virtualenv" recovery command for this platform.

    Every refusal ends in this advice, so it has to be runnable where it is
    read: a POSIX one-liner is not pasteable in PowerShell."""
    return _MAKE_VENV_WINDOWS if os.name == "nt" else _MAKE_VENV_POSIX


class EnvKind(StrEnum):
    """How the running interpreter is owned."""

    VENV = "venv"
    PROJECT = "project"
    TOOL = "tool"
    SYSTEM_MANAGED = "system-managed"
    SYSTEM = "system"
    CONDA = "conda"


@dataclass(frozen=True)
class Environment:
    kind: EnvKind
    python: str
    prefix: str
    has_pip: bool
    writable: bool
    creator: str | None = None
    manager: str | None = None
    uv: str | None = None
    pep668_error: str | None = None


@dataclass(frozen=True)
class InstallPlan:
    """Either a command to run (``command``) or a refusal carrying the command
    the user should run themselves (``manual``)."""

    command: list[str] | None
    note: str = ""
    manual: str | None = None

    def describe(self) -> str:
        if self.manual:
            return f"{self.note}\n\n{self.manual}" if self.note else self.manual
        return self.note


def _pyvenv_creator(prefix: Path) -> str | None:
    """Tool that built this venv, per ``pyvenv.cfg``; None when it is not a venv.

    uv and virtualenv each stamp a version key under their own name; the stdlib
    ``venv`` module stamps neither, so a readable file with no such key is the
    stdlib's."""
    try:
        text = (prefix / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        key, separator, _ = line.partition("=")
        if separator and key.strip() in ("uv", "virtualenv"):
            return key.strip()
    return "venv"


def _pep668_error(in_venv: bool) -> str | None:
    """Text of the EXTERNALLY-MANAGED marker, or None when installs are allowed.

    PEP 668 binds the base interpreter only. The marker lives in the stdlib
    directory, which a venv inherits from the base it was built from, so testing
    for it unconditionally would flag every healthy venv created from a
    Homebrew, Debian or uv-managed Python."""
    if in_venv:
        return None
    stdlib = sysconfig.get_path("stdlib", sysconfig.get_default_scheme())
    try:
        text = (Path(stdlib) / "EXTERNALLY-MANAGED").read_text(encoding="utf-8")
    except OSError:
        return None
    parser = configparser.ConfigParser()
    try:
        parser.read_string(text)
    except configparser.Error:
        return _PEP668_FALLBACK
    error = parser.get("externally-managed", "Error", fallback="").strip()
    return error or _PEP668_FALLBACK


def _project_manager(prefix: Path) -> str | None:
    """Dependency manager that owns this venv, or None for a plain one.

    A lockfile beside the venv covers the conventional ``<project>/.venv``
    layout; the environment variables cover managers that park their venvs
    outside the project tree, which is Poetry's default."""
    for variable, manager in _ACTIVE_MANAGER_VARS.items():
        if os.environ.get(variable):
            return manager
    for lockfile, manager in _LOCKFILES.items():
        if (prefix.parent / lockfile).is_file():
            return manager
    return None


def _tool_manager(prefix: Path) -> str | None:
    """Tool installer that owns this environment, or None.

    ``uvx`` builds ephemeral environments under its cache rather than the tool
    directory, so they carry no receipt and are recognised by path instead."""
    if (prefix / "uv-receipt.toml").is_file():
        return "uv"
    if (prefix / "pipx_metadata.json").is_file():
        return "pipx"
    if "archive-v0" in prefix.parts:
        return "uv"
    return None


def detect() -> Environment:
    prefix = Path(sys.prefix)
    in_venv = sys.prefix != sys.base_prefix
    shared = {
        "python": sys.executable,
        "prefix": str(prefix),
        "has_pip": find_spec("pip") is not None,
        "writable": os.access(sysconfig.get_path("purelib"), os.W_OK),
        "uv": os.environ.get("UV") or shutil.which("uv"),
    }

    if (prefix / "conda-meta").is_dir():
        return Environment(kind=EnvKind.CONDA, manager="conda", **shared)

    tool = _tool_manager(prefix)
    if tool:
        return Environment(kind=EnvKind.TOOL, manager=tool, **shared)

    creator = _pyvenv_creator(prefix)
    if in_venv:
        manager = _project_manager(prefix)
        return Environment(
            kind=EnvKind.PROJECT if manager else EnvKind.VENV,
            creator=creator, manager=manager, **shared)

    error = _pep668_error(in_venv)
    return Environment(
        kind=EnvKind.SYSTEM_MANAGED if error else EnvKind.SYSTEM,
        creator=creator, pep668_error=error, **shared)


def project_add_command(env: Environment) -> str:
    """The command that adds a dependency through this environment's manager."""
    return _ADD_COMMANDS.get(env.manager or "uv", "uv add")


def _tool_manual(manager: str, specs: list[str]) -> str:
    """Reinstall command for a tool environment.

    Tool installers own the whole environment, so extras arrive by reinstalling
    the tool rather than by adding to it: uv takes companions via ``--with``,
    pipx via a separate ``inject``."""
    head, *rest = (shlex.quote(spec) for spec in specs)
    if manager == "uv":
        companions = "".join(f" --with {spec}" for spec in rest)
        return f"  uv tool install {head}{companions}"
    lines = [f"  pipx install --force {head}"]
    if rest:
        lines.append(f"  pipx inject optics-framework {' '.join(rest)}")
    return "\n".join(lines)


def _direct_plan(env: Environment, specs: list[str]) -> InstallPlan:
    if not env.writable:
        return InstallPlan(
            None,
            f"{env.prefix} is read-only, so nothing can be installed into it.",
            f"  {_make_venv_hint()}")
    if env.has_pip:
        # --disable-pip-version-check: pip's "a new release is available"
        # notice is noise in the middle of a guided first install.
        return InstallPlan([env.python, "-m", "pip", "install",
                            "--disable-pip-version-check", *specs])
    if env.uv:
        return InstallPlan(
            [env.uv, "pip", "install", "--python", env.python, *specs],
            "This environment has no pip; installing with uv instead.")
    return InstallPlan(
        None,
        "This environment has neither pip nor uv, so engines cannot be "
        "installed into it.",
        f"  {_make_venv_hint()}")


def plan_install(env: Environment, specs: list[str]) -> InstallPlan:
    """Decide how ``specs`` reach ``env`` — by command, or by telling the user.

    A refusal is deliberate wherever a manager owns the environment: installing
    anyway would appear to work and then be undone by the next ``uv sync``,
    ``uv tool upgrade`` or lock resolution."""
    quoted = " ".join(shlex.quote(spec) for spec in specs)

    if env.kind is EnvKind.CONDA:
        return InstallPlan(
            None,
            "Conda is not supported: easyocr and optics-framework need "
            "conflicting numpy majors under Conda.",
            f"  {_make_venv_hint()}")

    if env.kind is EnvKind.TOOL:
        manager = env.manager or "uv"
        return InstallPlan(
            None,
            f"This is a {manager} tool environment, which {manager} rebuilds on "
            "every upgrade — anything installed into it directly is lost.",
            _tool_manual(manager, specs))

    if env.kind is EnvKind.PROJECT:
        manager = env.manager or "uv"
        return InstallPlan(
            None,
            f"This environment is managed by {manager}, which prunes packages "
            "its lockfile does not list.",
            f"  {project_add_command(env)} {quoted}")

    if env.kind is EnvKind.SYSTEM_MANAGED:
        return InstallPlan(
            None, env.pep668_error or _PEP668_FALLBACK, f"  {_make_venv_hint()}")

    return _direct_plan(env, specs)


def replace_command(env: Environment, stale: str, restore: str) -> str:
    """One line that drops ``stale`` and lays ``restore`` back down.

    Reinstalling is not padding. Two distributions that share an import name
    share their files on disk too, because the later install overwrote the
    earlier's — so uninstalling one deletes files the other's metadata still
    claims, and a bare uninstall trades an ambiguous install for a broken one.

    A lock-managed environment needs no case of its own here, unlike
    `plan_install`: dropping a distribution the lockfile does not list moves
    towards the lock rather than behind the manager's back, and ``restore``
    carries the version already recorded, so nothing is re-resolved. A tool
    environment still does, because its manager rebuilds it from current
    metadata, which is both steps at once."""
    restore = shlex.quote(restore)
    if env.kind is EnvKind.TOOL:
        return ("pipx reinstall optics-framework" if env.manager == "pipx"
                else "uv tool install --reinstall optics-framework")
    if not env.has_pip and env.uv:
        python = shlex.quote(env.python)
        return (f"uv pip uninstall --python {python} {stale} && "
                f"uv pip install --python {python} --reinstall {restore}")
    return (f"pip uninstall -y {stale} && "
            f"pip install --force-reinstall {restore}")


def describe(env: Environment) -> tuple[str, str, str] | None:
    """``(status, detail, hint)`` for the ``optics doctor`` environment row, or
    None when this environment takes engine installs.

    Only a refusal earns a row: doctor is a to-do list, and an environment that
    installs fine leaves the user nothing to act on."""
    plan = plan_install(env, [_PLACEHOLDER_SPEC])
    if plan.command is not None:
        return None
    return "warn", f"{env.kind} — {plan.note}", plan.manual or ""
