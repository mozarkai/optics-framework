from optics_framework.common.events import EventSubscriber, Event, EventStatus, get_event_manager
import xml.etree.ElementTree as ET #nosec B405
from pathlib import Path
from typing import List, Dict
import xml.dom.minidom  #nosec B408
import time
import logging
from optics_framework.common.logging_config import internal_logger, execution_logger, SensitiveDataFormatter
from optics_framework.common.config_handler import ConfigHandler

junit_handler = None

def setup_junit():
    global junit_handler
    config_handler = ConfigHandler.get_instance()
    config = config_handler.load()
    log_dir = config.execution_output_path or (Path.cwd() / "logs")

    junit_path = getattr(config, 'json_log_path', None)
    if junit_path:
        junit_path = Path(junit_path).expanduser()
    else:
        junit_path = Path(log_dir) / "junit_output.xml"

    # Setup JUnit handler if enabled
    if getattr(config, 'json_log', False):
        if junit_handler:
            junit_handler.close()
            get_event_manager().unsubscribe("junit")
        junit_handler = JUnitEventHandler(junit_path)
        get_event_manager().subscribe("junit", junit_handler)
        internal_logger.debug(f"Subscribed JUnitEventHandler to EventManager: {junit_path}")

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

class JUnitEventHandler(EventSubscriber):
    def __init__(self, output_path: Path):
        self.output_path = output_path
        self.testsuites = ET.Element("testsuites")
        self.session_suites: Dict[str, ET.Element] = {}
        self.testcase_cases: Dict[str, ET.Element] = {}
        self.keyword_elements: Dict[str, List[ET.Element]] = {}  # per testcase
        self.start_times: Dict[str, float] = {}
        self.module_names: Dict[str, str] = {}
        self.module_elements: Dict[str, ET.Element] = {}
        self.active_keyword_elements: Dict[str, ET.Element] = {}  # Per keyword_id for update during execution
        self.keyword_log_buffers: Dict[str, LogCaptureBuffer] = {}

        internal_logger.debug(f"Initialized JUnitEventHandler with output: {self.output_path}")


    async def on_event(self, event: Event) -> None:
        internal_logger.debug(
            f"JUnitEventHandler received event: {event.model_dump()}")
        session_id = event.extra.get("session_id") if event.extra else None
        if not session_id and event.entity_type == "test_case":
            internal_logger.warning(
                f"Test case event missing session_id: {event.model_dump()}")
            execution_logger.warning(
                f"Test case event missing session_id: {event.model_dump()}")
            return

        if event.entity_type == "test_case":
            if session_id is None:
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
            session_suite.set("tests", str(
                int(session_suite.get("tests", "0")) + 1))
            internal_logger.debug(
                f"Created testcase: id={event.entity_id}, start_time={event_time}")
        elif event.entity_id in self.testcase_cases:
            testcase = self.testcase_cases[event.entity_id]
            elapsed = event_time - self.start_times.get(event.entity_id, event_time)
            testcase.set("time", f"{elapsed:.2f}")
            self._update_testcase_status(testcase, event, session_suite)
            # Attach all collected keywords as children
            for kw_element in self.keyword_elements.get(event.entity_id, []):
                testcase.append(kw_element)

            total_time = float(session_suite.get("time", "0")) + elapsed
            session_suite.set("time", f"{total_time:.2f}")

            # cleanup
            del self.testcase_cases[event.entity_id]
            del self.start_times[event.entity_id]
            del self.keyword_elements[event.entity_id]
            if event.entity_id in self.module_names:
                del self.module_names[event.entity_id]

    async def _handle_module_event(self, event: Event) -> None:
        testcase_id = event.parent_id
        if not testcase_id or testcase_id not in self.testcase_cases:
            return

        if event.status == EventStatus.RUNNING:
            testcase = self.testcase_cases[testcase_id]
            module_kw = ET.SubElement(testcase, "kw", name=event.name, type="setup", status="RUNNING")
            self.module_elements[event.entity_id] = module_kw

        elif event.status in [EventStatus.PASS, EventStatus.FAIL, EventStatus.ERROR]:
            module_kw = self.module_elements.get(event.entity_id)
            if module_kw:
                module_kw.set("status", event.status.value)

    async def _handle_keyword_event(self, event: Event) -> None:
        module_id = event.parent_id
        module_kw = self.module_elements.get(module_id)
        if module_kw is None:
            return  # Module not active yet

        kw_element = ET.SubElement(module_kw, "kw", name=event.name, status=event.status.value)

        if event.start_time:
            kw_element.set("starttime", time.strftime('%Y%m%d %H:%M:%S', time.localtime(event.start_time)))
        if event.end_time:
            kw_element.set("endtime", time.strftime('%Y%m%d %H:%M:%S', time.localtime(event.end_time)))
        if event.elapsed:
            kw_element.set("elapsed", f"{event.elapsed:.2f}")

        if event.args:
            args_element = ET.SubElement(kw_element, "arguments")
            for arg in event.args:
                ET.SubElement(args_element, "arg").text = str(arg)

        sensitive_formatter = SensitiveDataFormatter()
        if event.logs:
            internal_logger.info(f"Keyword {event.name} has logs: {event.logs}")
            for message in event.logs:
                senitised_message = sensitive_formatter._sanitize(message)
                log_element = ET.SubElement(kw_element, "log")
                log_element.text = senitised_message

        if event.parent_id not in self.keyword_elements:
            self.keyword_elements[event.parent_id] = []
        self.keyword_elements[event.parent_id].append(kw_element)


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
            internal_logger.debug(f"Flushing Robot-style XML to {self.output_path}")
            self.output_path.parent.mkdir(parents=True, exist_ok=True)

            # Convert ElementTree to string
            rough_string = ET.tostring(self.testsuites, encoding="utf-8")   #nosec B318
            reparsed = xml.dom.minidom.parseString(rough_string) #nosec B318
            pretty_xml = reparsed.toprettyxml(indent="  ")

            with open(self.output_path, "w", encoding="utf-8") as f:
                f.write(pretty_xml)
        except Exception as e:
            internal_logger.error(f"Failed to flush Robot-style XML: {str(e)}")

    def close(self):
        self.flush()
