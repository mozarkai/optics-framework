import logging
import queue
import time
import re
import atexit
from contextlib import contextmanager
from contextvars import ContextVar
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
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
            existing_internal = {getattr(h, "baseFilename", None) for h in self.internal_logger.handlers}
            existing_execution = {getattr(h, "baseFilename", None) for h in self.execution_logger.handlers}
            if str(internal_log_path) not in existing_internal:
                self.internal_logger.addHandler(
                    create_file_handler(internal_log_path, log_level, LOG_FORMATTER, use_sensitive=True)
                )
            if str(execution_log_path) not in existing_execution:
                self.execution_logger.addHandler(
                    create_file_handler(execution_log_path, log_level, LOG_FORMATTER, use_sensitive=True)
                )

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
        self.internal_logger.addFilter(SecretMaskingFilter())
        self.execution_logger.addFilter(SecretMaskingFilter())
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

SECRET_NAME_WORDS = frozenset({"pass", "password", "passwd", "passcode", "pwd", "pin", "otp", "secret", "token", "key", "apikey"})
# Shorter values, like otp_length=6, would mask digits all over the log.
MIN_SECRET_LENGTH = 4
_active_secrets: ContextVar[set[str] | None] = ContextVar("_active_secrets", default=None)
_secret_names: ContextVar[frozenset[str]] = ContextVar("_secret_names", default=frozenset())


# Fixed-width lookarounds only, so a long name cannot make the match backtrack.
_WORD_BOUNDARY = re.compile(r"(?<=[A-Z])(?=[A-Z][a-z])|(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")


def is_secret_name(name: str | None) -> bool:
    if not name:
        return False
    if name in _secret_names.get():
        return True
    # Splits camelCase, acronym runs (APIToken) and letters from digits (password1).
    split = re.sub(_WORD_BOUNDARY, "_", name)
    words = re.split(r"[^a-z0-9]+", split.lower())
    return any(word in SECRET_NAME_WORDS for word in words)


def use_secret_names(names: list[str]) -> None:
    """Names a run's config marks secret, so their stored values mask like a secret-looking name's."""
    _secret_names.set(frozenset(names))


def use_secrets(secrets: set[str]) -> None:
    """Mask this session's secrets in logs written from the current context."""
    _active_secrets.set(secrets)


def register_secret(secrets: set[str], name: str, value) -> None:
    text = str(value)
    if is_secret_name(name) and len(text) >= MIN_SECRET_LENGTH:
        secrets.add(text)
    use_secrets(secrets)


def mask_secrets(text: str) -> str:
    # Longest first, so overlapping secrets mask whole.
    for secret in sorted(_active_secrets.get() or (), key=len, reverse=True):
        text = text.replace(secret, "****")
    return re.sub(r"@:([^\s,\)\]]+)", "****", text)


_held_records: ContextVar[list | None] = ContextVar("_held_records", default=None)


@contextmanager
def holding_logs_for_secret(name: str | None):
    """Get Text logs what it read before store_as registers it; hold those lines until then."""
    if not is_secret_name(name):
        yield
        return
    held: list[logging.LogRecord] = []
    token = _held_records.set(held)
    try:
        yield
    finally:
        _held_records.reset(token)
        for record in held:
            logging.getLogger(record.name).handle(record)


class SecretMaskingFilter(logging.Filter):
    """On the logger, so plain file handlers get masked lines too."""

    def filter(self, record):
        held = _held_records.get()
        if held is not None:
            held.append(record)
            return False
        record.msg = mask_secrets(record.getMessage())
        record.args = None
        return True


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


def quiet_console_logs():
    """
    Raise the console handlers to WARNING so teardown records can no longer
    reach the terminal once the result printer has rendered its final summary.
    File handlers keep their configured levels and still record everything.
    """
    logging_manager.internal_console_handler.setLevel(logging.WARNING)
    logging_manager.execution_console_handler.setLevel(logging.WARNING)


def decouple_console_logs():
    """Write console records from a background thread, so an unread stderr cannot block the logger."""
    manager = logging_manager
    if manager.internal_listener is not None:
        return
    # shortcut: the queue is unbounded, so records pile up in memory while stderr
    # goes unread; bound it if a long-lived server ever logs enough to matter.
    manager.internal_logger.removeHandler(manager.internal_console_handler)
    manager.internal_logger.addHandler(QueueHandler(manager.internal_log_queue))
    manager.internal_listener = QueueListener(
        manager.internal_log_queue,
        manager.internal_console_handler,
        respect_handler_level=True,
    )
    manager.internal_listener.start()


def drain_console_logs() -> bool:
    """Stop the decoupled listener; False when it is still blocked on a full stderr."""
    listener = logging_manager.internal_listener
    thread = getattr(listener, "_thread", None)
    logging_manager.stop_listeners()
    return thread is None or not thread.is_alive()


def restore_console_logs():
    """
    Restore console handler levels (used when a new live render starts so
    mid-run records are visible on the console again).
    """
    logging_manager.internal_console_handler.setLevel(internal_logger.level)
    logging_manager.execution_console_handler.setLevel(execution_logger.level)


__all__ = ["internal_logger","execution_logger",
           "reconfigure_logging", "quiet_console_logs", "restore_console_logs",
           "decouple_console_logs", "drain_console_logs",
           "LoggerContext", "SessionLoggerAdapter"]
