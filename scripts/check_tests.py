"""Refuse test shapes that guard no behavior, and keep tests/ inside its line budget."""
from __future__ import annotations

import ast
import subprocess  # nosec B404
import sys
from pathlib import Path

BUDGET = 10_000
SLEEP_FUNCS = {"time.sleep", "asyncio.sleep"}
SOURCE_READS = {"inspect.getsource", "inspect.getsourcelines", "inspect.getsourcefile"}
TAUTOLOGY_CALLS = {"hasattr", "callable"}


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def is_test(node: ast.AST) -> bool:
    return isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")


def proves_something(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.Call):
            name = dotted(child.func).rsplit(".", 1)[-1]
            if name in {"raises", "warns", "fail"} or name.startswith(("assert", "_assert", "check", "_check", "expect", "_expect")):
                return True
    return False


def tautology(test: ast.expr) -> str | None:
    if isinstance(test, ast.Constant):
        return "asserts a constant"
    if isinstance(test, ast.Call) and dotted(test.func) in TAUTOLOGY_CALLS:
        return f"asserts {dotted(test.func)}(), which only proves the code was written"
    if isinstance(test, ast.Compare) and len(test.comparators) == 1 and ast.dump(test.left) == ast.dump(test.comparators[0]):
        return "compares a value with itself"
    return None


def call_problem(node: ast.Call, polls: set[int]) -> str | None:
    name = dotted(node.func)
    fixed = node.args and isinstance(node.args[0], ast.Constant) and node.args[0].value
    if name in SLEEP_FUNCS and fixed and id(node) not in polls:
        return f"{name}() with a fixed delay: wait for the event or patch the clock"
    if name in SOURCE_READS:
        return f"{name}() greps source: test what the code does, not how it is written"
    return None


def node_problem(node: ast.AST, polls: set[int]) -> str | None:
    if isinstance(node, ast.Call):
        return call_problem(node, polls)
    if isinstance(node, ast.Assert):
        return tautology(node.test)
    if is_test(node) and not proves_something(node):
        return f"{node.name} asserts nothing: a test that cannot fail guards nothing"
    return None


def check_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    polls = {id(n) for loop in ast.walk(tree) if isinstance(loop, (ast.For, ast.AsyncFor, ast.While)) for n in ast.walk(loop)}
    return [f"{path}:{node.lineno}: {why}" for node in ast.walk(tree) if (why := node_problem(node, polls))]


def tracked_test_lines() -> int:
    files = subprocess.run(["git", "ls-files", "-z", "tests"], capture_output=True, text=True, check=True).stdout  # nosec B603 B607
    return sum(len(Path(f).read_bytes().splitlines()) for f in files.split("\0") if f and Path(f).is_file())


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv if a.endswith(".py") and Path(a).name.startswith(("test_", "conftest"))]
    problems = [p for path in paths for p in check_file(path)]
    lines = tracked_test_lines()
    if lines > BUDGET:
        problems.append(f"tests/ holds {lines} lines, over the {BUDGET} budget: delete or merge tests before adding more")
    if problems:
        print("\n".join(problems), file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
