"""Interactive live session controller for the ``optics live`` command.

This module holds the non-UI half of the live experience. It keeps a single
framework :class:`~optics_framework.common.session_manager.Session` alive for
the whole session, resolves and executes individual keywords against it (reusing
the same ``KeywordRegistry`` and ``${element}`` resolution the batch runner uses),
records executed actions, and persists them as framework-compatible CSV modules.

The full-screen terminal UI lives in :mod:`optics_framework.helper.live_tui`.
"""

import io
import os
import re
import csv
import sys
import time
import shlex
import shutil
import logging
import tempfile
import subprocess
import inspect
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

import yaml

from optics_framework.common import utils
from optics_framework.common.config_handler import Config, DependencyConfig
from optics_framework.common.error import OpticsError, Code
from optics_framework.common.logging_config import (
    internal_logger,
    execution_logger,
    LogCaptureBuffer,
    initialize_handlers,
)
from optics_framework.common.models import ModuleData, ElementData, ApiData, TemplateData
from optics_framework.common.session_manager import SessionManager, Session
from optics_framework.common.runner.keyword_register import KeywordRegistry
from optics_framework.common.runner.data_reader import (
    CSVDataReader,
    YAMLDataReader,
    DataReader,
)
from optics_framework.common.utils import escape_csv_value
from optics_framework.api import ActionKeyword, AppManagement, FlowControl, Verifier
from optics_framework.helper.execute import discover_templates, identify_file_content


# Map locator-strategy class names (logged by ExecutionTracer) to short labels.
_STRATEGY_LABELS: Dict[str, str] = {
    "XPathStrategy": "XPath",
    "TextElementStrategy": "Text",
    "TextDetectionStrategy": "OCR",
    "ImageDetectionStrategy": "Image",
}
_STRATEGY_SUCCESS_RE = re.compile(r"Trying (\w+) on .*? \.\.\. SUCCESS", re.IGNORECASE)


@dataclass
class ActionResult:
    """Outcome of a single keyword execution, ready to render in the history pane."""

    raw: str
    keyword: str = ""
    params: List[str] = field(default_factory=list)
    status: str = "PASS"  # PASS | FAIL | INFO
    elapsed: float = 0.0
    strategy: Optional[str] = None
    message: Optional[str] = None
    recorded: bool = False


def keyword_to_title(func_name: str) -> str:
    """Convert a snake_case keyword name to the Title Case form used in CSV modules.

    ``press_element`` -> ``Press Element`` (matches the framework's existing modules).
    """
    return " ".join(word.capitalize() for word in func_name.split("_"))


_CONFIG_KEYS = frozenset({
    "driver_sources", "element_sources", "elements_sources",
    "text_detection", "image_detection",
    "log_level", "json_log", "file_log",
})


def _load_partial_config(folder_path: str) -> Optional[Config]:
    """Load the first YAML in ``folder_path`` that looks like an Optics config.

    Lenient on purpose: any file containing at least one recognised top-level key
    counts. Missing sections are filled in later by :func:`_compose_config` rather
    than rejecting the file.
    """
    for root, _dirs, files in os.walk(folder_path):
        for fname in files:
            if not fname.lower().endswith((".yml", ".yaml")):
                continue
            path = os.path.join(root, fname)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
            except (OSError, yaml.YAMLError) as exc:
                internal_logger.debug("Skipping unreadable YAML %s: %s", path, exc)
                continue
            if not isinstance(data, dict) or not (set(data.keys()) & _CONFIG_KEYS):
                continue
            if "element_sources" in data and "elements_sources" not in data:
                data["elements_sources"] = data.pop("element_sources")
            try:
                return Config(**data)
            except Exception as exc:
                internal_logger.error("Invalid config in %s: %s", path, exc)
    return None


def _default_appium_caps() -> Dict[str, Any]:
    """Minimum-viable Appium capabilities: enough to open a session, no app launch.

    Picks the first ``adb devices`` entry as the target so a session starts even
    when the user has no config and no fancy setup. Android-only by design — iOS
    users supply their own config.yaml.
    """
    caps: Dict[str, Any] = {
        "platformName": "Android",
        "automationName": "UiAutomator2",
    }
    devices = LiveController.list_devices()
    if devices:
        caps["udid"] = devices[0]
        caps["deviceName"] = devices[0]
    return caps


def _default_driver_source() -> Dict[str, DependencyConfig]:
    return {
        "appium": DependencyConfig(
            enabled=True,
            url="http://127.0.0.1:4723",
            capabilities=_default_appium_caps(),
        )
    }


def _default_element_sources() -> List[Dict[str, DependencyConfig]]:
    return [
        {"appium_find_element": DependencyConfig(enabled=True, url=None, capabilities={})},
        {"appium_page_source": DependencyConfig(enabled=True, url=None, capabilities={})},
        {"appium_screenshot": DependencyConfig(enabled=True, url=None, capabilities={})},
    ]


def _has_enabled(sources: List[Dict[str, DependencyConfig]]) -> bool:
    return any(details.enabled for item in sources for _name, details in item.items())


def _compose_config(folder_path: Optional[str]) -> Config:
    """Build a working ``Config``: user-provided pieces win, defaults fill the gaps.

    Goal: ``optics live`` should run without a config.yaml — and a partial config
    should not be rejected. Anything the user specifies is preserved; anything
    missing (no enabled driver, no enabled element source) gets a default appended.
    """
    config = _load_partial_config(folder_path) if folder_path else None
    if config is None:
        config = Config()
    if not _has_enabled(config.driver_sources):
        config.driver_sources = [_default_driver_source(), *config.driver_sources]
    if not _has_enabled(config.elements_sources):
        config.elements_sources = [*_default_element_sources(), *config.elements_sources]
    return config


class LiveController:
    """Owns the long-lived session and exposes live keyword/recording operations.

    Unlike the batch runner (which builds a session, runs once, then tears it down),
    this controller keeps the session and its driver open across many keyword calls.
    """

    def __init__(self, folder_path: Optional[str] = None):
        # The folder is optional: with no config, optics live still runs against
        # the first connected device using sensible Android defaults.
        if folder_path:
            self.folder_path = os.path.abspath(folder_path)
            if not os.path.isdir(self.folder_path):
                raise OpticsError(Code.E0501, message=f"Invalid project folder: {self.folder_path}")
        else:
            self.folder_path = os.getcwd()

        config = _compose_config(self.folder_path if folder_path else None)
        config.project_path = self.folder_path
        # Route framework-generated artifacts (the auto pre-/post-action screenshots
        # written by @with_self_healing, AOI captures, logs, etc.) to a tempdir so
        # they don't litter the project. Cleaned on teardown — only things the user
        # explicitly persists (/save CSVs, /screenshot captures) survive the session.
        self._artifacts_tempdir: str = tempfile.mkdtemp(prefix="optics_live_")
        config.execution_output_path = self._artifacts_tempdir
        self.config: Config = config
        initialize_handlers(self.config)

        self.templates: TemplateData = discover_templates(self.folder_path)
        self.manager = SessionManager()
        # No test cases yet; elements are loaded lazily on first use.
        self.session_id: str = self.manager.create_session(
            self.config,
            None,
            ModuleData(),
            ElementData(),
            ApiData(),
            self.templates,
        )
        session = self.manager.get_session(self.session_id)
        if session is None:  # pragma: no cover - defensive
            raise OpticsError(Code.E0702, message="Failed to create live session")
        self.session: Session = session

        self.keyword_map: Dict[str, Callable[..., Any]] = {}
        self._action_keyword: Optional[ActionKeyword] = None
        self._build_registry()

        # Per-session log file. Lives under <project>/logs/ so it survives /quit
        # (unlike the tempdir holding screenshots, which is rebuildable). Attached
        # to both optics loggers so we get internal + execution chronologically
        # interleaved — the most useful view when debugging a session.
        self._live_log_handler: Optional[logging.Handler] = None
        self.live_log_path: Optional[str] = self._setup_live_logging()

        self._elements_loaded = False
        self.recorded: List[Tuple[str, List[str]]] = []
        self.saved = True  # nothing recorded yet -> considered "saved"
        self.active_device_serial: Optional[str] = self._device_from_config()

    # -- Logging ------------------------------------------------------------------

    def _setup_live_logging(self) -> Optional[str]:
        """Create and attach a per-session log file. Returns the file path or None.

        Routes both ``optics.internal`` and ``optics.execution`` records into one
        chronological file under ``<project>/logs/``. The handler is a plain
        :class:`FileHandler` (not a RotatingFileHandler) — one file per session,
        easy to find, no rotation surprises during a short interactive run.

        The console-suppression context manager (:func:`_silence_console_logging`)
        explicitly excludes :class:`FileHandler` instances, so this handler keeps
        receiving records throughout the TUI's lifetime.
        """
        log_dir = os.path.join(self.folder_path, "logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
        except OSError as exc:
            internal_logger.error("Could not create log dir %s: %s", log_dir, exc)
            return None
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S")
        log_path = os.path.join(log_dir, f"optics_live_{timestamp}.log")
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)  # let the loggers' own levels gate volume
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        for logger in (
            logging.getLogger("optics.internal"),
            logging.getLogger("optics.execution"),
        ):
            logger.addHandler(handler)
            # Default level (NOTSET) inherits from root, which is WARNING. We want
            # INFO so the framework's "Locating element", "Trying X strategy",
            # "Pressing at coordinates" lines actually land in the file.
            if logger.level == logging.NOTSET or logger.level > logging.INFO:
                logger.setLevel(logging.INFO)
        self._live_log_handler = handler
        return log_path

    def _teardown_live_logging(self) -> None:
        if self._live_log_handler is None:
            return
        for logger in (
            logging.getLogger("optics.internal"),
            logging.getLogger("optics.execution"),
        ):
            try:
                logger.removeHandler(self._live_log_handler)
            except ValueError:
                pass
        try:
            self._live_log_handler.close()
        except Exception:  # pragma: no cover - defensive
            pass
        self._live_log_handler = None

    # -- Registry / session setup -------------------------------------------------

    def _build_registry(self) -> None:
        """Build the keyword map exactly as the normal runner does (RunnerFactory)."""
        registry = KeywordRegistry()
        action_keyword = self.session.optics.build(ActionKeyword)
        registry.register(action_keyword)
        registry.register(self.session.optics.build(AppManagement))
        registry.register(self.session.optics.build(Verifier))
        registry.register(FlowControl(session=self.session, keyword_map=registry.keyword_map))
        self.keyword_map = registry.keyword_map
        self._action_keyword = action_keyword

    # -- Keyword introspection (for autocomplete + ghost text) --------------------

    def keyword_names(self) -> List[str]:
        """Sorted list of available keyword names (snake_case), live from the registry."""
        return sorted(self.keyword_map.keys())

    def keyword_signature(self, func_name: str) -> Optional[str]:
        """Return a ghost-text parameter hint, e.g. ``press_element <element> [repeat]``.

        Required parameters are shown as ``<name>``, optional ones as ``[name]``.
        """
        method = self.keyword_map.get(func_name)
        if method is None:
            return None
        try:
            sig = inspect.signature(method)
        except (TypeError, ValueError):
            return None
        parts: List[str] = [func_name]
        for name, param in sig.parameters.items():
            if name == "self":
                continue
            # Skip keyword-only sentinels like ``located`` used by self-healing.
            if param.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.VAR_KEYWORD):
                continue
            if param.kind == inspect.Parameter.VAR_POSITIONAL:
                parts.append(f"[{name}...]")
            elif param.default is inspect.Parameter.empty:
                parts.append(f"<{name}>")
            else:
                parts.append(f"[{name}]")
        return " ".join(parts)

    # -- Element loading (lazy) ---------------------------------------------------

    def ensure_elements_loaded(self) -> None:
        """Load named elements from the project on first use (not eagerly at startup)."""
        if self._elements_loaded:
            return
        csv_reader = CSVDataReader()
        yaml_reader = YAMLDataReader()
        elements = self.session.elements if self.session.elements is not None else ElementData()
        for root, _dirs, files in os.walk(self.folder_path):
            for fname in files:
                path = os.path.join(root, fname)
                lname = fname.lower()
                if not lname.endswith((".csv", ".yml", ".yaml")):
                    continue
                try:
                    if "elements" not in identify_file_content(path):
                        continue
                    reader = csv_reader if lname.endswith(".csv") else yaml_reader
                    for name, values in reader.read_elements(path).items():
                        for value in values:
                            existing = elements.get_element(name) or []
                            if value not in existing:
                                elements.add_element(name, value)
                except Exception as exc:  # pragma: no cover - defensive
                    internal_logger.debug("Failed to load elements from %s: %s", path, exc)
        self.session.elements = elements
        self._elements_loaded = True

    def element_names(self) -> List[str]:
        """Names of loaded elements (loads them on first call)."""
        self.ensure_elements_loaded()
        if self.session.elements is None:
            return []
        return sorted(self.session.elements.elements.keys())

    def element_first_locator(self, name: str) -> Optional[str]:
        """First (highest-priority) locator for an element, for inline autocomplete display."""
        if self.session.elements is None:
            return None
        return self.session.elements.get_first(name)

    # -- Keyword execution --------------------------------------------------------

    def run_keyword(self, raw: str) -> ActionResult:
        """Execute one keyword call. Blocking: callers run this off the UI thread.

        Mirrors the batch runner's parameter handling: bare ``${element}`` positional
        arguments expand into fallback candidates tried in order, ``key=value`` tokens
        become keyword arguments, and the winning locator strategy is read back from the
        execution tracer's logs.
        """
        raw = raw.strip()
        start = time.time()
        try:
            tokens = shlex.split(raw, posix=True)
        except ValueError as exc:
            return ActionResult(raw=raw, status="FAIL", message=f"Parse error: {exc}")
        if not tokens:
            return ActionResult(raw=raw, status="FAIL", message="Empty command")

        keyword_token, params = tokens[0], tokens[1:]
        func_name = "_".join(keyword_token.split()).lower()
        method = self.keyword_map.get(func_name)
        if method is None:
            return ActionResult(
                raw=raw,
                keyword=keyword_token,
                status="FAIL",
                message=f"Unknown keyword: {keyword_token}",
            )

        if any(p.startswith("${") for p in params):
            self.ensure_elements_loaded()

        try:
            param_candidates = self._build_candidates(params)
        except OpticsError as exc:
            return ActionResult(
                raw=raw,
                keyword=func_name,
                params=params,
                status="FAIL",
                message=self._format_error(exc),
                elapsed=time.time() - start,
            )

        strategy_capture = LogCaptureBuffer()
        strategy_capture.setLevel(logging.DEBUG)
        execution_logger.addHandler(strategy_capture)
        # ExecutionTracer logs the winning strategy at INFO; make sure that level
        # reaches our buffer even if the project configured a higher log level.
        prev_level = execution_logger.level
        if prev_level == logging.NOTSET or prev_level > logging.INFO:
            execution_logger.setLevel(logging.INFO)

        # Many framework methods (appium swipe with a bad direction, scroll with an
        # unsupported direction, force_terminate failures, etc.) report problems via
        # ``internal_logger.error(...)`` and then RETURN — they never raise. Without
        # watching the logger we'd record those as passes. Capture internal_logger
        # records per-combo so we can flag a "silent" failure after method() returns.
        internal_capture = LogCaptureBuffer()
        internal_capture.setLevel(logging.DEBUG)
        internal_logger_obj = logging.getLogger("optics.internal")
        internal_logger_obj.addHandler(internal_capture)

        last_exc: Optional[BaseException] = None
        try:
            for combo in product(*param_candidates):
                internal_capture.clear()
                try:
                    positional, keywords = self._resolve_candidate(combo)
                    method(*positional, **keywords)
                except OpticsError as exc:
                    last_exc = exc
                    # Element-location codes (E02xx / X0201) mean "not found here" ->
                    # try the next fallback locator. Use .value because str(Code.X)
                    # renders as "Code.X" on the str-Enum under Python 3.12.
                    if exc.code.value.startswith("E02") or exc.code == Code.X0201:
                        continue
                    break
                except Exception as exc:  # noqa: BLE001 - surfaced to the user, never crashes
                    last_exc = exc
                    break

                # The method returned normally. Did it log a failure on its way out?
                silent_error = self._find_error_log(internal_capture)
                if silent_error is not None:
                    # Not a locator-fallback case — internal errors (bad direction,
                    # unsupported element type, ...) won't get better on retry.
                    last_exc = OpticsError(Code.E0401, message=silent_error)
                    break

                elapsed = time.time() - start
                self.recorded.append((func_name, params))
                self.saved = False
                return ActionResult(
                    raw=raw,
                    keyword=func_name,
                    params=params,
                    status="PASS",
                    elapsed=elapsed,
                    strategy=self._winning_strategy(strategy_capture),
                    recorded=True,
                )
        finally:
            execution_logger.removeHandler(strategy_capture)
            execution_logger.setLevel(prev_level)
            internal_logger_obj.removeHandler(internal_capture)

        return ActionResult(
            raw=raw,
            keyword=func_name,
            params=params,
            status="FAIL",
            elapsed=time.time() - start,
            message=self._format_error(last_exc),
        )

    def _build_candidates(self, params: List[str]) -> List[List[str]]:
        """Expand each positional ``${element}`` into its list of fallback locators."""
        candidates: List[List[str]] = []
        for param in params:
            if param.startswith("${") and param.endswith("}"):
                var_name = param[2:-1].strip()
                values = self.session.elements.get_element(var_name) if self.session.elements else None
                if not values:
                    raise OpticsError(Code.E0201, message=f"Element not found: {var_name}")
                candidates.append(list(values))
            else:
                candidates.append([param])
        return candidates

    def _resolve_candidate(self, combo: Tuple[str, ...]) -> Tuple[List[str], Dict[str, str]]:
        """Split one candidate combination into positional args and keyword args."""
        combo_list = list(combo)
        kw_params = DataReader.get_keyword_params(combo_list)
        positional = DataReader.get_positional_params(combo_list)
        resolved_positional = [self._resolve_value(p) for p in positional]
        resolved_kw: Dict[str, str] = {}
        for key, value in kw_params.items():
            if value.startswith("${") and value.endswith("}"):
                value = self._resolve_value(value)
            resolved_kw[key] = value
        return resolved_positional, resolved_kw

    def _resolve_value(self, value: str) -> str:
        """Resolve a single ``${element}`` reference to its first locator; pass through otherwise."""
        if not (value.startswith("${") and value.endswith("}")):
            return value
        var_name = value[2:-1].strip()
        resolved = self.session.elements.get_first(var_name) if self.session.elements else None
        if resolved is None:
            raise OpticsError(Code.E0201, message=f"Element not found: {var_name}")
        return resolved

    @staticmethod
    def _find_error_log(capture: LogCaptureBuffer) -> Optional[str]:
        """Return the message of the first ERROR+ record in ``capture``, or None.

        Used to detect cases where the framework reports a failure via logging
        instead of raising (the appium driver's "Unknown swipe direction" path
        is the canonical example). A returned value means the keyword "succeeded"
        in the Python sense but didn't actually do what was asked.
        """
        for record in capture.records:
            if isinstance(record, logging.LogRecord) and record.levelno >= logging.ERROR:
                try:
                    return record.getMessage()
                except Exception:  # pragma: no cover - defensive
                    return str(record)
        return None

    @staticmethod
    def _winning_strategy(capture: LogCaptureBuffer) -> Optional[str]:
        """Read the last successful locator strategy from captured execution logs."""
        for record in reversed(capture.records):
            try:
                message = record.getMessage() if isinstance(record, logging.LogRecord) else str(record)
            except Exception:  # pragma: no cover - defensive
                continue
            match = _STRATEGY_SUCCESS_RE.search(message)
            if match:
                cls_name = match.group(1)
                return _STRATEGY_LABELS.get(cls_name, cls_name.replace("Strategy", ""))
        return None

    @staticmethod
    def _format_error(exc: Optional[BaseException]) -> str:
        if exc is None:
            return "Keyword failed"
        if isinstance(exc, OpticsError):
            msg = f"[{exc.code.value}] {exc.message}"
            if exc.code == Code.E0101:
                msg += "  — run `launch_app` first to open the session."
            return msg
        return f"{type(exc).__name__}: {exc}"

    # -- Recording / save ---------------------------------------------------------

    def save(self, name: str) -> Tuple[str, str, Optional[str]]:
        """Persist the recorded actions and their accompanying artifacts.

        Writes:

        * ``modules/<name>.csv`` — Title Case keywords + ``param_N`` columns, matching
          :class:`CSVDataReader` exactly.
        * ``test_cases/<name>.csv`` — a single test case referencing the module.
        * ``execution_output/<name>/`` — a snapshot of every artifact the framework
          generated during the session so far (the auto pre-/post-action screenshots
          written by ``@with_self_healing``, AOI captures, annotated detections,
          per-session logs). These otherwise live only in the tempdir and would
          vanish on ``/quit``.

        Artifacts are **copied** (not moved) so further keyword runs continue to
        accumulate into the same tempdir; re-saving captures the up-to-date set.

        Returns ``(modules_path, test_cases_path, artifacts_path or None)``.
        ``artifacts_path`` is ``None`` only when the session has produced no
        artifacts yet, not when the copy itself fails (that raises).
        """
        if not self.recorded:
            raise OpticsError(Code.E0501, message="Nothing recorded to save")
        safe = re.sub(r"[^A-Za-z0-9_ -]", "", name).strip()
        if not safe:
            raise OpticsError(Code.E0501, message=f"Invalid module name: {name!r}")

        modules_dir = os.path.join(self.folder_path, "modules")
        test_cases_dir = os.path.join(self.folder_path, "test_cases")
        os.makedirs(modules_dir, exist_ok=True)
        os.makedirs(test_cases_dir, exist_ok=True)
        modules_path = os.path.join(modules_dir, f"{safe}.csv")
        test_cases_path = os.path.join(test_cases_dir, f"{safe}.csv")

        max_params = max((len(params) for _kw, params in self.recorded), default=0)
        with open(modules_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(
                ["module_name", "module_step"] + [f"param_{i}" for i in range(1, max_params + 1)]
            )
            for func_name, params in self.recorded:
                row = [safe, keyword_to_title(func_name)]
                row.extend(escape_csv_value(p) for p in params)
                row.extend([""] * (max_params - len(params)))
                writer.writerow(row)

        with open(test_cases_path, "w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["test_case", "test_step"])
            writer.writerow([safe, safe])

        artifacts_path: Optional[str] = None
        if os.path.isdir(self._artifacts_tempdir) and os.listdir(self._artifacts_tempdir):
            destination = os.path.join(self.folder_path, "execution_output", safe)
            if os.path.isdir(destination):
                shutil.rmtree(destination)
            shutil.copytree(self._artifacts_tempdir, destination)
            artifacts_path = destination

        self.saved = True
        return modules_path, test_cases_path, artifacts_path

    # -- Devices ------------------------------------------------------------------

    def _enabled_driver_caps(self) -> Optional[Dict[str, Any]]:
        """Capabilities dict of the first enabled driver source, if any."""
        for item in self.config.driver_sources:
            for _name, details in item.items():
                if details.enabled:
                    return details.capabilities
        return None

    def _device_from_config(self) -> Optional[str]:
        caps = self._enabled_driver_caps()
        if not caps:
            return None
        for key in ("udid", "deviceName", "deviceUDID"):
            if caps.get(key):
                return str(caps[key])
        return None

    def active_device(self) -> str:
        """Human-readable name of the active device for the status bar."""
        if self.active_device_serial:
            return self.active_device_serial
        for item in self.config.driver_sources:
            for name, details in item.items():
                if details.enabled:
                    return name
        return "unknown"

    @staticmethod
    def list_devices() -> List[str]:
        """List connected Android device serials via ``adb`` (best effort)."""
        try:
            output = subprocess.run(
                ["adb", "devices"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
        except (FileNotFoundError, subprocess.SubprocessError):
            return []
        devices: List[str] = []
        for line in output.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                devices.append(parts[0])
        return devices

    def switch_device(self, serial: str) -> None:
        """Switch the active device by rebuilding the driver/session with new capabilities."""
        caps = self._enabled_driver_caps()
        if caps is not None:
            caps["udid"] = serial
            caps["deviceName"] = serial
        existing_elements = self.session.elements
        self.manager.terminate_session(self.session_id)
        self.session_id = self.manager.create_session(
            self.config,
            None,
            ModuleData(),
            existing_elements if existing_elements is not None else ElementData(),
            ApiData(),
            self.templates,
        )
        session = self.manager.get_session(self.session_id)
        if session is None:  # pragma: no cover - defensive
            raise OpticsError(Code.E0702, message="Failed to rebuild session for device switch")
        self.session = session
        self._build_registry()
        self.active_device_serial = serial

    # -- Screenshot ---------------------------------------------------------------

    def capture_screenshot(self) -> str:
        """Capture the current device screen to a JPG and return the file path.

        Goes to a persistent ``screenshots/`` folder under the project (not the
        tempdir used for framework auto-artifacts) because ``/screenshot`` is an
        explicit user action — they expect the file to survive ``/quit``.
        """
        if self._action_keyword is None:  # pragma: no cover - defensive
            raise OpticsError(Code.E0303, message="Screenshot capture unavailable")
        image = self._action_keyword.strategy_manager.capture_screenshot()
        output_dir = os.path.join(self.folder_path, "screenshots")
        os.makedirs(output_dir, exist_ok=True)
        name = "live_capture"
        timestamp = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S-%f")
        utils.save_screenshot(image, name, output_dir=output_dir, time_stamp=timestamp)
        sanitized = re.sub(r"[^a-zA-Z0-9\s_]", "", name)
        return os.path.join(output_dir, f"{timestamp}-{sanitized}.jpg")

    # -- Teardown -----------------------------------------------------------------

    def teardown(self) -> None:
        """Run the framework's normal session teardown and remove live artifacts.

        Removes the per-session tempdir holding framework auto-screenshots.
        Anything the user explicitly persisted (``/save`` CSVs and snapshots,
        ``/screenshot`` captures, the session log file) lives elsewhere and is kept.
        """
        try:
            self.manager.terminate_session(self.session_id)
        except Exception as exc:  # pragma: no cover - defensive
            internal_logger.error("Failed to terminate live session: %s", exc)
        shutil.rmtree(self._artifacts_tempdir, ignore_errors=True)
        # Detach the file handler last so any log emitted by terminate_session
        # itself still lands in the session log.
        self._teardown_live_logging()


# Loggers from third-party libraries that talk to the device stack and routinely
# emit WARNINGs we don't want anywhere near the TUI (and which would otherwise
# bubble up to root → lastResort → stderr).
_NOISY_LOGGERS = (
    "selenium",
    "urllib3",
    "appium",
    "asyncio",
    "PIL",
    "easyocr",
    "websockets",
)


@contextmanager
def _silence_console_logging() -> Iterator[None]:
    """Detach console log handlers and mute noisy library loggers.

    This is the *Python-side* silencer. It is necessary but **not sufficient** for
    a corruption-free TUI: C-extension libraries (opencv, PIL, Appium child
    processes) and Python's own ``logging.lastResort`` can still bypass this and
    write directly to fd 2. The fd-level redirect (:func:`_redirect_stderr_fd`)
    is what makes the UI bulletproof; this layer just keeps the redirect log clean.
    """
    from rich.logging import RichHandler  # local import: keep top-level imports light

    def _is_console_handler(handler: logging.Handler) -> bool:
        if isinstance(handler, RichHandler):
            return True
        # FileHandler / RotatingFileHandler are StreamHandler subclasses — keep them.
        return isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler)

    targets = [
        logging.getLogger("optics.internal"),
        logging.getLogger("optics.execution"),
        logging.getLogger(),
    ]
    saved: List[Tuple[logging.Logger, logging.Handler]] = []
    saved_propagate: List[Tuple[logging.Logger, bool]] = []
    for logger in targets:
        saved_propagate.append((logger, logger.propagate))
        for handler in list(logger.handlers):
            if _is_console_handler(handler):
                saved.append((logger, handler))
                logger.removeHandler(handler)
    logging.getLogger("optics.execution").propagate = False
    logging.getLogger("optics.internal").propagate = False

    saved_last_resort = logging.lastResort
    logging.lastResort = None

    # Once the console handlers are gone, ``internal_logger`` may have no handlers
    # at all — Python would then emit a one-time "No handlers could be found"
    # warning to stderr. Attach a NullHandler to absorb records silently.
    null_handlers: List[Tuple[logging.Logger, logging.NullHandler]] = []
    for logger in (logging.getLogger("optics.internal"), logging.getLogger("optics.execution")):
        nh = logging.NullHandler()
        logger.addHandler(nh)
        null_handlers.append((logger, nh))

    saved_levels: List[Tuple[logging.Logger, int]] = []
    for name in _NOISY_LOGGERS:
        lg = logging.getLogger(name)
        saved_levels.append((lg, lg.level))
        lg.setLevel(logging.CRITICAL)

    try:
        yield
    finally:
        for logger, nh in null_handlers:
            logger.removeHandler(nh)
        for logger, handler in saved:
            logger.addHandler(handler)
        for logger, propagate in saved_propagate:
            logger.propagate = propagate
        logging.lastResort = saved_last_resort
        for lg, lvl in saved_levels:
            lg.setLevel(lvl)


@contextmanager
def _redirect_stderr_fd(target_path: str) -> Iterator[None]:
    """Redirect fd 2 (stderr) to a file so nothing can corrupt the full-screen UI.

    Why this is the right hammer: prompt_toolkit owns stdout (the alternate screen
    buffer) and renders incrementally — it assumes the cursor is where it left it.
    Any rogue byte written to the terminal between renders desynchronises that
    model, leaving half-erased entries and a status bar that drifts offscreen.

    Patching ``sys.stderr`` alone isn't enough: C extensions (opencv, PIL), child
    processes started by drivers, and Python's own ``logging.lastResort`` can all
    write straight to fd 2. ``os.dup2`` redirects at the kernel level, catching
    everything. Restored on exit so post-quit output (teardown messages, tracebacks
    from a crashed TUI) lands on the real terminal.
    """
    try:
        real_stderr_fd = sys.stderr.fileno()
    except (AttributeError, io.UnsupportedOperation, OSError):
        # sys.stderr is already a non-fd stream (e.g. test harness, embedded host).
        # Best we can do is the Python-level swap; UI corruption from C extensions
        # is no longer possible because there's nothing to dup, but it's also no
        # longer our concern.
        saved_sys_stderr = sys.stderr
        log_file = open(target_path, "w", buffering=1)
        sys.stderr = log_file
        try:
            yield
        finally:
            sys.stderr = saved_sys_stderr
            log_file.close()
        return
    saved_fd = os.dup(real_stderr_fd)
    log_file = open(target_path, "w", buffering=1)  # line-buffered for tail-ability
    saved_sys_stderr = sys.stderr
    try:
        os.dup2(log_file.fileno(), real_stderr_fd)
        sys.stderr = log_file
        yield
    finally:
        try:
            sys.stderr.flush()
        except Exception:
            pass
        os.dup2(saved_fd, real_stderr_fd)
        os.close(saved_fd)
        sys.stderr = saved_sys_stderr
        log_file.close()


def live_main(folder_path: Optional[str] = None) -> None:
    """Entry point for the ``optics live`` command: open the interactive session.

    The folder is optional. Without one (or with one but no config.yaml), the
    controller uses sensible Android+Appium defaults pointed at the first device
    reported by ``adb`` so you can swipe/tap your way around without any setup.
    """
    from optics_framework.helper.live_tui import LiveTUI  # local import: heavy UI deps

    try:
        controller = LiveController(folder_path)
    except OpticsError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001 - surface setup failures cleanly
        print(f"Error: failed to start live session: {exc}", file=sys.stderr)
        sys.exit(1)

    stderr_log_path = os.path.join(tempfile.gettempdir(), "optics_live_stderr.log")
    live_log_path = controller.live_log_path
    try:
        with _silence_console_logging(), _redirect_stderr_fd(stderr_log_path):
            tui = LiveTUI(controller)
            tui.run()
    finally:
        controller.teardown()
    # Post-quit, the user is back on the real terminal. Surface the per-session
    # log path (always written) and the suppressed-stderr log (only if non-empty)
    # so they can tail either when something needs investigating.
    if live_log_path:
        print(f"Session log: {live_log_path}", file=sys.stderr)
    try:
        if os.path.getsize(stderr_log_path) > 0:
            print(f"Suppressed stderr: {stderr_log_path}", file=sys.stderr)
    except OSError:
        pass
