from typing import Any

from optics_framework.common.database_interface import DatabaseInterface
from optics_framework.common.error import OpticsError, Code

try:
    from sqlalchemy import create_engine
    from sqlalchemy.exc import SQLAlchemyError

    _IMPORT_ERROR: Exception | None = None
except ImportError as exc:  # pragma: no cover - exercised only without the extra installed
    create_engine = None  # type: ignore[assignment]
    SQLAlchemyError = Exception  # type: ignore[assignment,misc]
    _IMPORT_ERROR = exc

MAX_ROWS = 100


class SQLAlchemyDatabase(DatabaseInterface):
    """Any SQL database SQLAlchemy has a dialect for (PostgreSQL, MySQL, SQLite, ...), chosen by the ``url``."""

    def __init__(self, config: dict[str, Any] | None = None):
        if create_engine is None:
            raise OpticsError(
                Code.E0601,
                message="The 'sqlalchemy' database needs the optional dependency 'sqlalchemy'. Install it with: "
                f"pip install 'optics-framework[sqlalchemy]'. Underlying import error: {_IMPORT_ERROR}",
            )
        url = (config or {}).get("url")
        if not url:
            raise OpticsError(
                Code.E0501,
                message="The 'sqlalchemy' database needs a url, e.g. url: ${APP_DB_URL} with "
                "APP_DB_URL=postgresql+psycopg://user:password@localhost:5432/app.",
            )
        try:
            self._engine = create_engine(url)
        except (SQLAlchemyError, ImportError) as exc:
            raise OpticsError(Code.E1001, message=f"Cannot use the database url: {exc}", cause=exc) from exc

    def query(self, statement: str) -> list[dict[str, Any]]:
        try:
            connection = self._engine.connect()
        except SQLAlchemyError as exc:
            raise OpticsError(Code.E1001, message=f"Cannot connect to the database: {exc}", cause=exc) from exc
        # Never committed, so a statement that writes is undone when the connection closes.
        with connection:
            try:
                # no_parameters: the driver must not read a % or : in the statement as a placeholder.
                result = connection.execution_options(no_parameters=True).exec_driver_sql(statement)
                if not result.returns_rows:
                    raise OpticsError(
                        Code.E1002,
                        message=f"The statement returns no rows, and only statements that read are run: {statement}",
                    )
                return [dict(row) for row in result.mappings().fetchmany(MAX_ROWS)]
            except SQLAlchemyError as exc:
                raise OpticsError(Code.E1002, message=f"Query failed: {exc}", cause=exc) from exc

    def close(self) -> None:
        self._engine.dispose()
