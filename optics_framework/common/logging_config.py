import logging
import queue
import threading
from rich.logging import RichHandler
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
import xml.etree.ElementTree as ET  # For JUnit XML generation # nosec B405
from datetime import datetime
import atexit
import sys

from optics_framework.common.config_handler import ConfigHandler

# Initialize ConfigHandler
config_handler = ConfigHandler.get_instance()
config = config_handler.load()

# Global Queues
user_log_queue = queue.Queue(-1)
internal_log_queue = queue.Queue(-1)

# User Logger
user_logger = logging.getLogger("optics.user")
user_logger.propagate = False

user_console_handler = RichHandler(
    rich_tracebacks=False, show_time=False, show_level=False, markup=True)
user_console_handler.setFormatter(logging.Formatter("%(message)s"))

user_queue_handler = QueueHandler(user_log_queue)
user_logger.addHandler(user_queue_handler)

user_listener = QueueListener(
    user_log_queue, user_console_handler, respect_handler_level=True)
user_listener.start()

# Internal Logger
internal_logger = logging.getLogger("optics.internal")
internal_logger.propagate = False

internal_console_handler = RichHandler(
    rich_tracebacks=True, tracebacks_show_locals=True, show_time=True, show_level=True)
internal_console_handler.setFormatter(logging.Formatter(
    "%(levelname)s | %(asctime)s | %(message)s", datefmt="%H:%M:%S"))

internal_queue_handler = QueueHandler(internal_log_queue)
internal_logger.addHandler(internal_queue_handler)

# JUnit Handler


class JUnitHandler(logging.Handler):
    def __init__(self, filename: Path, buffer_size: int = 100):
        super().__init__()
        self.filename = filename
        self.buffer = []
        self.buffer_size = buffer_size
        self.lock = threading.Lock()
        self.testsuites = ET.Element("testsuites")
        self.suite_map = {}
        self.start_time = datetime.now()
        self.formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        if self.lock is None:
            raise RuntimeError("Lock not initialized")
        try:
            with self.lock:
                suite_key = getattr(record, "module", record.name)
                if suite_key not in self.suite_map:
                    suite = ET.SubElement(self.testsuites, "testsuite", {
                        "name": suite_key,
                        "tests": "0",
                        "failures": "0",
                        "errors": "0",
                        "skipped": "0",
                        "time": "0"
                    })
                    self.suite_map[suite_key] = suite
                suite = self.suite_map[suite_key]

                testcase = ET.SubElement(suite, "testcase", {
                    "name": record.getMessage(),
                    "classname": record.module if hasattr(record, "module") else record.name,
                    "time": "0"
                })

                if record.levelno >= logging.ERROR:
                    failure_type = "Error"
                    if record.exc_info and record.exc_info[0] is not None:
                        failure_type = record.exc_info[0].__name__
                    failure = ET.SubElement(testcase, "failure", {
                        "message": record.getMessage(),
                        "type": failure_type
                    })
                    if record.exc_info:
                        if self.formatter:
                            failure.text = self.formatter.formatException(
                                record.exc_info)
                        else:
                            failure.text = logging.Formatter().formatException(
                                record.exc_info)
                    suite.attrib["failures"] = str(
                        int(suite.attrib["failures"]) + 1)
                elif record.levelno == logging.WARNING:
                    ET.SubElement(testcase, "skipped")

                suite.attrib["tests"] = str(int(suite.attrib["tests"]) + 1)
                self.buffer.append(record)

                if len(self.buffer) >= self.buffer_size:
                    self.flush()
        except Exception as e:
            self.handleError(record)
            print(f"Logging error: {e}", file=sys.stderr)

    def flush(self) -> None:
        if self.lock is None:
            raise RuntimeError("Lock not initialized")
        with self.lock:
            if self.testsuites:
                elapsed = (datetime.now() - self.start_time).total_seconds()
                for suite in self.testsuites:
                    suite.attrib["time"] = str(elapsed / len(self.testsuites))
                tree = ET.ElementTree(self.testsuites)
                ET.indent(tree, space="  ")
                tree.write(self.filename, encoding="utf-8",
                           xml_declaration=True)
                self.buffer.clear()

    def close(self) -> None:
        self.flush()
        super().close()


# Global listener and JUnit handler
junit_handler = None
internal_listener = None


def initialize_handlers():
    global config, junit_handler, internal_listener
    config = config_handler.load()  # Reload config

    # Set log levels dynamically from config
    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    user_logger.setLevel(log_level)
    user_console_handler.setLevel(log_level)
    internal_logger.setLevel(log_level)  # Update internal logger too
    internal_console_handler.setLevel(log_level)

    project_path = config.project_path or Path.home() / ".optics"
    log_dir = Path(project_path) / "execution_output"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Stop and clear existing internal_listener if it exists
    if internal_listener:
        internal_listener.stop()
    internal_listener = QueueListener(
        internal_log_queue, internal_console_handler, respect_handler_level=True)

    # File Handler
    if config.file_log:
        log_path = Path(config.log_path or log_dir /
                        "internal_logs.log").expanduser()
        file_handler = RotatingFileHandler(
            log_path, maxBytes=10*1024*1024, backupCount=10)
        file_handler.setFormatter(logging.Formatter(
            "%(levelname)s | %(asctime)s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        file_handler.setLevel(log_level)  # Use dynamic log level
        internal_listener.handlers += (file_handler,)

    # JUnit Handler
    if config.json_log:
        global junit_handler
        junit_path = Path(config.json_path or log_dir /
                          "test_results.xml").expanduser()
        if junit_handler:
            junit_handler.close()
            user_logger.handlers = [
                h for h in user_logger.handlers if not isinstance(h, JUnitHandler)]
            internal_logger.handlers = [
                h for h in internal_logger.handlers if not isinstance(h, JUnitHandler)]
        junit_handler = JUnitHandler(junit_path, buffer_size=100)
        junit_handler.setLevel(log_level)  # Use dynamic log level
        internal_listener.handlers += (junit_handler,)
        user_logger.addHandler(junit_handler)

    internal_listener.start()


initialize_handlers()

# Shutdown


def shutdown_logging():
    internal_logger.debug("Shutting down logging system")
    user_listener.stop()
    if internal_listener:
        internal_listener.stop()
    for handler in internal_logger.handlers + user_logger.handlers:
        if isinstance(handler, JUnitHandler):
            handler.close()


atexit.register(shutdown_logging)

# Dynamic Reconfiguration


def reconfigure_logging():
    """Reinitialize handlers if config changes."""
    internal_logger.debug("Reconfiguring logging due to config change")
    initialize_handlers()


__all__ = ["user_logger", "internal_logger", "reconfigure_logging"]
