import logging
import queue
import os
import time
import re
from typing import Tuple
import atexit
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from rich.logging import RichHandler
from optics_framework.common.config_handler import ConfigHandler

# Global variables
config_handler = ConfigHandler.get_instance()
config = config_handler.load()

# Global Queues
internal_log_queue = queue.Queue(-1)
execution_log_queue = queue.Queue(-1)


# Internal Logger
internal_logger = logging.getLogger("optics.internal")
internal_logger.propagate = False


class SensitiveDataFormatter(logging.Formatter):
    def format(self, record):
        if isinstance(record.msg, str):
            record.msg = self._sanitize(record.msg)
        elif hasattr(record, "args") and record.args:
            record.args = tuple(self._sanitize(str(arg)) for arg in record.args)
        return super().format(record)

    def _sanitize(self, message: str) -> str:
        return re.sub(r"@:([^\s,)\]]+)", "****", message)

internal_console_handler = RichHandler(
    rich_tracebacks=True, tracebacks_show_locals=True, show_time=True, show_level=True)
internal_console_handler.setFormatter(SensitiveDataFormatter(
    "%(levelname)s | %(asctime)s | %(message)s", datefmt="%H:%M:%S"))

internal_queue_handler = QueueHandler(internal_log_queue)
internal_logger.addHandler(internal_queue_handler)

# Execution Logger
execution_logger = logging.getLogger("optics.execution")
execution_logger.propagate = True

execution_console_handler = RichHandler(
    rich_tracebacks=False, show_time=True, show_level=True, markup=True)
execution_console_handler.setFormatter(SensitiveDataFormatter("%(message)s"))

execution_queue_handler = QueueHandler(execution_log_queue)
execution_logger.addHandler(execution_queue_handler)

execution_listener = QueueListener(
    execution_log_queue, execution_console_handler, respect_handler_level=True)
execution_listener.start()



# SessionLoggerAdapter
class SessionLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        session_id = self.extra.get("session_id", "unknown")
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


junit_handler = None
internal_listener = None
execution_listener = None

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


def initialize_handlers():
    global config, junit_handler, internal_listener, execution_listener
    config = config_handler.load()
    internal_logger.debug("Initializing logging handlers")

    log_level_str = config.log_level.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    internal_logger.debug(f"Setting log level to {log_level_str} ({log_level})")

    if execution_logger is None or internal_logger is None:
        raise RuntimeError(
            f"Loggers not initialized: execution_logger={execution_logger}, internal_logger={internal_logger}"
        )

    # Set levels for console loggers
    internal_logger.setLevel(log_level)
    internal_console_handler.setLevel(log_level)
    execution_logger.setLevel(log_level)
    execution_console_handler.setLevel(log_level)


    # Prepare directories
    execution_output_path = config.execution_output_path or (Path.cwd() / "logs")
    log_dir = Path(execution_output_path).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    internal_logger.debug(f"Output directory: {log_dir}, writable={os.access(log_dir, os.W_OK)}")

    # Stop old listeners if exist
    if internal_listener:
        internal_listener.stop()
    if execution_listener:
        execution_listener.stop()

    # Start listeners with console handlers first
    internal_listener = QueueListener(internal_log_queue, internal_console_handler, respect_handler_level=True)
    execution_listener = QueueListener(execution_log_queue, execution_console_handler, respect_handler_level=True)

    # Add file handlers only if enabled
    if config.file_log or log_level <= logging.DEBUG:
        internal_log_path = Path(config.log_path or log_dir / "internal_logs.log").expanduser()
        execution_log_path = Path(config.log_path or log_dir / "execution_logs.log").expanduser()

        internal_file_handler = create_file_handler(internal_log_path, log_level, LOG_FORMATTER, use_sensitive=True)
        execution_file_handler = create_file_handler(execution_log_path, log_level, LOG_FORMATTER, use_sensitive=True)

        internal_listener.handlers += (internal_file_handler,)
        execution_listener.handlers += (internal_file_handler, execution_file_handler,)

        internal_logger.debug(f"Added internal log file: {internal_log_path}")
        internal_logger.debug(f"Added execution log file: {execution_log_path}")


    internal_listener.start()
    execution_listener.start()

    internal_logger.debug("Logging handlers initialized")

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
    if internal_listener:
        internal_listener.stop()
    if execution_listener:
        execution_listener.stop()


def wait_for_threads():
    """Waits for listener threads to terminate with a timeout."""
    timeout = 2.0
    start_time = time.time()
    while time.time() - start_time < timeout:
        if not (is_thread_alive(internal_listener)) or is_thread_alive(execution_listener):
            return
        time.sleep(0.1)
    check_thread_status()


def is_thread_alive(listener):
    """Checks if a listener's thread is alive."""
    return listener is not None and listener._thread and listener._thread.is_alive()


def check_thread_status():
    if is_thread_alive(internal_listener):
        internal_logger.warning("internal_listener thread did not terminate")
    if is_thread_alive(execution_listener):
        internal_logger.warning("execution_listener thread did not terminate")

def flush_handlers():
    if junit_handler:
        try:
            junit_handler.close()
        except Exception as e:
            internal_logger.error(f"Error closing junit_handler: {e}")


def clear_queues():
    for log_queue in [internal_log_queue, execution_log_queue]:
        while not log_queue.empty():
            try:
                log_queue.get_nowait()
            except queue.Empty:
                break


atexit.register(shutdown_logging)

# Dynamic Reconfiguration


def reconfigure_logging():
    internal_logger.debug("Reconfiguring logging due to config change")
    initialize_handlers()


__all__ = ["internal_logger","execution_logger",
           "reconfigure_logging", "LoggerContext", "SessionLoggerAdapter"]
