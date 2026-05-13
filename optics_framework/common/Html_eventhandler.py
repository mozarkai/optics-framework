import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from optics_framework.common.config_handler import Config
from optics_framework.common.events import Event, EventStatus, EventSubscriber, get_event_manager
from optics_framework.common.logging_config import SensitiveDataFormatter, internal_logger


TERMINAL_STATUSES = {
    EventStatus.PASS,
    EventStatus.FAIL,
    EventStatus.ERROR,
    EventStatus.SKIPPED,
}


class HtmlHandlerRegistry:
    def __init__(self):
        self._handlers: Dict[str, "HtmlEventHandler"] = {}
        self._lock = threading.Lock()

    def setup_html_for_session(self, session_id: str, config: Config) -> None:
        with self._lock:
            if session_id in self._handlers:
                self._handlers[session_id].close()
                del self._handlers[session_id]

            if not getattr(config, "file_log", False) and not getattr(config, "json_log", False):
                return

            html_path = self._get_session_html_path(session_id, config)
            handler = HtmlEventHandler(html_path, session_id=session_id, config=config)

            event_manager = get_event_manager(session_id)
            event_manager.subscribe("html", handler)
            handler.flush()

            self._handlers[session_id] = handler

    def _get_session_html_path(self, session_id: str, config: Config) -> Path:
        log_dir = config.execution_output_path or (Path.cwd() / "logs")
        return Path(log_dir) / f"report_{session_id}.html"

    def cleanup_session(self, session_id: str) -> None:
        with self._lock:
            if session_id in self._handlers:
                self._handlers[session_id].close()
                del self._handlers[session_id]

    def get_handler(self, session_id: str) -> Optional["HtmlEventHandler"]:
        with self._lock:
            return self._handlers.get(session_id)

    def get_active_sessions(self) -> List[str]:
        with self._lock:
            return list(self._handlers.keys())


_html_handler_registry = HtmlHandlerRegistry()


def setup_html(session_id: str, config: Config) -> None:
    _html_handler_registry.setup_html_for_session(session_id, config)


def get_html_handler_registry() -> HtmlHandlerRegistry:
    return _html_handler_registry


class HtmlEventHandler(EventSubscriber):
    def __init__(self, output_path: Path, session_id: str, config: Config):
        self.output_path = Path(output_path)
        self.session_id = session_id
        self.config = config
        self.test_cases: Dict[str, Dict[str, Any]] = {}
        self.module_to_testcase: Dict[str, str] = {}
        self.start_times: Dict[str, float] = {}
        self.session_start = time.time()
        self.session_end: Optional[float] = None
        self.execution_status = "RUNNING"
        self.execution_message = ""
        self.sensitive_formatter = SensitiveDataFormatter()
        self.metadata = self._build_metadata(config)

    async def on_event(self, event: Event) -> None:
        session_id = event.extra.get("session_id") if event.extra else None
        if session_id and session_id != self.session_id:
            return

        if event.entity_type == "execution":
            self._handle_execution_event(event)
        elif event.entity_type == "test_case":
            self._handle_test_case_event(event)
        elif event.entity_type == "module":
            self._handle_module_event(event)
        elif event.entity_type == "keyword":
            self._handle_keyword_event(event)

        if event.entity_type == "test_case" and event.status in TERMINAL_STATUSES:
            self.flush()

    def _handle_execution_event(self, event: Event) -> None:
        self.execution_status = event.status.value
        self.execution_message = event.message
        if event.status in TERMINAL_STATUSES:
            self.session_end = event.end_time or event.timestamp

    def _handle_test_case_event(self, event: Event) -> None:
        event_time = event.timestamp or time.time()
        if event.status == EventStatus.RUNNING:
            self.test_cases[event.entity_id] = {
                "id": event.entity_id,
                "name": event.name,
                "status": EventStatus.RUNNING.value,
                "message": "",
                "duration": 0.0,
                "start_time": event.start_time or event_time,
                "end_time": None,
                "modules": {},
            }
            self.start_times[event.entity_id] = event.start_time or event_time
            return

        test_case = self.test_cases.setdefault(
            event.entity_id,
            {
                "id": event.entity_id,
                "name": event.name,
                "status": EventStatus.NOT_RUN.value,
                "message": "",
                "duration": 0.0,
                "start_time": event.start_time or event_time,
                "end_time": None,
                "modules": {},
            },
        )
        test_case["status"] = event.status.value
        test_case["message"] = self._sanitize(event.message)
        test_case["duration"] = self._elapsed_for_event(event, event.entity_id)
        test_case["end_time"] = event.end_time or event_time

    def _handle_module_event(self, event: Event) -> None:
        testcase_id = event.parent_id
        if not testcase_id or testcase_id not in self.test_cases:
            return

        modules = self.test_cases[testcase_id]["modules"]
        if event.status == EventStatus.RUNNING:
            modules[event.entity_id] = {
                "id": event.entity_id,
                "name": event.name,
                "status": EventStatus.RUNNING.value,
                "message": "",
                "duration": 0.0,
                "start_time": event.start_time or event.timestamp,
                "end_time": None,
                "keywords": {},
            }
            self.module_to_testcase[event.entity_id] = testcase_id
            self.start_times[event.entity_id] = event.start_time or event.timestamp
            return

        module = modules.get(event.entity_id)
        if not module:
            return
        module["status"] = event.status.value
        module["message"] = self._sanitize(event.message)
        module["duration"] = self._elapsed_for_event(event, event.entity_id)
        module["end_time"] = event.end_time or event.timestamp

    def _handle_keyword_event(self, event: Event) -> None:
        module_id = event.parent_id
        testcase_id = self.module_to_testcase.get(module_id or "")
        if not testcase_id:
            return

        module = self.test_cases[testcase_id]["modules"].get(module_id)
        if not module:
            return

        keywords = module["keywords"]
        keyword = keywords.setdefault(
            event.entity_id,
            {
                "id": event.entity_id,
                "name": event.name,
                "status": EventStatus.NOT_RUN.value,
                "message": "",
                "duration": 0.0,
                "start_time": event.start_time or event.timestamp,
                "end_time": None,
                "args": [],
                "logs": [],
                "screenshot": None,
            },
        )
        keyword["status"] = event.status.value
        keyword["message"] = self._sanitize(event.message)
        keyword["duration"] = self._elapsed_for_event(event, event.entity_id)
        keyword["start_time"] = event.start_time or keyword["start_time"]
        keyword["end_time"] = event.end_time or event.timestamp
        keyword["args"] = self._normalize_args(event.args)
        keyword["logs"] = [self._sanitize(str(item)) for item in (event.logs or [])]
        if event.status in {EventStatus.FAIL, EventStatus.ERROR}:
            keyword["screenshot"] = self._find_failure_screenshot(event)

    def _build_metadata(self, config: Config) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "project_path": config.project_path,
            "execution_output_path": config.execution_output_path,
            "log_level": config.log_level,
            "drivers": self._enabled_dependencies(config.driver_sources),
            "element_sources": self._enabled_dependencies(config.elements_sources),
            "text_detection": self._enabled_dependencies(config.text_detection),
            "image_detection": self._enabled_dependencies(config.image_detection),
        }

    def _enabled_dependencies(self, dependencies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        enabled = []
        for item in dependencies or []:
            for name, details in item.items():
                is_enabled = getattr(details, "enabled", False)
                if not is_enabled:
                    continue
                capabilities = getattr(details, "capabilities", {}) or {}
                enabled.append(
                    {
                        "name": name,
                        "url": getattr(details, "url", None),
                        "capabilities": self._sanitize_value(capabilities),
                    }
                )
        return enabled

    def _sanitize_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._sanitize(value)
        if isinstance(value, dict):
            return {key: self._sanitize_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._sanitize_value(item) for item in value]
        return value

    def _sanitize(self, value: str) -> str:
        return self.sensitive_formatter._sanitize(value or "")

    def _normalize_args(self, args: Any) -> List[str]:
        if args is None:
            return []
        if isinstance(args, dict):
            return [f"{key}={self._sanitize(str(value))}" for key, value in args.items()]
        return [self._sanitize(str(item)) for item in args]

    def _elapsed_for_event(self, event: Event, entity_id: str) -> float:
        if event.elapsed is not None:
            return max(0.0, float(event.elapsed))
        start_time = event.start_time or self.start_times.get(entity_id)
        end_time = event.end_time or event.timestamp
        if start_time and end_time:
            return max(0.0, float(end_time) - float(start_time))
        return 0.0

    def _find_failure_screenshot(self, event: Event) -> Optional[str]:
        output_dir = self.output_path.parent
        if not output_dir.exists():
            return None

        start_time = event.start_time or 0.0
        end_time = event.end_time or event.timestamp or time.time()
        image_paths = [
            path
            for path in output_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ]
        if not image_paths:
            return None

        candidates = [
            path
            for path in image_paths
            if start_time - 5 <= path.stat().st_mtime <= end_time + 5
        ]
        if not candidates:
            candidates = [path for path in image_paths if path.stat().st_mtime <= end_time + 5]
        if not candidates:
            return None

        latest = max(candidates, key=lambda path: path.stat().st_mtime)
        return Path(os.path.relpath(latest, output_dir)).as_posix()

    def _report_payload(self) -> Dict[str, Any]:
        end_time = self.session_end or time.time()
        test_cases = []
        passed = failed = skipped = 0

        for test_case in self.test_cases.values():
            status = test_case["status"]
            passed += int(status == EventStatus.PASS.value)
            failed += int(status in {EventStatus.FAIL.value, EventStatus.ERROR.value})
            skipped += int(status == EventStatus.SKIPPED.value)
            modules = []
            for module in test_case["modules"].values():
                modules.append(
                    {
                        **{key: module[key] for key in ("id", "name", "status", "message", "duration", "start_time", "end_time")},
                        "keywords": list(module["keywords"].values()),
                    }
                )
            test_cases.append(
                {
                    **{key: test_case[key] for key in ("id", "name", "status", "message", "duration", "start_time", "end_time")},
                    "modules": modules,
                }
            )

        total = len(test_cases)
        overall_status = self.execution_status
        if total:
            overall_status = EventStatus.FAIL.value if failed else EventStatus.PASS.value
        return {
            "summary": {
                "overall_status": overall_status,
                "total": total,
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "duration": max(0.0, end_time - self.session_start),
                "message": self.execution_message,
            },
            "metadata": {
                **self.metadata,
                "start_time": self.session_start,
                "end_time": end_time,
            },
            "test_cases": test_cases,
        }

    def flush(self) -> None:
        try:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            template_path = Path(__file__).parent / "report_template.html"

            if not template_path.exists():
                internal_logger.error("report_template.html not found.")
                return

            html_content = template_path.read_text(encoding="utf-8")
            payload = json.dumps(self._report_payload(), ensure_ascii=False).replace("</", "<\\/")
            html_content = html_content.replace("{{REPORT_DATA_JSON}}", payload)
            self.output_path.write_text(html_content, encoding="utf-8")
        except Exception as e:
            internal_logger.error(f"Failed to flush HTML report: {str(e)}", exc_info=True)

    def close(self) -> None:
        self.session_end = self.session_end or time.time()
        self.flush()
