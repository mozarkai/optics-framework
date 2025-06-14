from typing import Optional
from optics_framework.common.logging_config import execution_logger

class ExecutionTracer:
    """
    Helper for logging structured strategy attempts into execution_logger.
    """

    @staticmethod
    def log_attempt(strategy,
                    element: str,
                    status: str,
                    keyword: str,
                    session: str = "global",
                    duration: Optional[float] = None,
                    error: Optional[str] = None):

        element_str = str(element)
        if isinstance(element, list) and len(element) == 1:
            element_str = str(element[0])

        # Push into execution logger (structured)
        execution_logger.info({
            "event": "strategy_attempt",
            "keyword": keyword,
            "strategy": strategy.__class__.__name__,
            "element": element_str,
            "status": status.lower(),
            "session": session,
            "duration": duration,
            "error": error
        })

execution_tracer = ExecutionTracer()
