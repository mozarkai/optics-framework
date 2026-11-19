import json
import sqlite3
from types import SimpleNamespace

import pytest

from optics_framework.api.database import Database
from optics_framework.common.error import Code, OpticsError
from optics_framework.engines.databases.sqlalchemy import SQLAlchemyDatabase


def test_queries_read_the_database_without_changing_it_and_are_reported(tmp_path):
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.executescript("CREATE TABLE todos (title TEXT); INSERT INTO todos VALUES ('50% milk');")
    engine = SQLAlchemyDatabase({"url": f"sqlite:///{tmp_path}/app.db"})
    keywords = Database(SimpleNamespace(session_config=SimpleNamespace(execution_output_path=str(tmp_path)), get_database=lambda: engine))
    assert keywords.run_query("SELECT title FROM todos WHERE title LIKE '50%'") == "50% milk"
    assert keywords.assert_query("SELECT count(*) FROM todos", ">= 1", "0") == 1
    for query, expected, code in [("SELECT count(*) FROM todos", "2", Code.E1003), ("DELETE FROM todos", "0", Code.E1002)]:
        with pytest.raises(OpticsError) as error:
            keywords.assert_query(query, expected, "0.01")
        assert error.value.code == code
    assert keywords.run_query("SELECT count(*) FROM todos") == 1
    engine.close()
    assert json.loads((tmp_path / "db_queries.json").read_text())["summary"] == {"total": 5, "pass": 3, "fail": 1, "error": 1}
