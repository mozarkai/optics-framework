import time
from typing import Optional, List, Dict, Any
from optics_framework.common.logging_config import execution_logger


class ExecutionTracer:
    """
    Tracks and logs strategy execution attempts across different components.
    Stores logs in memory and writes to the internal logger.
    """

    def __init__(self, session_id: Optional[str] = None):
        self.session_id = session_id or "unknown"
        self.logs: List[Dict[str, Any]] = []

    def log_attempt(self, strategy, element: str, status: str, error: Optional[str] = None):
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "time": timestamp,
            "strategy": strategy.__class__.__name__,
            "element": str(element),
            "status": status,
            "error": error,
            "session_id": self.session_id
        }
        self.logs.append(entry)

        msg = (
            f"[Execution] Strategy: {entry['strategy']} | Element: {entry['element']} "
            f"| Status: {status} | Session: {self.session_id}"
        )
        if error:
            msg += f" | Error: {error}"

        if status.lower() == "success":
            execution_logger.info(msg)
        elif status.lower() == "fail":
            execution_logger.warning(msg)
        else:
            execution_logger.debug(msg)

    def get_logs(self) -> List[Dict[str, Any]]:
        return self.logs

    def dump_to_console(self):
        execution_logger.info(f"[ExecutionTracer] Dumping {len(self.logs)} strategy logs:")
        for log in self.logs:
            execution_logger.info(log)

    def clear(self):
        self.logs.clear()


def log_strategy_attempt(strategy, element: str, status: str, error: Optional[str] = None, session_id: Optional[str] = None):
    """
    Logs a single strategy attempt without using ExecutionTracer.
    Useful for quick one-off strategy logs.

    Args:
        strategy: Strategy class or object.
        element (str): Element being processed.
        status (str): "success", "fail", or other.
        error (str, optional): Error message if applicable.
        session_id (str, optional): Optional session identifier.
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        f"Strategy: {strategy.__class__.__name__} | Element: {element} "
        f"| Status: {status} | Time: {timestamp}"
    )
    if session_id:
        msg += f" | Session: {session_id}"
    if error:
        msg += f" | Error: {error}"

    if status.lower() == "success":
        execution_logger.info(msg)
    elif status.lower() == "fail":
        execution_logger.warning(msg)
    else:
        execution_logger.debug(msg)
