"""One answer to "this engine is enabled in config.yaml but its package is missing".

``optics doctor``, ``optics generate``, ``optics dry_run`` and ``optics execute``
all walk into the same condition, and each used to describe it in its own way: a
machine-wide warning that said nothing about the project, an uncaught ``E0601``
naming an optics module that is in fact present, and silence. Detection and
wording live here, so the commands differ only in the severity their job
warrants — inspect and warn (doctor, generate), or refuse to start (dry_run,
execute, which are about to load the engine).

Nothing here imports an engine: presence is decided from installed package
metadata, so asking the question never costs an import of easyocr or Appium.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from importlib.metadata import PackageNotFoundError, version
from typing import Any, NamedTuple

from optics_framework.helper.setup import EngineBackend, engine_for

# config.yaml sections whose entries name an engine backend.
_ENGINE_SECTIONS = (
    "driver_sources",
    "elements_sources",
    "text_detection",
    "image_detection",
    "llm_models",
)


class MissingEngine(NamedTuple):
    """An engine a project enabled whose Python package is not installed."""

    config_key: str
    engine: EngineBackend
    packages: tuple[str, ...]

    @property
    def detail(self) -> str:
        """The condition, in the words every command reports it with."""
        return ("enabled in config.yaml, but its Python package is not "
                f"installed ({', '.join(self.packages)})")

    @property
    def hint(self) -> str:
        return self.engine.install_hint


def _installed(package: str) -> bool:
    try:
        version(package)
    except PackageNotFoundError:
        return False
    return True


def _is_enabled(entry: Any) -> bool:
    return (entry.get("enabled") is True if isinstance(entry, Mapping)
            else getattr(entry, "enabled", False) is True)


def enabled_keys(config: Any) -> list[str]:
    """Config keys of every ``enabled: true`` engine entry, in config order.

    ``config`` is either a ``config.yaml`` parsed with ``yaml.safe_load`` —
    doctor and generate read it that way, and doctor must not build a
    ``ConfigHandler``, whose constructor writes to the project it is inspecting
    — or a ``Config`` model, which the runners hold. Both answer
    ``.get(section, default)``, which is all this reads.
    """
    keys: list[str] = []
    for section in _ENGINE_SECTIONS:
        for entry in config.get(section, None) or []:
            if not isinstance(entry, Mapping):
                continue
            for key, value in entry.items():
                if _is_enabled(value) and str(key) not in keys:
                    keys.append(str(key))
    return keys


def missing_engines(config: Any) -> list[MissingEngine]:
    """Every engine ``config`` enables whose packages are not all installed."""
    missing: list[MissingEngine] = []
    for key in enabled_keys(config):
        engine = engine_for(key)
        if engine is None:
            continue
        absent = tuple(p for p in engine.packages if not _installed(p))
        if absent:
            missing.append(MissingEngine(key, engine, absent))
    return missing


def report(missing: Sequence[MissingEngine], folder_path: str) -> str:
    """The condition as a block of text, with no severity of its own.

    Callers prefix their own headline: the severity is theirs to choose, the
    description is not."""
    noun = "engine" if len(missing) == 1 else "engines"
    lines = [f"{len(missing)} {noun} this project enables cannot be loaded:"]
    for item in missing:
        lines.append(f"  {item.config_key}: {item.detail}")
        lines.append(f"    → {item.hint}")
    lines.append("Install what is missing, or set `enabled: false` in "
                 f"{os.path.join(folder_path, 'config.yaml')}.")
    return "\n".join(lines)
