import operator
import re
import time
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from optics_framework.common.error import OpticsError, Code
from optics_framework.common.optics_builder import OpticsBuilder
from optics_framework.common.query_report import QueryReport

POLL_INTERVAL_S = 0.5
_COMPARISON = re.compile(r"^\s*(==|!=|>=|<=|>|<)\s*(.*?)\s*$", re.DOTALL)
_OPERATORS: dict[str, Callable[[Any, Any], bool]] = {
    "==": operator.eq, "!=": operator.ne, ">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt,
}
_NO_ROWS = object()


def _as_number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _as_text(value: Any) -> str:
    if value is None:
        return "null"
    return str(value).lower() if isinstance(value, bool) else str(value)


def _expectation(expected: str) -> Callable[[Any], bool]:
    """``expected`` as a check of one value: a value to equal, or ``>=``, ``<=``, ``>``, ``<``, ``!=``, ``==`` and a value."""
    match = _COMPARISON.match(expected)
    symbol, operand = match.groups() if match else ("==", expected.strip())
    compare = _OPERATORS[symbol]
    wanted_number = _as_number(operand)
    if symbol not in ("==", "!=") and wanted_number is None:
        raise OpticsError(Code.E0403, message=f"'{expected}' compares with '{operand}', which is not a number.")

    def check(actual: Any) -> bool:
        number = _as_number(actual)
        if wanted_number is not None and number is not None:
            return compare(number, wanted_number)
        if symbol not in ("==", "!="):
            return False
        wanted = operand.lower() if isinstance(actual, bool) or actual is None else operand
        return compare(_as_text(actual), wanted)

    return check


def _plain(value: Any) -> Any:
    """A cell as a value JSON and ``store_as`` can hold."""
    return value if value is None or isinstance(value, (bool, int, float, str)) else str(value)


def _single_value(rows: list[dict[str, Any]], query: str) -> Any:
    if not rows:
        return _NO_ROWS
    if len(rows) > 1 or len(rows[0]) != 1:
        raise OpticsError(
            Code.E1002,
            message=f"Assert Query checks one value, but the query returned {len(rows)} row(s) of "
            f"{len(rows[0])} column(s). Select one column of one row, e.g. count(*): {query}",
        )
    return next(iter(rows[0].values()))


def _seconds(timeout: str) -> float:
    try:
        seconds = float(timeout)
    except (TypeError, ValueError):
        seconds = -1.0
    if seconds < 0:
        raise OpticsError(Code.E0403, message=f"timeout must be a number of seconds, 0 or more, got '{timeout}'.")
    return seconds


class Database:
    """Keywords that read the application's database, so a test can check what a UI step stored."""

    def __init__(self, builder: OpticsBuilder):
        self._builder = builder
        self._report = QueryReport(builder.session_config.execution_output_path)

    def _rows(self, keyword: str, query: str, started: float, attempts: int, expected: str | None = None) -> list[dict[str, Any]]:
        try:
            rows = self._builder.get_database().query(query)
        except OpticsError as error:
            self._report.add(
                keyword, query, [], status="error", attempts=attempts,
                duration_s=time.monotonic() - started, expected=expected, error=str(error),
            )
            raise
        return [{column: _plain(value) for column, value in row.items()} for row in rows]

    def run_query(self, query: str) -> Any:
        """
        Runs a query that reads from the database and returns its result.

        One row of one column returns that value; anything else returns the rows as a list of
        column-to-value maps. Add ``store_as=name`` to keep the result as ``${name}``.

        :param query: The query, in the database's own language (SQL for the ``sqlalchemy`` engine).
        """
        started = time.monotonic()
        rows = self._rows("Run Query", query, started, 1)
        self._report.add("Run Query", query, rows, status="pass", attempts=1, duration_s=time.monotonic() - started)
        return next(iter(rows[0].values())) if len(rows) == 1 and len(rows[0]) == 1 else rows

    def assert_query(self, query: str, expected: str, timeout: str = "5") -> Any:
        """
        Asserts that a query's single value matches ``expected``, re-running it until it does or ``timeout`` passes.

        The application writes after the UI step returns, so one look can be too early; the query runs
        every half second. A query that returns no rows counts as not matching yet.

        :param query: A query that returns one column of one row, e.g. ``SELECT count(*) FROM todos WHERE title = 'Buy milk'``.
        :param expected: The value to equal, or a comparison: ``>= 1``, ``!= 0``, ``< 10``.
        :param timeout: Seconds to keep trying. ``0`` looks once.
        """
        check = _expectation(expected)
        limit = _seconds(timeout)
        started = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            rows = self._rows("Assert Query", query, started, attempts, expected)
            try:
                actual = _single_value(rows, query)
            except OpticsError as error:
                self._record_assertion(query, rows, "error", attempts, started, expected, str(error))
                raise
            if actual is not _NO_ROWS and check(actual):
                self._record_assertion(query, rows, "pass", attempts, started, expected)
                return actual
            remaining = limit - (time.monotonic() - started)
            if remaining <= 0:
                break
            time.sleep(min(POLL_INTERVAL_S, remaining))
        got = "no rows" if actual is _NO_ROWS else repr(actual)
        message = f"The query returned {got}, expected {expected.strip()}, after {attempts} attempt(s) in {limit:g}s: {query}"
        self._record_assertion(query, rows, "fail", attempts, started, expected, message)
        raise OpticsError(Code.E1003, message=message)

    def _record_assertion(
        self, query: str, rows: list[dict[str, Any]], status: str, attempts: int, started: float, expected: str, error: str | None = None,
    ) -> None:
        self._report.add(
            "Assert Query", query, rows, status=status, attempts=attempts,
            duration_s=time.monotonic() - started, expected=expected.strip(), error=error,
        )
