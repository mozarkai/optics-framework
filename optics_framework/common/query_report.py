import json
import os
from datetime import datetime, timezone
from typing import Any

from optics_framework.common.logging_config import mask_secrets

REPORT_NAME = "db_queries.json"
MAX_REPORTED_ROWS = 20


class QueryReport:
    """Every database query a run made, rewritten to ``db_queries.json`` after each one so a crash loses none."""

    def __init__(self, output_dir: str | None):
        self.path = os.path.join(output_dir, REPORT_NAME) if output_dir else None
        self.entries: list[dict[str, Any]] = []

    def add(
        self,
        keyword: str,
        query: str,
        rows: list[dict[str, Any]],
        *,
        status: str,
        attempts: int,
        duration_s: float,
        expected: str | None = None,
        error: str | None = None,
    ) -> None:
        self.entries.append({
            "index": len(self.entries) + 1,
            "time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "keyword": keyword,
            "query": query,
            "expected": expected,
            "status": status,
            "attempts": attempts,
            "duration_s": round(duration_s, 3),
            "row_count": len(rows),
            "rows": rows[:MAX_REPORTED_ROWS],
            "error": error,
        })
        if self.path is None:
            return
        counts = {status: sum(entry["status"] == status for entry in self.entries) for status in ("pass", "fail", "error")}
        text = json.dumps({"summary": {"total": len(self.entries), **counts}, "queries": self.entries}, indent=2, default=str)
        with open(self.path, "w", encoding="utf-8") as report:
            report.write(mask_secrets(text))
