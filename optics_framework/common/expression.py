"""The one evaluator for expressions that arrive as data: Evaluate, Condition, JSONPath assertions and Read Data filters."""
import ast
import itertools
import operator
import re
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from typing import Any

import pandas as pd

from optics_framework.common.error import Code, OpticsError

MAX_CHARS = 100_000
MAX_NODES = 2_000
MAX_INT_BITS = 10_000
MAX_SIZE = 1_000_000
MAX_BUILT = 4 * MAX_SIZE

_BINARY: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY: dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg, ast.Not: operator.not_}
_COMPARE: dict[type, Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda item, group: item in group,
    ast.NotIn: lambda item, group: item not in group,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}
_STRUCTURE = frozenset({
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp,
    ast.Constant, ast.Name, ast.Load, ast.List, ast.Tuple, ast.Call, ast.And, ast.Or,
    ast.Subscript, ast.Slice,
})
_SEQUENCES = (str, bytes, list, tuple)
_VALUE = "value"
# pandas ``query`` reads these as and/or/not before parsing, so they bind looser than comparisons.
_QUERY_BOOLEANS = {"&": " and ", "|": " or ", "~": "not "}
_CONDITION_FUNCTIONS: dict[str, Callable[..., Any]] = {"len": len, "str": str, "int": int, "float": float, "bool": bool}


def _refuse(expression: str, reason: str) -> OpticsError:
    return OpticsError(Code.E0406, message=f"Expression '{expression[:200]}' refused: {reason}.")


class _Evaluator:
    allowed = _STRUCTURE | set(_BINARY) | set(_UNARY) | set(_COMPARE)

    def __init__(self, expression: str, names: Any, functions: Mapping[str, Callable[..., Any]], source: str | None = None) -> None:
        self.expression = expression
        self.source = expression if source is None else source
        self.names = names
        self.functions = functions
        self.sizes: dict[int, tuple[Any, int]] = {}
        self.built = 0
        self.handlers: dict[type, Callable[[Any], Any]] = {
            ast.Constant: lambda node: node.value,
            ast.Name: lambda node: self.name(node.id),
            ast.List: lambda node: self._bounded([self.eval(item) for item in node.elts]),
            ast.Tuple: lambda node: self._bounded(tuple(self.eval(item) for item in node.elts)),
            ast.UnaryOp: lambda node: self.unary(node.op, self.eval(node.operand)),
            ast.BinOp: lambda node: self.binary(node.op, self.eval(node.left), self.eval(node.right)),
            ast.BoolOp: lambda node: self.boolean(node.op, node.values),
            ast.Compare: lambda node: self.compare(node.left, node.ops, node.comparators),
            ast.IfExp: lambda node: self.eval(node.body) if self.eval(node.test) else self.eval(node.orelse),
            ast.Call: lambda node: self.functions[node.func.id](*(self.eval(arg) for arg in node.args)),
            ast.Subscript: lambda node: self._bounded(self.eval(node.value)[self.eval(node.slice)]),
            ast.Slice: lambda node: slice(*(part and self.eval(part) for part in (node.lower, node.upper, node.step))),
        }
        self.cost_checks: dict[type, Callable[[Any, Any], str | None]] = {
            ast.Pow: self._power_cost,
            ast.Mult: self._repeat_cost,
            ast.Add: self._join_cost,
            ast.Mod: self._format_cost,
        }

    def run(self) -> Any:
        tree = self._parse()
        try:
            return self.eval(tree.body)
        except OpticsError:
            raise
        except RecursionError as e:
            raise _refuse(self.expression, "it is nested too deeply") from e
        except Exception as e:
            raise OpticsError(Code.E0403, message=f"Error evaluating expression '{self.expression}': {e}", cause=e) from e

    def _parse(self) -> ast.Expression:
        if len(self.source) > MAX_CHARS:
            raise _refuse(self.expression, f"it is longer than {MAX_CHARS} characters")
        try:
            tree = ast.parse(self.source, mode="eval")
        except (MemoryError, RecursionError) as e:
            raise _refuse(self.expression, "it is nested too deeply") from e
        except (SyntaxError, ValueError) as e:
            raise OpticsError(Code.E0403, message=f"Invalid expression '{self.expression}': {e}", cause=e) from e
        for count, node in enumerate(ast.walk(tree)):
            if count >= MAX_NODES:
                raise _refuse(self.expression, f"it has more than {MAX_NODES} parts")
            self._check_node(node)
        return tree

    def _check_node(self, node: ast.AST) -> None:
        if type(node) not in self.allowed:
            raise _refuse(self.expression, f"{type(node).__name__} is not allowed; use literals, names, indexing, arithmetic, comparisons, and/or/not and if/else")
        if isinstance(node, ast.Call) and (
            not isinstance(node.func, ast.Name) or node.func.id not in self.functions or node.keywords
        ):
            raise _refuse(self.expression, f"only these calls are allowed: {', '.join(self.functions) or 'none'}")

    def eval(self, node: ast.AST) -> Any:
        return self.handlers[type(node)](node)

    def name(self, name: str) -> Any:
        if name not in self.names:
            raise OpticsError(Code.E0403, message=f"Error evaluating expression '{self.expression}': name '{name}' is not defined.")
        return self.names[name]

    def unary(self, op: ast.unaryop, value: Any) -> Any:
        return _UNARY[type(op)](value)

    def binary(self, op: ast.operator, left: Any, right: Any) -> Any:
        self._check_cost(op, left, right)
        result = _BINARY[type(op)](left, right)
        if isinstance(result, int) and result.bit_length() > MAX_INT_BITS:
            raise _refuse(self.expression, f"a number grows past {MAX_INT_BITS} bits")
        return self._bounded(result)

    def boolean(self, op: ast.boolop, nodes: list[ast.expr]) -> Any:
        value = None
        for node in nodes:
            value = self.eval(node)
            if bool(value) == isinstance(op, ast.Or):
                return value
        return value

    def compare(self, left: ast.expr, ops: list[ast.cmpop], comparators: list[ast.expr]) -> Any:
        current = self.eval(left)
        for op, node in zip(ops, comparators):
            following = self.eval(node)
            if not _COMPARE[type(op)](current, following):
                return False
            current = following
        return True

    def _check_cost(self, op: ast.operator, left: Any, right: Any) -> None:
        check = self.cost_checks.get(type(op))
        reason = check(left, right) if check else None
        if reason:
            raise _refuse(self.expression, reason)

    def _power_cost(self, base: Any, exponent: Any) -> str | None:
        if isinstance(base, int) and isinstance(exponent, int) and exponent > 0 and abs(base) > 1 and base.bit_length() * exponent > MAX_INT_BITS:
            return f"the power grows past {MAX_INT_BITS} bits"
        return None

    def _repeat_cost(self, left: Any, right: Any) -> str | None:
        sequence, count = (left, right) if isinstance(right, int) else (right, left)
        if isinstance(sequence, _SEQUENCES) and isinstance(count, int) and self._size(sequence) * count > MAX_SIZE:
            return f"the repeated value grows past {MAX_SIZE} items"
        return None

    def _join_cost(self, left: Any, right: Any) -> str | None:
        if isinstance(left, _SEQUENCES) and isinstance(right, _SEQUENCES) and self._size(left) + self._size(right) > MAX_SIZE:
            return f"the joined value grows past {MAX_SIZE} items"
        return None

    @staticmethod
    def _format_cost(left: Any, _right: Any) -> str | None:
        return "% formatting of text is not allowed" if isinstance(left, (str, bytes)) else None

    def _bounded(self, value: Any) -> Any:
        if not isinstance(value, _SEQUENCES):
            return value
        if self._size(value) > MAX_SIZE:
            raise _refuse(self.expression, f"a value grows past {MAX_SIZE} items")
        # The size memo holds every value it measured, so intermediates count against one budget.
        self.built += len(value)
        if self.built > MAX_BUILT:
            raise _refuse(self.expression, f"it builds more than {MAX_BUILT} items in total")
        return value

    def _size(self, value: Any) -> int:
        """Items a value holds, counting nested lists; memoised by identity so shared parts count once per lookup."""
        if isinstance(value, (str, bytes)):
            return len(value)
        if not isinstance(value, (list, tuple)):
            return 1
        known = self.sizes.get(id(value))
        if known is not None and known[0] is value:
            return known[1]
        size = len(value) + sum(self._size(item) for item in value)
        self.sizes[id(value)] = (value, size)
        return size


class _RowFilter(_Evaluator):
    """Names are columns; comparisons, and/or/not and ``in`` combine into a row mask, as in pandas ``query``."""

    allowed = _Evaluator.allowed - {ast.Call}

    def __init__(self, expression: str, frame: pd.DataFrame) -> None:
        source, self.columns = _rewrite(expression, _QUERY_BOOLEANS)
        super().__init__(expression, frame, {}, source)

    def name(self, name: str) -> Any:
        column = self.columns.get(name, name)
        if column not in self.names.columns:
            raise OpticsError(Code.E0403, message=f"Invalid query expression: {self.expression}. Column '{column}' is not in the data.")
        return self.names[column]

    def unary(self, op: ast.unaryop, value: Any) -> Any:
        if isinstance(op, ast.Not) and isinstance(value, pd.Series):
            return ~value
        return super().unary(op, value)

    def binary(self, op: ast.operator, left: Any, right: Any) -> Any:
        if isinstance(left, pd.Series) or isinstance(right, pd.Series):
            if not (_numeric(left) and _numeric(right)):
                raise _refuse(self.expression, "a filter computes only with numbers")
            return _BINARY[type(op)](left, right)
        return super().binary(op, left, right)

    def boolean(self, op: ast.boolop, nodes: list[ast.expr]) -> Any:
        combine = operator.or_ if isinstance(op, ast.Or) else operator.and_
        values = [self.eval(node) for node in nodes]
        result = values[0]
        for value in values[1:]:
            result = combine(result, value)
        return result

    def compare(self, left: ast.expr, ops: list[ast.cmpop], comparators: list[ast.expr]) -> Any:
        current = self.eval(left)
        result: Any = True
        for op, node in zip(ops, comparators):
            following = self.eval(node)
            result = result & self._pair(op, current, following)
            current = following
        return result

    def _pair(self, op: ast.cmpop, left: Any, right: Any) -> Any:
        if isinstance(op, (ast.In, ast.NotIn)) and isinstance(left, pd.Series):
            if not isinstance(right, (list, tuple)):
                raise OpticsError(Code.E0403, message=f"Invalid query expression: {self.expression}. Use 'column in [values]'.")
            found = left.isin(right)
            return ~found if isinstance(op, ast.NotIn) else found
        return _COMPARE[type(op)](left, right)


def _numeric(value: Any) -> bool:
    if isinstance(value, pd.Series):
        return pd.api.types.is_numeric_dtype(value)
    return isinstance(value, (int, float))


def evaluate(expression: str, names: Mapping[str, Any], functions: Mapping[str, Callable[..., Any]] | None = None) -> Any:
    """Evaluate ``expression`` over ``names``; anything beyond literals, arithmetic, comparisons and the given calls raises E0406."""
    return _Evaluator(expression, names, functions or {}).run()


def check_condition(condition: str, value: Any) -> Any:
    """Evaluate a JSONPath assertion such as ``len($) > 0`` with ``$`` bound to ``value``, never pasted into the source."""
    source, _ = _rewrite(condition, {"$": _VALUE})
    return _Evaluator(condition, {_VALUE: value}, _CONDITION_FUNCTIONS, source).run()


def _rewrite(text: str, swaps: Mapping[str, str]) -> tuple[str, dict[str, str]]:
    """Outside string literals: each backtick-quoted name becomes an alias (pandas ``query`` style) and each ``swaps`` character its text."""
    if len(text) > MAX_CHARS:
        raise _refuse(text, f"it is longer than {MAX_CHARS} characters")
    out: list[str] = []
    aliases: dict[str, str] = {}
    # Python reads names in NFKC form, so an alias must not equal any word of the normalized text.
    taken = set(re.findall(r"\w+", unicodedata.normalize("NFKC", text)))
    names = (name for name in (f"_column_{n}" for n in itertools.count()) if name not in taken)
    quote, escaped, at = "", False, 0
    while at < len(text):
        char = text[at]
        if escaped:
            escaped = False
        elif quote:
            escaped = char == "\\"
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "`":
            at, char = _alias(text, at, aliases, names)
        elif char in swaps:
            char = swaps[char]
        out.append(char)
        at += 1
    return "".join(out), aliases


def _alias(text: str, at: int, aliases: dict[str, str], names: Iterator[str]) -> tuple[int, str]:
    close = text.find("`", at + 1)
    if close < 0:
        raise OpticsError(Code.E0403, message=f"Invalid expression '{text}': a backtick-quoted name is not closed.")
    alias = next(names)
    aliases[alias] = text[at + 1:close]
    return close, alias


def filter_rows(frame: pd.DataFrame, expression: str) -> pd.DataFrame:
    """The rows of ``frame`` a pandas-style ``query`` filter keeps, without pandas' Python evaluation."""
    mask = _RowFilter(expression, frame).run()
    if not isinstance(mask, pd.Series) or mask.dtype != bool:
        raise OpticsError(Code.E0403, message=f"Invalid query expression: {expression}. A filter must compare a column, e.g. status == 'active'.")
    return frame[mask]
