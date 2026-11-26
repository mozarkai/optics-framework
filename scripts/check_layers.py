"""Refuse an import from a module to a module in a later layer; an import inside a function counts too."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

PACKAGE = "optics_framework"
ROOT = Path(__file__).resolve().parents[1]

# The first layer imports nothing from the package. A name that ends with "." takes every module below it
# that no full name claims, and "" is the package root.
LAYERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("run context", ("common.test_context",)),
    ("kernel", (
        "common.error", "common.logging_config", "common.async_utils", "common.safe_fs", "common.image_decode",
        "common.google_credentials", "common.caller_policy", "common.instance_fallback", "common.optics_home",
        "common.xpath_index", "helper.version", "helper.abort", "helper.environment",
    )),
    ("utils", ("common.utils",)),
    ("project model", (
        "common.config_handler", "common.models", "common.expression", "common.permissions",
        "common.runner.step_params", "helper.case_selection",
    )),
    ("events and report", (
        "common.events", "common.Junit_eventhandler", "common.eventSDK", "common.execution_tracer",
        "common.error_detection", "common.keyword_outcome", "common.runner.printers", "common.runner.event_stream",
    )),
    ("drivers", ("engines.drivers.", "common.driver_interface", "common.session_liveness", "common.popup_screen")),
    ("locator pipeline", (
        "common.strategies", "common.text_match", "common.elementsource_interface", "common.image_interface",
        "common.text_interface", "engines.elementsources.", "engines.vision_models.",
    )),
    ("agent kernel", (
        "common.agent_core", "common.screen_snapshot", "common.nl_agent", "common.step_curation",
        "common.llm_interface", "engines.llm_models.",
    )),
    ("session assembly", ("common.session_manager", "common.optics_builder", "common.factories", "common.base_factory")),
    ("recovery", ("common.fallback", "common.popup_guard", "common.ai_self_heal")),
    ("keyword API", ("api.", "common.runner.keyword_register")),
    ("project reader", ("common.runner.data_reader",)),
    ("execution engine", ("common.execution", "common.runner.test_runnner", "helper.execute")),
    ("front ends", ("helper.", "optics", "common.expose_api", "common.http_guard", "")),
)


def claim(name: str, module: str) -> int:
    """How strongly a LAYERS name claims a module: 0 for no claim, and a full name wins over a package name."""
    if name == module:
        return len(module) + 2
    return len(name) if name.endswith(".") and f"{module}.".startswith(name) else 0


def layer_of(module: str) -> int | None:
    strength, index = max((claim(name, module), i) for i, (_, names) in enumerate(LAYERS) for name in names)
    return index if strength else None


def package_modules() -> dict[str, Path]:
    modules = {}
    for path in (ROOT / PACKAGE).rglob("*.py"):
        parts = path.relative_to(ROOT / PACKAGE).with_suffix("").parts
        if "samples" not in parts:
            modules[".".join(p for p in parts if p != "__init__")] = path
    return modules


def imported_names(node: ast.AST, package: list[str]) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if not isinstance(node, ast.ImportFrom):
        return []
    stem = node.module or ""
    if node.level:
        stem = ".".join([PACKAGE, *package[: len(package) - node.level + 1], *filter(None, [node.module])])
    return [stem, *(f"{stem}.{alias.name}" for alias in node.names)]


def resolve(name: str, modules: dict[str, Path]) -> str | None:
    """The package module that an imported name is, or is defined in; None for a name from outside the package."""
    if name != PACKAGE and not name.startswith(PACKAGE + "."):
        return None
    target = name[len(PACKAGE) + 1:]
    while target and target not in modules:
        target = target.rpartition(".")[0]
    return target if target or name == PACKAGE else None


def targets(module: str, path: Path, modules: dict[str, Path]) -> set[tuple[str, int]]:
    """Each package module that ``module`` imports, with the line of the import."""
    package = module.split(".") if path.name == "__init__.py" else module.split(".")[:-1]
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        for name in imported_names(node, package):
            target = resolve(name, modules)
            if target is not None and target != module:
                found.add((target, node.lineno))
    return found


def problems() -> list[str]:
    modules = package_modules()
    layers = {module: layer_of(module) for module in modules}
    found = []
    for module, path in sorted(modules.items()):
        imports = targets(module, path, modules)
        where = path.relative_to(ROOT).as_posix()
        if layers[module] is None:
            if imports:
                found.append(f"{where}: {module} has no layer; add it to LAYERS in scripts/check_layers.py")
            continue
        for target, line in sorted(imports):
            higher = layers[target]
            if higher is not None and higher > layers[module]:
                found.append(
                    f"{where}:{line}: {module} ({LAYERS[layers[module]][0]}) imports "
                    f"{target or PACKAGE} ({LAYERS[higher][0]}), a later layer"
                )
    return found


def main() -> int:
    found = problems()
    for line in found:
        print(line)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
