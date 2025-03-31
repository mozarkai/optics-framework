import json
import logging
from typing import Optional, Dict, Any, Callable
from rich.logging import RichHandler
from contextlib import contextmanager
import threading
from pathlib import Path
from functools import wraps
from optics_framework.common.config_handler import ConfigHandler, Config
from pythonjsonlogger.json import JsonFormatter

# Thread-local variable for temporary configuration
_log_context = threading.local()
_log_context.format_type = "internal"  # Default mode is "internal"

# Get the singleton config instance
config_handler = ConfigHandler.get_instance()
config: Config = config_handler.config  # Type as Pydantic Config
logger = logging.getLogger("optics_framework")


class DynamicFilter(logging.Filter):
    """Filter logs based on mode and configured level."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Filters log records dynamically.

        :param record: The log record to filter.
        :return: True if the record should be logged, False otherwise.
        """
        current = getattr(_log_context, "format_type", "internal")
        config_level_name = str(config.log_level).upper()  # Use Config field
        config_level = getattr(logging, config_level_name, logging.INFO)
        # Apply level filtering only in "user" mode for console; "internal" sees all
        if current == "user":
            return record.levelno >= config_level
        return True  # "internal" mode passes all logs to handlers


class UniversalFormatter(logging.Formatter):
    """Formatter switching between internal and user formats."""

    def __init__(self) -> None:
        internal_fmt = ("%(message)-65s")
        user_fmt = "%(message)s"
        datefmt = "%H:%M:%S"
        self.datefmt = datefmt
        self.internal_formatter = logging.Formatter(
            internal_fmt, datefmt=datefmt)
        self.user_formatter = logging.Formatter(user_fmt)

    def format(self, record: logging.LogRecord) -> str:
        """Formats the log record based on current mode.

        :param record: The log record to format.
        :return: Formatted log string.
        """
        fmt = getattr(_log_context, "format_type", "internal")
        for attr in ["test_case", "test_module", "keyword"]:
            if not hasattr(record, attr):
                setattr(record, attr, "N/A")
        if fmt == "user":
            return self.user_formatter.format(record)
        return self.internal_formatter.format(record)

    def __getattr__(self, name: str) -> Any:
        if name == "_style":
            fmt = getattr(_log_context, "format_type", "internal")
            if fmt == "user":
                return self.user_formatter._style
            return self.internal_formatter._style
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'")


class EnhancedJsonFormatter(JsonFormatter):
    """JSON formatter with enhanced fields."""

    def add_fields(self, log_record: Dict[str, Any], record: logging.LogRecord, message_dict: Dict[str, Any]) -> None:
        """Adds custom fields to the JSON log record.

        :param log_record: The dictionary to populate with log data.
        :param record: The original log record.
        :param message_dict: The message dictionary from the record.
        """
        super().add_fields(log_record, record, message_dict)
        log_record["timestamp"] = self.formatTime(record)
        log_record["level"] = record.levelname
        # Use getMessage() for consistency
        log_record["message"] = record.getMessage()
        log_record["test_case"] = getattr(record, "test_case", "N/A")
        log_record["test_module"] = getattr(record, "test_module", "N/A")
        log_record["keyword"] = getattr(record, "keyword", "N/A")
        log_record["logger"] = record.name
        log_record["function"] = record.funcName
        log_record["line"] = record.lineno
        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)


class HierarchicalJsonHandler(logging.Handler):
    """Custom handler that accumulates logs into a nested JSON structure."""

    def __init__(self, filename: str | Path) -> None:
        """Initializes the handler with a target file.

        :param filename: Path to the JSON log file.
        """
        super().__init__()
        self.filename = str(filename)  # Ensure string for consistency
        self.logs: Dict[str, Dict[str, Dict[str, list[Dict[str, Any]]]]] = {}
        self.setFormatter(EnhancedJsonFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        """Emits a log record to the nested dictionary.

        :param record: The log record to emit.
        """
        try:
            json_record = self.format(record)
            log_entry = json.loads(json_record)
            test_case = log_entry.get("test_case", "N/A")
            test_module = log_entry.get("test_module", "N/A")
            keyword = log_entry.get("keyword", "N/A")
            self.logs.setdefault(test_case, {})
            self.logs[test_case].setdefault(test_module, {})
            self.logs[test_case][test_module].setdefault(
                keyword, []).append(log_entry)
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        """Writes the accumulated logs to the file."""
        with open(self.filename, 'w', encoding='utf-8') as f:
            json.dump(self.logs, f, indent=2)


logging.root.handlers = []
logger.setLevel(logging.DEBUG)  # Root logger captures all levels

# Console (Rich) Handler
console_level_name = str(config.log_level).upper()  # Line 172 fix
console_level = getattr(logging, console_level_name, logging.INFO)
rich_handler = RichHandler(
    rich_tracebacks=True,  # Use Config fields
    tracebacks_show_locals=True,
    show_time=True,
    show_level=True,
)
rich_handler.setFormatter(UniversalFormatter())
rich_handler.addFilter(DynamicFilter())
rich_handler.setLevel(console_level)  # Respect configured log level
logger.addHandler(rich_handler)


def initialize_additional_handlers() -> None:
    """Initializes file and JSON handlers based on configuration."""
    project_path = config_handler.get_project_path()
    if not project_path:
        logger.warning("Project path not set; defaulting to ~/.optics")
        project_path = str(Path.home() / ".optics")
    else:
        logger.debug(f"Using project path: {project_path}")

    # File Handler
    if config.file_log:
        default_log_path = Path(project_path) / "execution_output" / "logs.log"
        log_path = Path(
            config.log_path if config.log_path is not None else default_log_path).expanduser()
        logger.debug(f"Log file path: {log_path}")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, mode='w')
        file_handler.setFormatter(UniversalFormatter())
        file_handler.addFilter(DynamicFilter())
        file_handler.setLevel(logging.DEBUG)  # File captures all logs
        logger.addHandler(file_handler)

    # JSON Handler
    if config.json_log:
        default_json_path = Path(project_path) / \
            "execution_output" / "logs.json"
        json_path = Path(
            config.json_path if config.json_path is not None else default_json_path).expanduser()
        logger.debug(f"JSON log file path: {json_path}")
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_handler = HierarchicalJsonHandler(json_path)
        json_handler.setLevel(logging.DEBUG)  # JSON captures all logs
        logger.addHandler(json_handler)


@contextmanager
def set_logger_format(fmt: str):
    """Temporarily sets the logger mode for the duration of a context.

    :param fmt: Logger mode ("internal" or "user").
    """
    old_format = getattr(_log_context, "format_type", "internal")
    _log_context.format_type = fmt
    try:
        yield  # Explicit yield for contextmanager
    finally:
        _log_context.format_type = old_format


def use_logger_format(fmt: Optional[str] = None) -> Callable:
    """Decorator to set the logger mode for a function call.

    :param fmt: Logger mode ("internal" or "user"), defaults to None (uses "internal").
    :return: Decorated function.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with set_logger_format(fmt or "internal"):
                return func(*args, **kwargs)
        return wrapper
    return decorator


def apply_logger_format_to_all(fmt: Optional[str] = None) -> Callable:
    """Class decorator to apply a logger mode to all callable methods.

    :param fmt: Logger mode for all methods in the class, defaults to None (uses "internal").
    :return: Decorated class.
    """
    def decorator(cls: type) -> type:
        for attr_name in dir(cls):
            if not attr_name.startswith("__"):
                attribute = getattr(cls, attr_name)
                if callable(attribute):
                    decorated = use_logger_format(fmt)(attribute)
                    setattr(cls, attr_name, decorated)
        return cls
    return decorator
