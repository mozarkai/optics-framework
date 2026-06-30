import logging
import queue
import time
import re
import atexit
from logging.handlers import QueueHandler, RotatingFileHandler
from rich.logging import RichHandler
from pydantic import BaseModel
from typing import Optional, Tuple
from pathlib import Path

class LoggingConfig(BaseModel):
    log_level: str = "INFO"
    log_path: Optional[str] = None
    file_log: bool = False
    execution_output_path: Optional[str] = None


class LoggingManager:
    def initialize_handlers(self, config):
        """
        Dynamically reconfigure logging handlers based on config.
        """
        log_level = getattr(logging, getattr(config, "log_level", "INFO").upper(), logging.INFO)
        self.internal_logger.setLevel(log_level)
        self.execution_logger.setLevel(log_level)
        self.internal_console_handler.setLevel(log_level)
        self.execution_console_handler.setLevel(log_level)
        # Optionally, add file handlers if config.file_log is True
        if getattr(config, "file_log", False):
            log_path = getattr(config, "log_path", None)
            execution_output_path = getattr(config, "execution_output_path", None)
            log_dir = Path(execution_output_path or (Path.cwd() / "logs")).expanduser()
            log_dir.mkdir(parents=True, exist_ok=True)
            internal_log_path = Path(log_path or log_dir / "internal_logs.log").expanduser()
            execution_log_path = Path(log_path or log_dir / "execution_logs.log").expanduser()
            internal_file_handler = create_file_handler(internal_log_path, log_level, LOG_FORMATTER, use_sensitive=True)
            execution_file_handler = create_file_handler(execution_log_path, log_level, LOG_FORMATTER, use_sensitive=True)
            self.internal_logger.addHandler(internal_file_handler)
            self.execution_logger.addHandler(execution_file_handler)

    def stop_listeners(self):
        def safe_stop(listener, name):
            if not listener:
                return
            try:
                thread = getattr(listener, '_thread', None)
                if thread and thread.is_alive():
                    try:
                        listener.enqueue_sentinel()
                    except Exception as e:
                        self.internal_logger.error(
                            f"Failed to enqueue sentinel for listener: {e}"
                        )
                    thread.join(timeout=2.0)
                    if thread.is_alive():
                        self.internal_logger.warning(f"{name} thread did not terminate after timeout.")
                listener._thread = None
            except Exception as e:
                self.internal_logger.warning(f"Error stopping {name}: {e}")
        safe_stop(self.internal_listener, "internal_listener")
        self.internal_listener = None
        safe_stop(self.execution_listener, "execution_listener")
        self.execution_listener = None

    def shutdown_logging(self):
        try:
            self.disable_logger()
            self.stop_listeners()
            self.internal_logger.debug("Logging shutdown completed")
        except Exception as e:
            self.internal_logger.error(f"Shutdown error: {e}")

    def disable_logger(self):
        logging.getLogger().disabled = True

    def get_internal_logger(self):
        return self.internal_logger

    def get_execution_logger(self):
        return self.execution_logger

    def get_listeners(self):
        return self.internal_listener, self.execution_listener

    def __init__(self):
        self.internal_log_queue = queue.Queue(-1)
        self.execution_log_queue = queue.Queue(-1)
        self.internal_logger = logging.getLogger("optics.internal")
        self.internal_logger.propagate = False
        self.execution_logger = logging.getLogger("optics.execution")
        self.execution_logger.propagate = True
        self.internal_console_handler = RichHandler(
            rich_tracebacks=True, tracebacks_show_locals=True, show_time=True, show_level=True)
        self.internal_console_handler.setFormatter(SensitiveDataFormatter(
            "%(message)s", datefmt="%H:%M:%S"))
        self.execution_console_handler = RichHandler(
            rich_tracebacks=False, show_time=True, show_level=True, markup=True)
        self.execution_console_handler.setFormatter(SensitiveDataFormatter("%(message)s"))
        self.execution_queue_handler = QueueHandler(self.execution_log_queue)
        self.internal_logger.addHandler(self.internal_console_handler)
        self.execution_logger.addHandler(self.execution_queue_handler)
        self.internal_listener = None
        self.execution_listener = None
        self.junit_handler = None

class SensitiveDataFormatter(logging.Formatter):
    def format(self, record):
        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        elif hasattr(record, "args") and record.args:
            record.args = tuple(self._sanitize(str(arg)) for arg in record.args)
        return super().format(record)

    def _sanitize(self, message: str) -> str:
        # Remove duplicate characters in regex character class
        return re.sub(r"@:([^\s,\)\]]+)", "****", message)


logging_manager = LoggingManager()
internal_logger = logging_manager.get_internal_logger()
execution_logger = logging_manager.get_execution_logger()


# SessionLoggerAdapter
class SessionLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = self.extra if isinstance(self.extra, dict) else {}
        session_id = extra.get("session_id", "unknown")
        kwargs.setdefault("extra", {})
        kwargs["extra"]["session_id"] = session_id
        return msg, kwargs

class LogCaptureBuffer(logging.Handler):
    """
    Custom log handler to capture logs emitted during keyword execution.
    """
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def clear(self):
        self.records.clear()

    def get_records(self):
        return self.records


# LoggerContext
class LoggerContext:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.original_execution_logger = execution_logger
        self.original_internal_logger = internal_logger

    def __enter__(self) -> Tuple[logging.LoggerAdapter, logging.LoggerAdapter]:
        session_execution_logger = SessionLoggerAdapter(
            self.original_execution_logger, {"session_id": self.session_id})
        session_internal_logger = SessionLoggerAdapter(
            self.original_internal_logger, {"session_id": self.session_id})
        return session_execution_logger, session_internal_logger

    def __exit__(self, exc_type, exc_value, traceback):
        """
        No-op implementation of the __exit__ method.
        This method is required to complete the context management protocol,
        but no cleanup actions are needed for this context manager.
        """
        pass

def create_file_handler(path, log_level, formatter=None, use_sensitive=False):
    handler = RotatingFileHandler(
        path, maxBytes=10 * 1024 * 1024, backupCount=10)
    if use_sensitive:
        handler.setFormatter(SensitiveDataFormatter('%(asctime)s - %(levelname)s - %(message)s'))
    else:
        handler.setFormatter(formatter or LOG_FORMATTER)
    handler.setLevel(log_level)
    return handler

LOG_FORMATTER = logging.Formatter(
    "%(levelname)s | %(asctime)s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)


def initialize_handlers(config):
    """
    Initialize logging handlers. Accepts either a full Config or a LoggingConfig.
    """
    logging_manager.initialize_handlers(config)

def shutdown_logging():
    try:
        disable_logger()
        stop_listeners()
        wait_for_threads()
        flush_handlers()
        clear_queues()
        internal_logger.debug("Logging shutdown completed")
    except Exception as e:
        internal_logger.error(f"Shutdown error: {e}")


def disable_logger():
    """Disables the root logger."""
    logging.getLogger().disabled = True


def stop_listeners():
    """Stops user and internal listeners."""
    logging_manager.stop_listeners()


def wait_for_threads():
    """Waits for listener threads to terminate with a timeout."""
    timeout = 2.0
    start_time = time.time()
    internal_listener, execution_listener = logging_manager.get_listeners()
    while time.time() - start_time < timeout:
        if not is_thread_alive(internal_listener) and not is_thread_alive(execution_listener):
            return
        time.sleep(0.1)
    check_thread_status()


def is_thread_alive(listener):
    """Checks if a listener's thread is alive."""
    return listener is not None and listener._thread and listener._thread.is_alive()


def check_thread_status():
    internal_listener, execution_listener = logging_manager.get_listeners()
    if is_thread_alive(internal_listener):
        internal_logger.warning("internal_listener thread did not terminate")
    if is_thread_alive(execution_listener):
        internal_logger.warning("execution_listener thread did not terminate")

def flush_handlers():
    try:
        from optics_framework.common.Junit_eventhandler import get_junit_handler_registry
        registry = get_junit_handler_registry()
        active_sessions = registry.get_active_sessions()

        for session_id in active_sessions:
            handler = registry.get_handler(session_id)
            if handler:
                handler.close()
                internal_logger.debug(f"Flushed JUnit handler for session {session_id}")
    except ImportError as e:
        internal_logger.debug(f"Could not import JUnit handler registry: {e}")
    except Exception as e:
        internal_logger.error(f"Error flushing JUnit handlers: {e}")


def clear_queues():
    for log_queue in [logging_manager.internal_log_queue, logging_manager.execution_log_queue]:
        while not log_queue.empty():
            try:
                log_queue.get_nowait()
            except queue.Empty:
                break


atexit.register(shutdown_logging)

# Dynamic Reconfiguration


def reconfigure_logging(config):
    internal_logger.debug("Reconfiguring logging due to config change")
    initialize_handlers(config)


__all__ = ["internal_logger","execution_logger",
           "reconfigure_logging", "LoggerContext", "SessionLoggerAdapter"]
