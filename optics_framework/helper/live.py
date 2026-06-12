"""Interactive live session controller for the ``optics live`` command.

This module holds the non-UI half of the live experience. It keeps a single
framework :class:`~optics_framework.common.session_manager.Session` alive for
the whole session, resolves and executes individual keywords against it (reusing
the same ``KeywordRegistry`` and ``${element}`` resolution the batch runner uses),
records executed actions, and persists them as framework-compatible CSV modules.

The full-screen terminal UI lives in :mod:`optics_framework.helper.live_tui`.
"""

import os
import re
import csv
import sys
import time
import shlex
import shutil
import logging
import tempfile
import subprocess  # nosec B404 - used only for local adb/idevice_id device discovery
import inspect
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
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
from optics_framework.common.nl_agent import (
    NaturalLanguageAgent,
    KeywordSpec,
    ExecResult,
    AgentStep,
    AgentResult,
)
from optics_framework.api import ActionKeyword, AppManagement, FlowControl, Verifier
from optics_framework.helper.execute import discover_templates, identify_file_content


# Canonical optics logger names, referenced in several handler-management helpers.
_INTERNAL_LOGGER_NAME = "optics.internal"
_EXECUTION_LOGGER_NAME = "optics.execution"


# Map locator-strategy class names (logged by ExecutionTracer) to short labels.
_STRATEGY_LABELS: Dict[str, str] = {
    "XPathStrategy": "XPath",
    "TextElementStrategy": "Text",
    "TextDetectionStrategy": "OCR",
    "ImageDetectionStrategy": "Image",
}
_STRATEGY_SUCCESS_RE = re.compile(r"Trying (\w+) on .*? \.\.\. SUCCESS", re.IGNORECASE)

class ActionStatus(str, Enum):
    """Status of a single keyword execution (str-valued, so it compares as a string)."""

    PASS = "PASS"  # nosec B105 - enum member name, not a credential
    FAIL = "FAIL"
    INFO = "INFO"
    RUNNING = "RUNNING"


class NLRunStatus(str, Enum):
    """Terminal status of a natural-language run."""

    PASS = "PASS"  # nosec B105 - enum member name, not a credential
    FAIL = "FAIL"
    ABORTED = "ABORTED"
    MAX_STEPS = "MAX_STEPS"


class NLStepKind(str, Enum):
    """Kind of streamed event from a natural-language run."""

    THINKING = "thinking"
    KEYWORD = "keyword"
    NOTE = "note"


# Map NaturalLanguageAgent terminal statuses to the live-session summary status.
_NL_STATUS_MAP: Dict[str, NLRunStatus] = {
    "done": NLRunStatus.PASS,
    "failed": NLRunStatus.FAIL,
    "aborted": NLRunStatus.ABORTED,
    "exhausted": NLRunStatus.MAX_STEPS,
}


@dataclass
class ActionResult:
    """Outcome of a single keyword execution, ready to render in the history pane."""

    raw: str
    keyword: str = ""
    params: List[str] = field(default_factory=list)
    status: ActionStatus = ActionStatus.PASS
    elapsed: float = 0.0
    strategy: Optional[str] = None
    message: Optional[str] = None
    recorded: bool = False
    # Set by the TUI when this entry belongs to a natural-language run, so the
    # history pane can render it as an indented child / muted thinking line.
    nl_child: bool = False
    nl_thinking: bool = False


@dataclass
class NLStep:
    """A single streamed event from a natural-language run, for the history pane."""

    kind: NLStepKind
    text: str
    result: Optional[ActionResult] = None  # set when kind == NLStepKind.KEYWORD


@dataclass
class NLSummary:
    """Terminal summary of a natural-language run."""

    instruction: str
    status: NLRunStatus
    steps: int = 0
    elapsed: float = 0.0
    message: Optional[str] = None


def keyword_to_title(func_name: str) -> str:
    """Convert a snake_case keyword name to the Title Case form used in CSV modules.

    ``press_element`` -> ``Press Element`` (matches the framework's existing modules).
    """
    return " ".join(word.capitalize() for word in func_name.split("_"))


_CONFIG_KEYS = frozenset({
    "driver_sources", "element_sources", "elements_sources",
    "text_detection", "image_detection", "llm_models",
    "log_level", "json_log", "file_log",
})


def _config_from_yaml(path: str) -> Optional[Config]:
    """Parse a single YAML file into a ``Config`` if it looks like one, else None.

    Surfaces real errors rather than masking them: a malformed conventional
    ``config.yaml``/``config.yml``, or any config-like file (one carrying a recognised
    top-level key) that fails ``Config`` validation, raises :class:`OpticsError` so the
    user sees the actual cause. Files that aren't config-like (e.g. test-data YAML) are
    skipped silently with ``None``.
    """
    is_named_config = os.path.basename(path).lower() in ("config.yaml", "config.yml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except OSError as exc:
        internal_logger.debug("Skipping unreadable YAML %s: %s", path, exc)
        return None
    except yaml.YAMLError as exc:
        if is_named_config:
            raise OpticsError(Code.E0501, message=f"Failed to parse {path}: {exc}") from exc
        internal_logger.debug("Skipping malformed YAML %s: %s", path, exc)
        return None
    if not isinstance(data, dict) or not (set(data.keys()) & _CONFIG_KEYS):
        return None
    if "element_sources" in data and "elements_sources" not in data:
        data["elements_sources"] = data.pop("element_sources")
    try:
        return Config(**data)
    except Exception as exc:  # config-like file but invalid -> surface, don't mask
        raise OpticsError(Code.E0501, message=f"Invalid config in {path}: {exc}") from exc


def _load_partial_config(folder_path: str) -> Optional[Config]:
    """Load the first YAML in ``folder_path`` that looks like an Optics config."""
    for root, _dirs, files in os.walk(folder_path):
        for fname in files:
            if not fname.lower().endswith((".yml", ".yaml")):
                continue
            config = _config_from_yaml(os.path.join(root, fname))
            if config is not None:
                return config
    return None


def _has_enabled(sources: List[Dict[str, DependencyConfig]]) -> bool:
    return any(details.enabled for item in sources for _name, details in item.items())


def _enabled_drivers(config: Config) -> List[str]:
    """Names of every enabled driver source in ``config``."""
    return [
        name
        for item in config.driver_sources
        for name, details in item.items()
        if details.enabled
    ]


def _compose_config(folder_path: Optional[str]) -> Config:
    """Load and validate the project's ``config.yaml`` for a live session.

    ``optics live`` is driver-agnostic but config-driven: the project must declare
    exactly one enabled driver (appium/selenium/playwright/…) plus at least one enabled
    element source. There are no appium auto-defaults — the driver comes entirely from
    the config, so the same flow works for android/web/iOS/TV.
    """
    config = _load_partial_config(folder_path) if folder_path else None
    if config is None:
        raise OpticsError(
            Code.E0501,
            message=(
                "optics live needs a config.yaml with an enabled driver and elements source "
                "in the project folder. See optics_framework/samples/ "
                "(contact=appium, gmail_web=selenium, playwright=playwright)."
            ),
        )
    drivers = _enabled_drivers(config)
    if not drivers:
        raise OpticsError(
            Code.E0501, message="No enabled driver in config.yaml's driver_sources."
        )
    if len(drivers) > 1:
        raise OpticsError(
            Code.E0501,
            message=(
                f"optics live supports exactly one enabled driver; found: {', '.join(drivers)}. "
                "Enable just one driver_source."
            ),
        )
    if not _has_enabled(config.elements_sources):
        raise OpticsError(
            Code.E0501, message="No enabled source in config.yaml's elements_sources."
        )
    return config


class LiveController:
    """Owns the long-lived session and exposes live keyword/recording operations.

    Unlike the batch runner (which builds a session, runs once, then tears it down),
    this controller keeps the session and its driver open across many keyword calls.
    """

    def __init__(self, folder_path: Optional[str] = None):
        # The folder is optional (defaults to cwd), but a config.yaml IS required:
        # the driver (appium/selenium/playwright/…) comes entirely from the config,
        # which is what makes optics live driver-agnostic.
        if folder_path:
            self.folder_path = os.path.abspath(folder_path)
            if not os.path.isdir(self.folder_path):
                raise OpticsError(Code.E0501, message=f"Invalid project folder: {self.folder_path}")
        else:
            self.folder_path = os.getcwd()

        config = _compose_config(self.folder_path)
        config.project_path = self.folder_path
        # One timestamp per session, shared by the log file and the screenshots dir so
        # they correlate (logs/optics_live_<stamp>.log ↔ screenshots/session_<stamp>/).
        self._session_stamp: str = datetime.now().astimezone().strftime("%Y-%m-%dT%H-%M-%S")
        # Route framework-generated artifacts (the auto pre-/post-action screenshots
        # written by @with_self_healing, AOI captures, etc.) to a persistent per-session
        # folder under the project's screenshots/ so they survive /quit — every keyword
        # (typed or NL-driven) leaves a visual record without needing /save.
        self._artifacts_dir: str = os.path.join(
            self.folder_path, "screenshots", f"session_{self._session_stamp}"
        )
        os.makedirs(self._artifacts_dir, exist_ok=True)
        config.execution_output_path = self._artifacts_dir
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
        # The single enabled driver name (e.g. "appium", "selenium", "playwright"),
        # used to drive UI labels and gate device-discovery features.
        self.driver_type: str = self._enabled_driver_name()
        self.active_target_label: Optional[str] = self._get_target_id_from_config()

        # Natural-language mode (lazy): the agent is built on first use and the
        # availability flag is computed from config (an enabled llm_models entry).
        self._nl_agent: Optional[NaturalLanguageAgent] = None
        self._nl_available: Optional[bool] = None

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
        log_path = os.path.join(log_dir, f"optics_live_{self._session_stamp}.log")
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)  # let the loggers' own levels gate volume
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)s | %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        for logger in (
            logging.getLogger(_INTERNAL_LOGGER_NAME),
            logging.getLogger(_EXECUTION_LOGGER_NAME),
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
            logging.getLogger(_INTERNAL_LOGGER_NAME),
            logging.getLogger(_EXECUTION_LOGGER_NAME),
        ):
            try:
                logger.removeHandler(self._live_log_handler)
            except ValueError:
                pass
        try:
            self._live_log_handler.close()
        except Exception:  # nosec B110 # pragma: no cover - defensive cleanup
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

    def _iter_element_files(self) -> Iterator[Tuple[str, str]]:
        """Yield ``(path, lowercased_name)`` for every CSV/YAML under the project."""
        for root, _dirs, files in os.walk(self.folder_path):
            for fname in files:
                lname = fname.lower()
                if lname.endswith((".csv", ".yml", ".yaml")):
                    yield os.path.join(root, fname), lname

    @staticmethod
    def _merge_elements(reader: DataReader, path: str, elements: ElementData) -> None:
        """Merge the elements parsed from ``path`` into ``elements`` (dedup per name)."""
        for name, values in reader.read_elements(path).items():
            for value in values:
                existing = elements.get_element(name) or []
                if value not in existing:
                    elements.add_element(name, value)

    def ensure_elements_loaded(self) -> None:
        """Load named elements from the project on first use (not eagerly at startup)."""
        if self._elements_loaded:
            return
        csv_reader = CSVDataReader()
        yaml_reader = YAMLDataReader()
        elements = self.session.elements if self.session.elements is not None else ElementData()
        for path, lname in self._iter_element_files():
            try:
                if "elements" not in identify_file_content(path):
                    continue
                reader = csv_reader if lname.endswith(".csv") else yaml_reader
                self._merge_elements(reader, path, elements)
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
        """Execute one keyword call and record it. Blocking: callers run this off the UI thread.

        Mirrors the batch runner's parameter handling: bare ``${element}`` positional
        arguments expand into fallback candidates tried in order, ``key=value`` tokens
        become keyword arguments, and the winning locator strategy is read back from the
        execution tracer's logs.
        """
        return self._execute_line(raw, record=True)

    def _execute_line(self, raw: str, *, record: bool) -> ActionResult:
        """Execute one keyword call. ``record`` gates the recording side-effect only.

        The manual flow (:meth:`run_keyword`) records every success so the session is
        ``/save``-able. The natural-language agent runs with ``record=False`` and buffers
        its own steps, committing only when the whole instruction succeeds — this keeps
        wandering/exploratory keywords out of the saved module.
        """
        raw = raw.strip()
        start = time.time()
        try:
            tokens = shlex.split(raw, posix=True)
        except ValueError as exc:
            return ActionResult(raw=raw, status=ActionStatus.FAIL, message=f"Parse error: {exc}")
        if not tokens:
            return ActionResult(raw=raw, status=ActionStatus.FAIL, message="Empty command")

        keyword_token, params = tokens[0], tokens[1:]
        func_name = "_".join(keyword_token.split()).lower()
        method = self.keyword_map.get(func_name)
        if method is None:
            return ActionResult(
                raw=raw,
                keyword=keyword_token,
                status=ActionStatus.FAIL,
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
                status=ActionStatus.FAIL,
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
        internal_logger_obj = logging.getLogger(_INTERNAL_LOGGER_NAME)
        internal_logger_obj.addHandler(internal_capture)

        try:
            return self._attempt_combos(
                method, param_candidates, internal_capture, strategy_capture,
                raw, func_name, params, start, record=record,
            )
        finally:
            execution_logger.removeHandler(strategy_capture)
            execution_logger.setLevel(prev_level)
            internal_logger_obj.removeHandler(internal_capture)

    @staticmethod
    def _is_fallback_error(exc: OpticsError) -> bool:
        """True for element-location codes (E02xx / X0201) — retry the next locator.

        Uses ``.value`` because ``str(Code.X)`` renders as ``"Code.X"`` on the
        str-Enum under Python 3.12.
        """
        return exc.code.value.startswith("E02") or exc.code == Code.X0201

    def _run_single_combo(
        self, method: Callable[..., Any], combo: Tuple[str, ...],
        internal_capture: LogCaptureBuffer,
    ) -> Optional[BaseException]:
        """Run one parameter combination. Returns the failing exception, or None on success.

        Many framework methods (appium swipe with a bad direction, force_terminate
        failures, etc.) report problems via ``internal_logger.error(...)`` and then
        RETURN — they never raise. We watch the captured records so those "silent"
        failures surface as an :class:`OpticsError` instead of a false pass.
        """
        internal_capture.clear()
        try:
            positional, keywords = self._resolve_candidate(combo)
            method(*positional, **keywords)
        except OpticsError as exc:
            return exc
        # Surfaced to the user, never crashes the controller.
        except Exception as exc:  # noqa: BLE001
            return exc
        silent_error = self._find_error_log(internal_capture)
        if silent_error is not None:
            return OpticsError(Code.E0401, message=silent_error)
        return None

    def _attempt_combos(
        self, method: Callable[..., Any], param_candidates: List[List[str]],
        internal_capture: LogCaptureBuffer, strategy_capture: LogCaptureBuffer,
        raw: str, func_name: str, params: List[str], start: float, *, record: bool,
    ) -> ActionResult:
        """Try each fallback combination in order; record and return the first success.

        ``record`` gates only the ``self.recorded`` side-effect; the actual keyword
        execution and ``${element}`` fallback are identical regardless.
        """
        last_exc: Optional[BaseException] = None
        for combo in product(*param_candidates):
            outcome = self._run_single_combo(method, combo, internal_capture)
            if outcome is not None:
                last_exc = outcome
                if isinstance(outcome, OpticsError) and self._is_fallback_error(outcome):
                    continue
                break
            if record:
                self.recorded.append((func_name, params))
                self.saved = False
            return ActionResult(
                raw=raw,
                keyword=func_name,
                params=params,
                status=ActionStatus.PASS,
                elapsed=time.time() - start,
                strategy=self._winning_strategy(strategy_capture),
                recorded=record,
            )
        return ActionResult(
            raw=raw,
            keyword=func_name,
            params=params,
            status=ActionStatus.FAIL,
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
                    raise OpticsError(
                        Code.E0901,
                        message=f"Named element '{var_name}' is not defined in this project's elements.",
                    )
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
            raise OpticsError(
                Code.E0901,
                message=f"Named element '{var_name}' is not defined in this project's elements.",
            )
        return resolved

    @staticmethod
    def _find_error_log(log_capture: LogCaptureBuffer) -> Optional[str]:
        """Return the message of the first ERROR+ record in ``log_capture``, or None.

        Used to detect cases where the framework reports a failure via logging
        instead of raising (the appium driver's "Unknown swipe direction" path
        is the canonical example). A returned value means the keyword "succeeded"
        in the Python sense but didn't actually do what was asked.
        """
        for record in log_capture.records:
            if isinstance(record, logging.LogRecord) and record.levelno >= logging.ERROR:
                try:
                    return record.getMessage()
                except Exception:  # pragma: no cover - defensive
                    return str(record)
        return None

    @staticmethod
    def _winning_strategy(log_capture: LogCaptureBuffer) -> Optional[str]:
        """Read the last successful locator strategy from captured execution logs."""
        for record in reversed(log_capture.records):
            try:
                message = record.getMessage() if isinstance(record, logging.LogRecord) else str(record)
            except Exception:  # nosec B112 # pragma: no cover - defensive
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
        if os.path.isdir(self._artifacts_dir) and os.listdir(self._artifacts_dir):
            destination = os.path.join(self.folder_path, "execution_output", safe)
            if os.path.isdir(destination):
                shutil.rmtree(destination)
            shutil.copytree(self._artifacts_dir, destination)
            artifacts_path = destination

        self.saved = True
        return modules_path, test_cases_path, artifacts_path

    # -- Devices ------------------------------------------------------------------

    def _enabled_driver_caps(self) -> Optional[Dict[str, Any]]:
        """Capabilities dict of the (single) enabled driver source, if any."""
        for item in self.config.driver_sources:
            for _name, details in item.items():
                if details.enabled:
                    return details.capabilities
        return None

    def _enabled_driver_name(self) -> str:
        """Name of the enabled driver source (e.g. ``appium``/``selenium``/``playwright``).

        ``_compose_config`` guarantees exactly one is enabled; ``unknown`` is a defensive
        fallback that should not occur in practice.
        """
        drivers = _enabled_drivers(self.config)
        return drivers[0] if drivers else "unknown"

    def _get_target_id_from_config(self) -> Optional[str]:
        """A driver-appropriate target identifier from the enabled driver's capabilities.

        appium -> device udid/name; selenium -> browser name/URL; playwright -> browser
        engine; ble -> serial port / device id. Returns ``None`` when nothing identifying
        is configured.
        """
        caps = self._enabled_driver_caps() or {}
        if self.driver_type == "appium":
            for key in ("udid", "deviceName", "deviceUDID"):
                if caps.get(key):
                    return str(caps[key])
        elif self.driver_type == "selenium":
            return caps.get("browserName") or caps.get("browserURL")
        elif self.driver_type == "playwright":
            return caps.get("browser")
        elif self.driver_type == "ble":
            return caps.get("port") or caps.get("device_id")
        return None

    def active_target(self) -> str:
        """Human-readable label of the live target for the status bar.

        e.g. ``appium:emulator-5554``, ``playwright:chromium``, ``selenium:chrome``.
        """
        label = self.active_target_label
        if label:
            return f"{self.driver_type}:{label}"
        return self.driver_type or "unknown"

    def supports_device_switching(self) -> bool:
        """True for Appium sessions (Android or iOS) — devices are targeted by ``udid``.

        The ``/device`` picker lists all connected Android (``adb``) and iOS
        (``idevice_id``) devices, and switching rebuilds the session with the chosen
        ``udid``. Non-Appium drivers (selenium/playwright) have no device concept.
        """
        return self.driver_type == "appium"

    def supports_adb_hotplug(self) -> bool:
        """True only for Android Appium — gates the ``adb`` hot-plug auto-init monitor.

        Hot-plug auto-init polls ``adb`` and only makes sense for Android; iOS devices
        are still listed/switchable via :meth:`list_devices` and ``/device``.
        """
        if self.driver_type != "appium":
            return False
        caps = self._enabled_driver_caps() or {}
        return str(caps.get("platformName", "")).lower() == "android"

    @staticmethod
    def list_android_devices() -> List[str]:
        """Connected Android device serials via ``adb devices`` (best effort, never raises)."""
        try:
            output = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, tool from PATH
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

    @staticmethod
    def list_ios_devices() -> List[str]:
        """Connected iOS device UDIDs via ``idevice_id -l`` (libimobiledevice).

        Best effort: returns ``[]`` when the tool isn't installed or no device is
        attached. ``idevice_id -l`` prints one UDID per line; we take the first token
        of each line so an optional ``(USB)``/``(Network)`` suffix is tolerated.
        """
        try:
            output = subprocess.run(  # nosec B603 B607 - fixed argv, no shell, tool from PATH
                ["idevice_id", "-l"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            ).stdout
        except (FileNotFoundError, subprocess.SubprocessError):
            return []
        devices: List[str] = []
        for line in output.splitlines():
            tokens = line.split()
            if tokens:
                devices.append(tokens[0])
        return devices

    @staticmethod
    def list_devices() -> List[Tuple[str, str]]:
        """All connected mobile devices as ``(udid, platform)`` pairs.

        Combines Android (``adb``) and iOS (``idevice_id``); ``platform`` is
        ``"android"`` or ``"ios"``. Best effort — a missing tool contributes nothing.
        """
        devices: List[Tuple[str, str]] = [
            (serial, "android") for serial in LiveController.list_android_devices()
        ]
        devices.extend((udid, "ios") for udid in LiveController.list_ios_devices())
        return devices

    def switch_device(self, udid: str) -> None:
        """Switch the active device by rebuilding the session with the chosen ``udid``.

        Appium only (Android or iOS); web drivers (selenium/playwright) have no device
        concept. The picked device must match the session's configured platform — picking
        a cross-platform device surfaces a clear session-rebuild failure rather than a crash.
        """
        if not self.supports_device_switching():
            raise OpticsError(
                Code.E0501,
                message=(
                    f"Device switching is available for Appium sessions only; "
                    f"this session uses {self.driver_type}."
                ),
            )
        caps = self._enabled_driver_caps()
        if caps is not None:
            caps["udid"] = udid
            caps["deviceName"] = udid
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
        self.active_target_label = udid

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

    def screenshot_png_bytes(self) -> bytes:
        """Capture the current screen as raw PNG bytes (for the LLM); no file side-effect."""
        if self._action_keyword is None:  # pragma: no cover - defensive
            raise OpticsError(Code.E0303, message="Screenshot capture unavailable")
        image = self._action_keyword.strategy_manager.capture_screenshot()
        try:
            return utils.encode_numpy_to_png_bytes(image)
        except ValueError as exc:  # pragma: no cover - defensive
            raise OpticsError(Code.E0303, message="Failed to encode screenshot") from exc

    # -- Natural-language mode ----------------------------------------------------

    def natural_language_available(self) -> bool:
        """True if an LLM engine is enabled in config (an ``llm_models`` entry).

        Instantiation errors (missing ``[llm]`` extra, bad credentials) are not probed
        here — they surface with an actionable message in :meth:`run_natural_language`.
        """
        if self._nl_available is None:
            self._nl_available = _has_enabled(self.session.config.llm_models)
        return self._nl_available

    def _nl_catalog(self) -> List[KeywordSpec]:
        return [
            KeywordSpec(name=name, signature=self.keyword_signature(name) or name)
            for name in self.keyword_names()
        ]

    def page_source(self) -> Optional[str]:
        """Condensed UI hierarchy of the current screen (stripped page source).

        Best-effort: returns ``None`` when the active element sources don't expose a
        page source (e.g. a screenshot-only or unsupported driver). Fed to the LLM
        alongside the screenshot so it can pick exact texts/ids and read element bounds.
        """
        if self._action_keyword is None:  # pragma: no cover - defensive
            return None
        try:
            result = self._action_keyword.strategy_manager.capture_pagesource()
        except OpticsError as exc:
            internal_logger.debug("Page source unavailable: %s", exc)
            return None
        if not result:
            return None
        xml_source = result[0]
        return utils.strip_page_source(xml_source) or None

    def _nl_execute(self, raw: str) -> ExecResult:
        """Agent executor: run one keyword WITHOUT recording, mapped to an ExecResult."""
        result = self._execute_line(raw, record=False)
        return ExecResult(
            ok=(result.status == ActionStatus.PASS),
            strategy=result.strategy,
            message=result.message,
            elapsed=result.elapsed,
        )

    def _get_nl_agent(self) -> NaturalLanguageAgent:
        """Build (and cache) the NL agent. Raises OpticsError if no LLM is usable."""
        if self._nl_agent is not None:
            return self._nl_agent
        llm = self.session.optics.get_llm()  # may raise E0601 if the [llm] extra is missing
        if llm is None or not getattr(llm, "instances", None):
            raise OpticsError(
                Code.E0501,
                message="No LLM engine enabled. Enable a 'gemini' entry under llm_models in config.yaml.",
            )
        self._nl_agent = NaturalLanguageAgent(
            llm=llm,
            screenshot_provider=self.screenshot_png_bytes,
            keyword_executor=self._nl_execute,
            keyword_catalog=self._nl_catalog,
            element_names=self.element_names,
            pagesource_provider=self.page_source,
        )
        return self._nl_agent

    @staticmethod
    def _nl_action_result(step: AgentStep) -> ActionResult:
        """Build the history ``ActionResult`` for an executed-keyword agent step."""
        ex = step.exec_result
        ok = bool(ex and ex.ok)
        if ok:
            message = None
        elif ex:
            message = ex.message
        else:
            message = step.observation
        return ActionResult(
            raw=NaturalLanguageAgent._build_line(step.keyword, step.params),
            keyword=step.keyword,
            params=step.params,
            status=ActionStatus.PASS if ok else ActionStatus.FAIL,
            elapsed=ex.elapsed if ex else 0.0,
            strategy=ex.strategy if ex else None,
            message=message,
        )

    def _emit_nl_step(self, step: AgentStep, on_step: Callable[[NLStep], None]) -> None:
        """Translate one agent step into a UI ``NLStep`` via the ``on_step`` callback."""
        if step.observation is None:
            # Decision phase: surface the model's thought before it acts.
            if step.thought:
                on_step(NLStep(kind=NLStepKind.THINKING, text=step.thought))
            return
        if step.action == "keyword":
            action_result = self._nl_action_result(step)
            on_step(NLStep(kind=NLStepKind.KEYWORD, text=action_result.raw, result=action_result))

    def run_natural_language(
        self,
        instruction: str,
        on_step: Callable[[NLStep], None],
        should_abort: Optional[Callable[[], bool]] = None,
    ) -> NLSummary:
        """Translate a natural-language instruction into keyword calls (ReAct loop).

        Blocking — callers run this off the UI thread. ``on_step`` is invoked per agent
        step (thinking lines + executed keyword results). Recording is commit-on-done: the
        buffered successful steps are appended to ``self.recorded`` only when the whole
        instruction succeeds, so an incomplete run never pollutes ``/save``.
        """
        start = time.time()
        instruction = instruction.strip()
        if not instruction:
            return NLSummary(instruction, NLRunStatus.FAIL, 0, 0.0, "Empty instruction")

        try:
            agent = self._get_nl_agent()
        except OpticsError as exc:
            return NLSummary(instruction, NLRunStatus.FAIL, 0, time.time() - start, self._format_error(exc))

        try:
            result: AgentResult = agent.run(
                instruction,
                on_step=lambda step: self._emit_nl_step(step, on_step),
                should_abort=should_abort,
            )
        except Exception as exc:  # noqa: BLE001 - never crash the controller
            return NLSummary(
                instruction, NLRunStatus.FAIL, 0, time.time() - start, f"{type(exc).__name__}: {exc}"
            )

        # Commit-on-done: only a fully successful run is added to the recording.
        if result.status == "done" and result.successful_steps:
            self.recorded.extend(result.successful_steps)
            self.saved = False

        return NLSummary(
            instruction=instruction,
            status=_NL_STATUS_MAP.get(result.status, NLRunStatus.FAIL),
            steps=len(result.successful_steps),
            elapsed=time.time() - start,
            message=result.message,
        )

    # -- Teardown -----------------------------------------------------------------

    def teardown(self) -> None:
        """Run the framework's normal session teardown.

        The per-session screenshots dir (``screenshots/session_<stamp>/``) is **kept** so
        the visual record of every keyword survives ``/quit``; it is only removed when
        empty (no screenshots were captured this session) to avoid leaving stray folders.
        """
        try:
            self.manager.terminate_session(self.session_id)
        except Exception as exc:  # pragma: no cover - defensive
            internal_logger.error("Failed to terminate live session: %s", exc)
        try:
            if os.path.isdir(self._artifacts_dir) and not os.listdir(self._artifacts_dir):
                os.rmdir(self._artifacts_dir)
        except OSError:  # pragma: no cover - defensive
            pass
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
        logging.getLogger(_INTERNAL_LOGGER_NAME),
        logging.getLogger(_EXECUTION_LOGGER_NAME),
        logging.getLogger(),
    ]
    saved: List[Tuple[logging.Logger, logging.Handler]] = []
    saved_propagate: List[Tuple[logging.Logger, bool]] = []
    for logger in targets:
        saved_propagate.append((logger, logger.propagate))
        # Collect first, then detach: removeHandler mutates logger.handlers, so
        # iterating it directly would skip entries.
        console_handlers = [h for h in logger.handlers if _is_console_handler(h)]
        for handler in console_handlers:
            saved.append((logger, handler))
            logger.removeHandler(handler)
    logging.getLogger(_EXECUTION_LOGGER_NAME).propagate = False
    logging.getLogger(_INTERNAL_LOGGER_NAME).propagate = False

    saved_last_resort = logging.lastResort
    logging.lastResort = None

    # Once the console handlers are gone, ``internal_logger`` may have no handlers
    # at all — Python would then emit a one-time "No handlers could be found"
    # warning to stderr. Attach a NullHandler to absorb records silently.
    null_handlers: List[Tuple[logging.Logger, logging.NullHandler]] = []
    for logger in (logging.getLogger(_INTERNAL_LOGGER_NAME), logging.getLogger(_EXECUTION_LOGGER_NAME)):
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
    except (AttributeError, OSError):
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
        except Exception:  # nosec B110 - best-effort flush during stderr restore
            pass
        os.dup2(saved_fd, real_stderr_fd)
        os.close(saved_fd)
        sys.stderr = saved_sys_stderr
        log_file.close()


def live_main(folder_path: Optional[str] = None) -> None:
    """Entry point for the ``optics live`` command: open the interactive session.

    The folder defaults to the current directory, but a ``config.yaml`` is required:
    it must declare exactly one enabled driver (appium/selenium/playwright/…) and at
    least one enabled element source. The driver comes entirely from the config, so the
    same live flow works for android, web, iOS, TV, etc. A missing or invalid config
    fails with a clear message (no auto-defaults).
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
