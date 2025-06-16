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
                    duration: Optional[float] = None,
                    error: Optional[str] = None):

        element_str = str(element)
        if isinstance(element, list) and len(element) == 1:
            element_str = str(element[0])

        status_str = status.upper() if status else "UNKNOWN"

        if duration is not None:
            duration_str = f", duration: {duration:.2f}s"
        else:
            duration_str = ""

        if error:
            log_line = f"Trying {strategy.__class__.__name__} on '{element_str}' ... {status_str} (error: {error}){duration_str}"
        else:
            log_line = f"Trying {strategy.__class__.__name__} on '{element_str}' ... {status_str}{duration_str}"

        execution_logger.info(log_line)

execution_tracer = ExecutionTracer()
