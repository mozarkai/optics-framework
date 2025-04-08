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
import time

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
        self._lock: threading.RLock = threading.RLock()  # Use RLock instead of Lock
        self.testsuites = ET.Element("testsuites")
        self.suite_map = {}
        self.start_time = datetime.now()
        self.formatter = logging.Formatter()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            print(
                f"Attempting to acquire lock for {self.filename}", file=sys.stderr)
            if not self._lock.acquire(timeout=2):  # Timeout after 2 seconds
                print(
                    f"Failed to acquire lock for {self.filename} within 2 seconds", file=sys.stderr)
                return
            try:
                suite_key = getattr(record, "module", record.name)
                if suite_key not in self.suite_map:
                    suite = ET.SubElement(self.testsuites, "testsuite", {
                        "name": suite_key, "tests": "0", "failures": "0", "errors": "0", "skipped": "0", "time": "0"
                    })
                    self.suite_map[suite_key] = suite
                suite = self.suite_map[suite_key]

                testcase = ET.SubElement(suite, "testcase", {
                    "name": record.getMessage(), "classname": record.module if hasattr(record, "module") else record.name, "time": "0"
                })

                if record.levelno >= logging.ERROR:
                    failure_type = "Error"
                    if record.exc_info and record.exc_info[0] is not None:
                        failure_type = record.exc_info[0].__name__
                    failure = ET.SubElement(testcase, "failure", {
                                            "message": record.getMessage(), "type": failure_type})
                    if record.exc_info:
                        formatter = self.formatter or logging.Formatter()
                        failure.text = formatter.formatException(
                            record.exc_info)
                    suite.attrib["failures"] = str(
                        int(suite.attrib["failures"]) + 1)
                elif record.levelno == logging.WARNING:
                    ET.SubElement(testcase, "skipped")

                suite.attrib["tests"] = str(int(suite.attrib["tests"]) + 1)
                self.buffer.append(record)

                if len(self.buffer) >= self.buffer_size:
                    self.flush()
            finally:
                self._lock.release()
        except Exception as e:
            print(
                f"JUnitHandler emit error for {self.filename}: {e}", file=sys.stderr)

    def flush(self) -> None:
        if not self._lock.acquire(timeout=2):
            print(
                f"Failed to acquire lock for flush in {self.filename}", file=sys.stderr)
            return
        try:
            if len(self.buffer) > 0:
                try:
                    elapsed = (datetime.now() -
                               self.start_time).total_seconds()
                    for suite in self.testsuites:
                        suite.attrib["time"] = str(
                            elapsed / len(self.testsuites))
                    tree = ET.ElementTree(self.testsuites)
                    ET.indent(tree, space="  ")
                    tree.write(self.filename, encoding="utf-8",
                               xml_declaration=True)
                    self.buffer.clear()
                except Exception as e:
                    print(
                        f"JUnitHandler flush error for {self.filename}: {e}", file=sys.stderr)
        finally:
            self._lock.release()

    def close(self) -> None:
        try:
            self.flush()
        except Exception as e:
            print(
                f"JUnitHandler close error for {self.filename}: {e}", file=sys.stderr)
        super().close()


# Global listeners and JUnit handlers
user_junit_handler = None
internal_junit_handler = None
internal_listener = None


def initialize_handlers():
    global config, user_junit_handler, internal_junit_handler, internal_listener
    config = config_handler.load()

    log_level = getattr(logging, config.log_level.upper(), logging.INFO)
    user_logger.setLevel(log_level)
    user_console_handler.setLevel(log_level)
    internal_logger.setLevel(log_level)
    internal_console_handler.setLevel(log_level)

    project_path = config.project_path or Path.home() / ".optics"
    log_dir = Path(project_path) / "execution_output"
    log_dir.mkdir(parents=True, exist_ok=True)

    if internal_listener:
        internal_listener.stop()

    internal_listener = QueueListener(
        internal_log_queue, internal_console_handler, respect_handler_level=True)

    if config.file_log:
        log_path = Path(config.log_path or log_dir /
                        "internal_logs.log").expanduser()
        file_handler = RotatingFileHandler(
            log_path, maxBytes=10*1024*1024, backupCount=10)
        file_handler.setFormatter(logging.Formatter(
            "%(levelname)s | %(asctime)s | %(name)s:%(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        file_handler.setLevel(log_level)
        internal_listener.handlers += (file_handler,)

    if config.json_log:
        global user_junit_handler, internal_junit_handler
        user_junit_path = log_dir / "user_test_results.xml"
        internal_junit_path = log_dir / "internal_test_results.xml"

        if user_junit_handler:
            user_junit_handler.close()
            user_logger.handlers = [
                h for h in user_logger.handlers if not isinstance(h, JUnitHandler)]
        user_junit_handler = JUnitHandler(user_junit_path, buffer_size=100)
        user_junit_handler.setLevel(log_level)
        user_logger.addHandler(user_junit_handler)

        if internal_junit_handler:
            internal_junit_handler.close()
            internal_logger.handlers = [
                h for h in internal_logger.handlers if not isinstance(h, JUnitHandler)]
        internal_junit_handler = JUnitHandler(
            internal_junit_path, buffer_size=100)
        internal_junit_handler.setLevel(log_level)
        internal_listener.handlers += (internal_junit_handler,)

    internal_listener.start()


initialize_handlers()

# Shutdown


def shutdown_logging():
    """Shuts down logging and related resources."""
    try:
        disable_logger()
        stop_listeners()
        wait_for_threads()
        flush_handlers()
        clear_queues()
    except Exception as e:
        print(f"Shutdown error: {e}", file=sys.stderr)


def disable_logger():
    """Disables the root logger."""
    logging.getLogger().disabled = True


def stop_listeners():
    """Stops user and internal listeners."""
    user_listener.stop()
    if internal_listener:
        internal_listener.stop()


def wait_for_threads():
    """Waits for listener threads to terminate with a timeout."""
    timeout = 2.0
    start_time = time.time()
    while time.time() - start_time < timeout:
        if not (is_thread_alive(user_listener) or is_thread_alive(internal_listener)):
            return
        time.sleep(0.1)
    check_thread_status()


def is_thread_alive(listener):
    """Checks if a listener's thread is alive."""
    return listener._thread and listener._thread.is_alive()


def check_thread_status():
    """Prints warnings if threads are still alive."""
    if is_thread_alive(user_listener):
        print("Warning: user_listener thread did not terminate", file=sys.stderr)
    if is_thread_alive(internal_listener):
        print("Warning: internal_listener thread did not terminate", file=sys.stderr)


def flush_handlers():
    """Flushes and closes JUnit handlers if they exist."""
    if user_junit_handler:
        user_junit_handler.flush()
        user_junit_handler.close()
    if internal_junit_handler:
        internal_junit_handler.flush()
        internal_junit_handler.close()


def clear_queues():
    """Clears user and internal log queues."""
    clear_queue(user_log_queue)
    clear_queue(internal_log_queue)


def clear_queue(log_queue):
    """Empties a single queue."""
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


__all__ = ["user_logger", "internal_logger", "reconfigure_logging"]
