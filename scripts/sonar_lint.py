"""Run the SonarCloud Python rules this project keeps hitting, offline, on the lines that changed."""

import argparse
import ast
import builtins
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import tempfile
import tokenize
from collections import defaultdict
from collections.abc import Callable, Iterator
from pathlib import Path

Finding = tuple[int, str, str]

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}
NOQA = re.compile(
    r"#\s*noqa\s*:\s*[A-Z]+\d+(?:\s*,\s*[A-Z]+\d+)*(?P<rest>.*)", re.IGNORECASE
)
MIN_DUPLICATED_LENGTH = 5
DUPLICATION_THRESHOLD = 3
CONSECUTIVE_APPENDS = 3
MAX_COGNITIVE_COMPLEXITY = 15
BLOCK_RULES = {"S8495", "S9152", "S5778"}
RUFF_RULES = {
    "PT018": "S9073",
    "PIE810": "S8513",
    "TRY400": "S8572",
    "B023": "S1515",
    "TRY002": "S112",
    "PLR0913": "S107",
    "FIX002": "S1135",
}


def _is_test_file(path: Path) -> bool:
    return "tests" in path.parts or path.name.startswith("test_")


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        first = body[0] if isinstance(body, list) and body else None
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def _exempt_literals(tree: ast.AST) -> set[int]:
    skip = _docstring_nodes(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            skip.update(id(v) for v in node.values)
        elif (
            isinstance(node, ast.Subscript)
            or isinstance(node, ast.Call)
            and _name(node.func) in {"getattr", "setattr", "hasattr"}
        ):
            skip.update(id(c) for c in ast.walk(node) if isinstance(c, ast.Constant))
    return skip


def _is_sentence(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and len(node.value) >= MIN_DUPLICATED_LENGTH
        and " " in node.value.strip()
    )


def duplicated_literals(tree: ast.AST, path: Path) -> Iterator[Finding]:
    if _is_test_file(path):
        return
    skip = _exempt_literals(tree)
    seen: dict[str, list[ast.Constant]] = defaultdict(list)
    for node in ast.walk(tree):
        if _is_sentence(node) and id(node) not in skip:
            seen[node.value].append(node)
    for value, nodes in seen.items():
        if len(nodes) >= DUPLICATION_THRESHOLD:
            for node in nodes:
                yield (
                    node.lineno,
                    "S1192",
                    f"Define a constant instead of duplicating {value[:40]!r} {len(nodes)} times.",
                )


def nested_conditionals(tree: ast.AST, _: Path) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.IfExp) and any(
            isinstance(c, ast.IfExp) for c in (node.test, node.body, node.orelse)
        ):
            yield (
                node.lineno,
                "S3358",
                "Extract this nested conditional expression into an independent statement.",
            )


def _endpoint_codes(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int] | None:
    for deco in func.decorator_list:
        if (
            isinstance(deco, ast.Call)
            and isinstance(deco.func, ast.Attribute)
            and deco.func.attr in HTTP_VERBS
        ):
            for kw in deco.keywords:
                if kw.arg == "responses" and isinstance(kw.value, ast.Dict):
                    return {_status(k) for k in kw.value.keys if k is not None} - {None}
            return set()
    return None


def _status(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    match = re.search(r"HTTP_(\d{3})", ast.unparse(node))
    return int(match.group(1)) if match else None


def _raised_codes(func: ast.AST) -> Iterator[tuple[int, int]]:
    for node in _own_nodes(func):
        if (
            isinstance(node, ast.Raise)
            and isinstance(node.exc, ast.Call)
            and _name(node.exc.func) == "HTTPException"
        ):
            code = next(
                (
                    _status(kw.value)
                    for kw in node.exc.keywords
                    if kw.arg == "status_code"
                ),
                None,
            )
            if code is None and node.exc.args:
                code = _status(node.exc.args[0])
            if code is not None:
                yield node.lineno, code


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    return (
        f
        for f in ast.walk(tree)
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def _reachable(start: str, funcs: dict[str, ast.AST]) -> set[str]:
    reached, pending = set(), [start]
    while pending:
        name = pending.pop()
        if name not in reached:
            reached.add(name)
            pending += [
                _name(c.func)
                for c in _own_nodes(funcs[name])
                if isinstance(c, ast.Call) and _name(c.func) in funcs
            ]
    return reached


def undocumented_http_exceptions(tree: ast.AST, _: Path) -> Iterator[Finding]:
    funcs = {f.name: f for f in _functions(tree)}
    for endpoint in funcs.values():
        documented = _endpoint_codes(endpoint)
        if documented is None:
            continue
        for name in _reachable(endpoint.name, funcs):
            for line, code in _raised_codes(funcs[name]):
                if code not in documented:
                    yield (
                        line,
                        "S8415",
                        f'Document this HTTPException with status code {code} in the "responses" parameter.',
                    )


def _name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _root(node: ast.AST) -> ast.AST:
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node


def _imported_modules(tree: ast.Module) -> set[str]:
    modules = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            modules.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "optics_framework"
        ):
            modules.update(a.asname or a.name for a in node.names if a.name.islower())
    return modules


def _assigned_targets(func: ast.AST) -> Iterator[tuple[int, ast.AST]]:
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            yield from ((node.lineno, t) for t in node.targets)
        elif isinstance(node, ast.AugAssign):
            yield node.lineno, node.target


def global_state_in_tests(tree: ast.AST, path: Path) -> Iterator[Finding]:
    if not _is_test_file(path):
        return
    modules = _imported_modules(tree)
    for func in _functions(tree):
        if not func.name.startswith("test"):
            continue
        for line, target in _assigned_targets(func):
            root = _root(target)
            if target is not root and isinstance(root, ast.Name) and root.id in modules:
                yield (
                    line,
                    "S8997",
                    'Use the "monkeypatch" fixture to modify this global state.',
                )


def inconsistent_tuple_returns(tree: ast.AST, _: Path) -> Iterator[Finding]:
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        lengths = {
            len(node.value.elts)
            for node in _own_nodes(func)
            if isinstance(node, ast.Return)
            and isinstance(node.value, ast.Tuple)
            and not any(isinstance(e, ast.Starred) for e in node.value.elts)
        }
        if len(lengths) > 1:
            yield (
                func.lineno,
                "S8495",
                "Refactor this function to always return tuples of the same length.",
            )


def _own_nodes(func: ast.AST) -> Iterator[ast.AST]:
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(
            node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            stack.extend(ast.iter_child_nodes(node))


def _is_raises(item: ast.withitem) -> bool:
    call = item.context_expr
    return isinstance(call, ast.Call) and _name(call.func) == "raises"


def several_calls_in_raises(tree: ast.AST, path: Path) -> Iterator[Finding]:
    if not _is_test_file(path):
        return
    for node in ast.walk(tree):
        if isinstance(node, (ast.With, ast.AsyncWith)) and any(
            _is_raises(i) for i in node.items
        ):
            calls = [
                c for s in node.body for c in ast.walk(s) if isinstance(c, ast.Call)
            ]
            if len(node.body) > 1 or len(calls) > 1:
                yield (
                    node.lineno,
                    "S5778",
                    "Refactor this exception test to have only one invocation possibly throwing an exception.",
                )


def _body_touches(loop: ast.AST, copied: ast.AST) -> bool:
    root = _root(copied)
    if not isinstance(loop, (ast.For, ast.AsyncFor)) or not isinstance(root, ast.Name):
        return False
    return any(
        isinstance(n, ast.Name) and n.id == root.id
        for stmt in loop.body
        for n in ast.walk(stmt)
    )


def needless_list_in_loop(tree: ast.AST, _: Path) -> Iterator[Finding]:
    for node in ast.walk(tree):
        iters = [node.iter] if isinstance(node, (ast.For, ast.AsyncFor)) else []
        iters += [g.iter for g in getattr(node, "generators", [])]
        for it in iters:
            if (
                isinstance(it, ast.Call)
                and len(it.args) == 1
                and not _body_touches(node, it.args[0])
                and _name(it.func) in {"list", "tuple"}
                and len(it.args) == 1
                and not it.keywords
            ):
                yield (
                    it.lineno,
                    "S7504",
                    f'Remove this unnecessary "{_name(it.func)}()" call on an already iterable object.',
                )


def _builtin_exception(node: ast.AST) -> type | None:
    obj = getattr(builtins, node.id, None) if isinstance(node, ast.Name) else None
    return obj if isinstance(obj, type) and issubclass(obj, BaseException) else None


def redundant_except_classes(tree: ast.AST, _: Path) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple):
            classes = [c for c in map(_builtin_exception, node.type.elts) if c]
            if any(a is not b and issubclass(a, b) for a in classes for b in classes):
                yield (
                    node.lineno,
                    "S5713",
                    "Remove this redundant Exception class; it derives from another listed class.",
                )


def _is_stub(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = [
        s
        for s in func.body
        if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
    ]
    return all(
        isinstance(s, (ast.Pass, ast.Raise))
        or isinstance(s, ast.Expr)
        and isinstance(s.value, ast.Constant)
        for s in body
    )


def _unused(
    func: ast.FunctionDef | ast.AsyncFunctionDef, skip_first: bool
) -> Iterator[Finding]:
    if func.decorator_list or func.name.startswith("__") or _is_stub(func):
        return
    used = {n.id for n in ast.walk(func) if isinstance(n, ast.Name)}
    params = func.args.posonlyargs + func.args.args + func.args.kwonlyargs
    for arg in params[1:] if skip_first else params:
        if arg.arg not in used and not arg.arg.startswith("_"):
            yield (
                arg.lineno,
                "S1172",
                f'Remove the unused function parameter "{arg.arg}".',
            )


def unused_parameters(tree: ast.AST, path: Path) -> Iterator[Finding]:
    if _is_test_file(path):
        return
    called = {id(c.func) for c in ast.walk(tree) if isinstance(c, ast.Call)}
    referenced = {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name)
        and isinstance(n.ctx, ast.Load)
        and id(n) not in called
    }
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name not in referenced
        ):
            yield from _unused(node, skip_first=False)
    baseless = {id(c) for c in _baseless_classes(tree)}
    for cls in (c for c in ast.walk(tree) if isinstance(c, ast.ClassDef)):
        for func in cls.body:
            if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                id(cls) in baseless or func.name.startswith("_")
            ):
                yield from _unused(func, skip_first=True)


def _baseless_classes(tree: ast.AST) -> Iterator[ast.ClassDef]:
    return (
        c
        for c in ast.walk(tree)
        if isinstance(c, ast.ClassDef)
        and not c.decorator_list
        and all(_name(b) == "object" for b in c.bases)
    )


def _yields_outside_finally(node: ast.AST, guarded: bool = False) -> bool:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return False
    if isinstance(node, ast.Try) and node.finalbody:
        return any(_yields_outside_finally(c, True) for c in node.body) or any(
            _yields_outside_finally(c, guarded)
            for c in node.handlers + node.orelse + node.finalbody
        )
    if isinstance(node, (ast.With, ast.AsyncWith)):
        guarded = True
    if isinstance(node, (ast.Yield, ast.YieldFrom)):
        return not guarded
    return any(_yields_outside_finally(c, guarded) for c in ast.iter_child_nodes(node))


def unguarded_context_managers(tree: ast.AST, _: Path) -> Iterator[Finding]:
    for func in ast.walk(tree):
        if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            _name(d) in {"contextmanager", "asynccontextmanager"}
            for d in func.decorator_list
        ):
            has_cleanup = any(
                not isinstance(s, ast.Expr)
                or not isinstance(s.value, (ast.Yield, ast.YieldFrom))
                for s in func.body[_yield_index(func) + 1 :]
            )
            if has_cleanup and any(_yields_outside_finally(s) for s in func.body):
                yield (
                    func.lineno,
                    "S9152",
                    "Wrap the yield in try/finally so cleanup runs when the block raises.",
                )


def _yield_index(func: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    for i, stmt in enumerate(func.body):
        if any(isinstance(n, (ast.Yield, ast.YieldFrom)) for n in ast.walk(stmt)):
            return i
    return len(func.body)


def _append_target(stmt: ast.stmt) -> str | None:
    call = stmt.value if isinstance(stmt, ast.Expr) else None
    if (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr == "append"
    ):
        return (
            ast.unparse(call.func.value)
            if len(call.args) == 1 and not call.keywords
            else None
        )
    return None


def _blocks(tree: ast.AST) -> Iterator[list[ast.stmt]]:
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if isinstance(stmts, list):
                yield stmts


def consecutive_appends(tree: ast.AST, _: Path) -> Iterator[Finding]:
    for stmts in _blocks(tree):
        targets = [_append_target(s) for s in stmts]
        for i, target in enumerate(targets):
            starts_run = target and (i == 0 or targets[i - 1] != target)
            if (
                starts_run
                and targets[i : i + CONSECUTIVE_APPENDS]
                == [target] * CONSECUTIVE_APPENDS
            ):
                yield (
                    stmts[i].lineno,
                    "S9454",
                    'Use "list.extend()" instead of consecutive "append()" calls.',
                )


def typing_unions(tree: ast.AST, _: Path) -> Iterator[Finding]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and _name(node.value) == "Union":
            yield (
                node.lineno,
                "S6546",
                'Use a union type expression ("X | Y") instead of "Union".',
            )


CHECKS: list[Callable[[ast.AST, Path], Iterator[Finding]]] = [
    duplicated_literals,
    nested_conditionals,
    undocumented_http_exceptions,
    global_state_in_tests,
    inconsistent_tuple_returns,
    several_calls_in_raises,
    needless_list_in_loop,
    redundant_except_classes,
    unused_parameters,
    unguarded_context_managers,
    consecutive_appends,
    typing_unions,
]


def suppression_comments(path: Path) -> tuple[set[int], list[Finding]]:
    suppressed, findings = set(), []
    with path.open("rb") as fh:
        for tok in tokenize.tokenize(fh.readline):
            if tok.type != tokenize.COMMENT:
                continue
            row = tok.start[0]
            if "NOSONAR" in tok.string:
                suppressed.add(row)
            match = NOQA.search(tok.string)
            if match and "," in match.group("rest"):
                findings.append(
                    (
                        row,
                        "S7632",
                        "Fix the syntax of this issue suppression comment: no commas after the codes.",
                    )
                )
    return suppressed, findings


def ruff_findings(files: list[Path]) -> dict[Path, list[Finding]]:
    out = subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--isolated",
            "--no-fix",
            "--exit-zero",
            "--output-format",
            "json",
            "--target-version",
            "py312",
            "--select",
            ",".join(RUFF_RULES),
            "--config",
            "lint.pylint.max-args=13",
            *map(str, files),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    found: dict[Path, list[Finding]] = defaultdict(list)
    for item in json.loads(out or "[]"):
        rule = RUFF_RULES.get(item["code"] or "")
        if not rule:
            continue
        found[Path(item["filename"]).resolve()].append(
            (item["location"]["row"], rule, f"{item['message']} ({item['code']})")
        )
    return found


def complexity_findings(
    files: list[Path],
) -> dict[Path, list[tuple[int, int, str, str]]]:
    exe = shutil.which("complexipy")
    if not exe:
        sys.exit(
            "sonar-lint: complexipy is not installed; run it through pre-commit or `pip install complexipy`."
        )
    report = Path(tempfile.mkdtemp(prefix="sonar-lint-")) / "complexity.json"
    try:
        subprocess.run(
            [
                exe,
                "--output-format",
                "json",
                "--output",
                str(report),
                "-q",
                "-i",
                "--",
                *map(str, files),
            ],  # nosec B603
            capture_output=True,
            check=True,
        )
        items = json.loads(report.read_text())
    finally:
        shutil.rmtree(report.parent, ignore_errors=True)
    found: dict[Path, list[tuple[int, int, str, str]]] = defaultdict(list)
    for item in items:
        if item["complexity"] > MAX_COGNITIVE_COMPLEXITY:
            message = (
                f"Refactor {item['function_name']} to reduce its Cognitive Complexity from "
                f"{item['complexity']} to the {MAX_COGNITIVE_COMPLEXITY} allowed."
            )
            found[Path(item["path"]).resolve()].append(
                (item["line_start"], item["line_end"], "S3776", message)
            )
    return found


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout  # nosec B603 B607


def _tree_of(base: str) -> str:
    try:
        sha = _git(
            "rev-parse", "--verify", "--end-of-options", f"{base}^{{tree}}"
        ).strip()
    except subprocess.CalledProcessError:
        sha = ""
    if not re.fullmatch(r"[0-9a-f]{40,64}", sha):
        sys.exit(f"sonar-lint: {base!r} is not a git ref")
    return sha


def changed_lines(base: str) -> dict[Path, set[int] | None]:
    root = Path(_git("rev-parse", "--show-toplevel").strip())
    lines: dict[Path, set[int] | None] = defaultdict(set)
    current = None
    diff = _git(
        "-c",
        "core.quotePath=false",
        "diff",
        "-U0",
        "--no-color",
        "--no-ext-diff",
        _tree_of(base),
        "--",
        "*.py",
    )
    for row in diff.splitlines():
        if row.startswith("+++ "):
            current = None if row == "+++ /dev/null" else (root / row[6:]).resolve()
        elif row.startswith("@@") and current:
            start, _, count = row.split("+", 1)[1].split(" ", 1)[0].partition(",")
            lines[current].update(range(int(start), int(start) + int(count or 1)))
    for name in _git(
        "ls-files", "-z", "--others", "--exclude-standard", "--", "*.py"
    ).split("\0"):
        if name:
            lines[(root / name).resolve()] = None
    return lines


def file_findings(
    file: Path,
    extra: list[Finding],
    spans: list[tuple[int, int, str, str]],
    touched: set[int] | None,
) -> list[Finding]:
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        suppressed, findings = suppression_comments(file)
    except (SyntaxError, tokenize.TokenError):
        return []
    for check in CHECKS:
        findings.extend(check(tree, file))
    findings += extra
    if touched is not None:
        ends = _block_ends(tree)
        findings = [f for f in findings if _touches(f, ends, touched)]
        spans = [s for s in spans if touched & set(range(s[0], s[1] + 1))]
    findings += [(start, rule, message) for start, _, rule, message in spans]
    return sorted(f for f in set(findings) if f[0] not in suppressed)


def _block_ends(tree: ast.AST) -> dict[int, int]:
    blocks = (ast.FunctionDef, ast.AsyncFunctionDef, ast.With, ast.AsyncWith)
    return {
        n.lineno: n.end_lineno or n.lineno
        for n in ast.walk(tree)
        if isinstance(n, blocks)
    }


def _touches(finding: Finding, ends: dict[int, int], touched: set[int]) -> bool:
    line, rule, _ = finding
    end = ends.get(line, line) if rule in BLOCK_RULES else line
    return bool(touched & set(range(line, end + 1)))


def _python_files(paths: list[Path]) -> list[Path]:
    files = {
        f.resolve() for p in paths for f in (p.rglob("*.py") if p.is_dir() else [p])
    }
    return sorted(f for f in files if f.suffix == ".py" and f.exists())


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="files or folders (default: the changed Python files)",
    )
    parser.add_argument(
        "--base",
        default=os.environ.get("SONAR_LINT_BASE", "HEAD"),
        help="report only lines changed since this ref (default: $SONAR_LINT_BASE, else HEAD)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="report every line, not only the changed ones",
    )
    args = parser.parse_args(argv)
    changed = None if args.all else changed_lines(args.base)
    root = Path(_git("rev-parse", "--show-toplevel").strip()).resolve()
    files = [
        f
        for f in _python_files(args.paths or list(changed or {}))
        if f.is_relative_to(root)
    ]
    if not files:
        return 0
    ruff, complexity = ruff_findings(files), complexity_findings(files)
    failed = False
    for file in files:
        touched = None if changed is None else changed.get(file, set())
        shown = (
            file.relative_to(Path.cwd()) if file.is_relative_to(Path.cwd()) else file
        )
        for line, rule, message in file_findings(
            file, ruff.get(file, []), complexity.get(file, []), touched
        ):
            print(f"{shown}:{line}: {rule} {message}")
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
