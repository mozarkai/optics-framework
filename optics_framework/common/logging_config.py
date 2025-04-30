import logging
import queue
import os
import time
from typing import Tuple, Dict
from builtins import open
import atexit
import xml.etree.ElementTree as ET  # For JUnit XML generation # nosec B405
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from rich.logging import RichHandler

from optics_framework.common.config_handler import ConfigHandler
from optics_framework.common.events import EventSubscriber, Event, EventStatus, get_event_manager

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

# SessionLoggerAdapter
class SessionLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        session_id = self.extra.get("session_id", "unknown")
        kwargs.setdefault("extra", {})
        kwargs["extra"]["session_id"] = session_id
        return msg, kwargs

# LoggerContext
class LoggerContext:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.original_user_logger = user_logger
        self.original_internal_logger = internal_logger

    def __enter__(self) -> Tuple[logging.LoggerAdapter, logging.LoggerAdapter]:
        session_user_logger = SessionLoggerAdapter(
            self.original_user_logger, {"session_id": self.session_id})
        session_internal_logger = SessionLoggerAdapter(
            self.original_internal_logger, {"session_id": self.session_id})
        return session_user_logger, session_internal_logger

    def __exit__(self, exc_type, exc_value, traceback):
        pass


class JUnitEventHandler(EventSubscriber):
    def __init__(self, output_path: Path):
        self.output_path = output_path
        self.testsuites = ET.Element("testsuites")
        self.session_suites: Dict[str, ET.Element] = {}
        self.testcase_cases: Dict[str, ET.Element] = {}
        self.start_times: Dict[str, float] = {}
        self.module_names: Dict[str, str] = {}
        self.keyword_logs: Dict[str, list] = {}
        internal_logger.debug(
            f"Initialized JUnitEventHandler with output: {self.output_path}")

    async def on_event(self, event: Event) -> None:
        internal_logger.debug(
            f"JUnitEventHandler received event: {event.model_dump()}")
        session_id = event.extra.get("session_id") if event.extra else None
        if not session_id and event.entity_type == "test_case":
            internal_logger.warning(
                f"Test case event missing session_id: {event.model_dump()}")
            return

        if event.entity_type == "test_case":
            if not session_id:
                return
            if session_id not in self.session_suites:
                session_suite = ET.SubElement(
                    self.testsuites, "testsuite",
                    name=f"session_{session_id}",
                    tests="0", failures="0", errors="0", skipped="0", time="0"
                )
                self.session_suites[session_id] = session_suite
            await self._handle_test_case_event(event, self.session_suites[session_id], session_id)
        elif event.entity_type == "module":
            await self._handle_module_event(event)
        elif event.entity_type == "keyword":
            await self._handle_keyword_event(event)

    async def _handle_test_case_event(self, event: Event, session_suite: ET.Element, session_id: str) -> None:
        event_time = getattr(event, 'timestamp', time.time())
        internal_logger.debug(
            f"Handling test_case event: id={event.entity_id}, status={event.status}, timestamp={event_time}")
        if event.status == EventStatus.RUNNING:
            testcase = ET.SubElement(
                session_suite, "testcase",
                name=event.name, id=event.entity_id, classname=f"session_{session_id}", time="0"
            )
            self.testcase_cases[event.entity_id] = testcase
            self.start_times[event.entity_id] = event_time
            self.keyword_logs[event.entity_id] = []
            session_suite.set("tests", str(
                int(session_suite.get("tests", "0")) + 1))
            internal_logger.debug(
                f"Created testcase: id={event.entity_id}, start_time={event_time}")
        elif event.entity_id in self.testcase_cases:
            testcase = self.testcase_cases[event.entity_id]
            elapsed = event_time - \
                self.start_times.get(event.entity_id, event_time)
            testcase.set("time", f"{elapsed:.2f}")
            self._update_testcase_status(testcase, event, session_suite)
            if self.keyword_logs.get(event.entity_id):
                system_out = ET.SubElement(testcase, "system-out")
                system_out.text = "\n".join(self.keyword_logs[event.entity_id])
                internal_logger.debug(
                    f"Added system-out for testcase {event.entity_id}: {self.keyword_logs[event.entity_id]}")
            total_time = float(session_suite.get("time", "0")) + elapsed
            session_suite.set("time", f"{total_time:.2f}")
            internal_logger.debug(
                f"Completed testcase: id={event.entity_id}, elapsed={elapsed}, total_time={total_time}")
            del self.testcase_cases[event.entity_id]
            del self.start_times[event.entity_id]
            del self.keyword_logs[event.entity_id]
            if event.entity_id in self.module_names:
                del self.module_names[event.entity_id]

    async def _handle_module_event(self, event: Event) -> None:
        testcase_id = event.parent_id
        internal_logger.debug(
            f"Handling module event: id={event.entity_id}, name={event.name}, testcase_id={testcase_id}")
        if not testcase_id or testcase_id not in self.testcase_cases:
            internal_logger.debug(
                f"Module event {event.name} ignored: testcase_id {testcase_id} not active")
            return
        if event.status == EventStatus.RUNNING:
            self.module_names[event.entity_id] = event.name
            self.testcase_cases[testcase_id].set("module_id", event.entity_id)
            internal_logger.debug(
                f"Set module name for testcase {testcase_id}: {event.name}, module_id={event.entity_id}")

    async def _handle_keyword_event(self, event: Event) -> None:
        module_id = event.parent_id
        testcase_id = None
        for tid, testcase in self.testcase_cases.items():
            if testcase.get("module_id") == module_id:
                testcase_id = tid
                break
        internal_logger.debug(
            f"Handling keyword event: id={event.entity_id}, name={event.name}, testcase_id={testcase_id}, module_id={module_id}")
        if not testcase_id or testcase_id not in self.testcase_cases:
            internal_logger.debug(
                f"Keyword event {event.name} ignored: testcase_id {testcase_id} not active")
            return
        module_name = self.module_names.get(module_id, "unknown")
        log_entry = f"Keyword: {event.name}, Module: {module_name}, Status: {event.status.value}"
        self.keyword_logs[testcase_id].append(log_entry)
        internal_logger.debug(
            f"Logged keyword for testcase {testcase_id}: {log_entry}")

    def _update_testcase_status(self, testcase: ET.Element, event: Event, testsuite: ET.Element) -> None:
        testcase.set("status", event.status.value)
        if event.status == EventStatus.FAIL:
            failure = ET.SubElement(
                testcase, "failure", message=event.message, type="Failure")
            failure.text = event.message
            testsuite.set("failures", str(
                int(testsuite.get("failures", "0")) + 1))
        elif event.status == EventStatus.ERROR:
            error = ET.SubElement(
                testcase, "error", message=event.message, type="Error")
            error.text = event.message
            testsuite.set("errors", str(int(testsuite.get("errors", "0")) + 1))
        elif event.status == EventStatus.SKIPPED:
            ET.SubElement(testcase, "skipped")
            testsuite.set("skipped", str(
                int(testsuite.get("skipped", "0")) + 1))

    def flush(self):
        try:
            internal_logger.debug(f"Flushing JUnit XML to {self.output_path}")
            tree = ET.ElementTree(self.testsuites)
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.output_path, "wb") as f:  # Use built-in open
                tree.write(f, encoding="utf-8", xml_declaration=True)
        except Exception as e:
            internal_logger.error(
                f"Failed to flush JUnit XML to {self.output_path}: {str(e)}")

    def close(self):
        self.flush()


junit_handler = None
internal_listener = None


def initialize_handlers():
    global config, junit_handler, internal_listener
    config = config_handler.load()
    internal_logger.debug("Initializing logging handlers")

    log_level_str = config.log_level.upper()
    log_level = getattr(logging, log_level_str, logging.INFO)
    internal_logger.debug(
        f"Setting log level to {log_level_str} ({log_level})")

    if user_logger is None or internal_logger is None:
        raise RuntimeError(
            f"Loggers not initialized: user_logger={user_logger}, internal_logger={internal_logger}")

    user_logger.setLevel(log_level)
    user_console_handler.setLevel(log_level)
    internal_logger.setLevel(log_level)
    internal_console_handler.setLevel(log_level)

    project_path = config.project_path or Path.home() / ".optics"
    log_dir = Path(project_path) / "execution_output"
    log_dir.mkdir(parents=True, exist_ok=True)
    internal_logger.debug(
        f"Output directory: {log_dir}, writable={os.access(log_dir, os.W_OK)}")

    if internal_listener:
        internal_listener.stop()

    internal_listener = QueueListener(
        internal_log_queue, internal_console_handler, respect_handler_level=True)

    if config.file_log or log_level <= logging.DEBUG:
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
        internal_logger.debug(f"Added file handler: {log_path}")

    if getattr(config, 'json_log', False):
        junit_path = log_dir / "junit_output.xml"
        if junit_handler:
            junit_handler.close()
            get_event_manager().unsubscribe("junit")
        junit_handler = JUnitEventHandler(junit_path)
        get_event_manager().subscribe("junit", junit_handler)
        internal_logger.debug(
            f"Subscribed JUnitEventHandler to EventManager: {junit_path}")

    internal_listener.start()
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
    return listener is not None and listener._thread and listener._thread.is_alive()


def check_thread_status():
    if is_thread_alive(user_listener):
        internal_logger.warning("user_listener thread did not terminate")
    if is_thread_alive(internal_listener):
        internal_logger.warning("internal_listener thread did not terminate")


def flush_handlers():
    if junit_handler:
        try:
            junit_handler.close()
        except Exception as e:
            internal_logger.error(f"Error closing junit_handler: {e}")


def clear_queues():
    for log_queue in [user_log_queue, internal_log_queue]:
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


__all__ = ["user_logger", "internal_logger",
           "reconfigure_logging", "LoggerContext", "SessionLoggerAdapter"]
