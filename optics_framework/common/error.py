"""Error codes, their plain-language meaning and the error type every Optics failure raises."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Dict, Optional, Any
from pydantic import BaseModel
from rich import get_console
from rich.panel import Panel
from rich.text import Text
from optics_framework.common.logging_config import internal_logger


class Category(StrEnum):
    DRIVER = "driver"
    ELEMENT = "element"
    SCREENSHOT = "screenshot"
    KEYWORD = "keyword"
    CONFIG = "config"
    MODULE = "module"
    TEST = "test"
    LIVE = "live"
    GENERAL = "general"


class Code(StrEnum):
    # Driver issue
    E0101 = "E0101"
    E0102 = "E0102"
    E0103 = "E0103"
    E0104 = "E0104"
    E0105 = "E0105"
    E0106 = "E0106"
    W0104 = "W0104"

    # Element location issue
    E0201 = "E0201"
    X0201 = "X0201"
    E0202 = "E0202"
    W0203 = "W0203"
    E0204 = "E0204"
    E0205 = "E0205"
    E0206 = "E0206"

    # Screenshot issues
    E0301 = "E0301"
    W0302 = "W0302"
    E0303 = "E0303"
    E0304 = "E0304"
    E0305 = "E0305"

    # Keyword Execution issues
    E0401 = "E0401"
    X0401 = "X0401"
    E0402 = "E0402"
    E0403 = "E0403"
    W0404 = "W0404"
    E0405 = "E0405"
    E0406 = "E0406"
    E0407 = "E0407"
    E0408 = "E0408"
    E0409 = "E0409"

    # Config/File issues
    E0501 = "E0501"
    X0502 = "X0502"
    E0503 = "E0503"
    W0503 = "W0503"

    # Module/Factory Issues
    E0601 = "E0601"
    W0602 = "W0602"
    E0603 = "E0603"
    X0604 = "X0604"

    # Test Case Issues
    E0701 = "E0701"
    E0702 = "E0702"
    E0703 = "E0703"
    E0704 = "E0704"
    W0705 = "W0705"
    E0706 = "E0706"

    # General Issues
    E0801 = "E0801"
    E0802 = "E0802"

    # Live (interactive session) issues
    E0901 = "E0901"


class ErrorSpec(BaseModel):
    """A known code: its default message, category, HTTP status and the next step a person can take."""

    code: Code
    default_message: str
    category: Category
    default_status: int = 500
    hint: Optional[str] = None
    meta: Optional[Dict[str, Any]] = None

    model_config = {
        "frozen": True,
    }


_NOT_FOUND_HINT = "Make sure that the screen shows the element when this step starts, or correct its locator."

_SPECS = (
    (Code.E0101, Category.DRIVER, 500, "The driver is not ready",
     "Start a new session. If this error occurs again, examine the driver entry in config.yaml."),
    (Code.E0102, Category.DRIVER, 500, "Could not start the session",
     "Make sure that the device and its driver server are on and that you can reach them. "
     "Run 'optics doctor' to examine the setup."),
    (Code.E0103, Category.DRIVER, 500, "Could not end the session", None),
    (Code.E0104, Category.DRIVER, 400, "The driver configuration is incomplete",
     "Add the missing capabilities to the driver entry in config.yaml."),
    (Code.E0105, Category.DRIVER, 400, "This keyword does not work on this platform",
     "Use a different keyword on this platform."),
    (Code.E0106, Category.DRIVER, 500, "The driver session ended",
     "The device or the driver server stopped the session. Examine the device, then run again."),
    (Code.W0104, Category.DRIVER, 200, "The driver configuration is incomplete", None),
    (Code.E0201, Category.ELEMENT, 404, "The element is not on the screen", _NOT_FOUND_HINT),
    (Code.X0201, Category.ELEMENT, 500, "The element was not found with any of its locators", _NOT_FOUND_HINT),
    (Code.E0202, Category.ELEMENT, 400, "The element source cannot do this",
     "Add an element source to config.yaml that can read this screen."),
    (Code.W0203, Category.ELEMENT, 200, "The element has an unexpected format", None),
    (Code.E0204, Category.ELEMENT, 504, "Timed out while looking for the element", _NOT_FOUND_HINT),
    (Code.E0205, Category.ELEMENT, 400, "The element locator is not valid",
     "Use text, an XPath, css=, id: or the file name of an image."),
    (Code.E0206, Category.ELEMENT, 409, "Unknown popup on screen",
     "Add a rule for this popup to popup_rules.json, or close it before this step."),
    (Code.E0301, Category.SCREENSHOT, 400, "Screenshots are off", None),
    (Code.W0302, Category.SCREENSHOT, 200, "Using the next camera driver", None),
    (Code.E0303, Category.SCREENSHOT, 500, "The screenshot is empty or black",
     "Make sure that the device screen is on and unlocked. Some apps block screenshots of secure screens."),
    (Code.E0304, Category.SCREENSHOT, 422, "The image is too large or is not a valid image",
     "Use a PNG or JPEG image."),
    (Code.E0305, Category.SCREENSHOT, 503, "The camera does not see the device screen",
     "Point the camera at the device screen, then run again."),
    (Code.E0401, Category.KEYWORD, 500, "The action failed", None),
    (Code.X0401, Category.KEYWORD, 500, "The action failed", None),
    (Code.E0402, Category.KEYWORD, 404, "Keyword not found",
     "Run 'optics list' to see the keywords."),
    (Code.E0403, Category.KEYWORD, 400, "The keyword parameters are not valid",
     "Run 'optics list <keyword>' to see the parameters of the keyword."),
    (Code.W0404, Category.KEYWORD, 200, "This keyword is deprecated", None),
    (Code.E0405, Category.KEYWORD, 500, "Action ran, but the screen does not show its result",
     "Look at the failure screenshot to see what the screen showed after the action."),
    (Code.E0406, Category.KEYWORD, 400, "Expression or filter refused: unsafe or too costly",
     "Make the expression shorter or simpler."),
    (Code.E0407, Category.KEYWORD, 409, "Popup keeps coming back", None),
    (Code.E0408, Category.KEYWORD, 409, "Popup stopped the step", None),
    (Code.E0409, Category.KEYWORD, 409, "Popup dismissed, the step looks for its element again", None),
    (Code.E0501, Category.CONFIG, 400, "A required setting or file is missing",
     "Correct config.yaml, or add the missing file."),
    (Code.X0502, Category.CONFIG, 500, "Could not read a file", None),
    (Code.E0503, Category.CONFIG, 400, "A setting has a value that is not valid",
     "Correct the value in config.yaml or in the test files."),
    (Code.W0503, Category.CONFIG, 200, "A setting has a value that is not valid; it is ignored", None),
    (Code.E0601, Category.MODULE, 404, "Module not found",
     "Add the module to the modules file, or correct its name."),
    (Code.W0602, Category.MODULE, 200, "No valid instance; it is ignored", None),
    (Code.E0603, Category.MODULE, 500, "Could not create an engine", None),
    (Code.X0604, Category.MODULE, 500, "Could not import a module", None),
    (Code.E0701, Category.TEST, 500, "The run failed", None),
    (Code.E0702, Category.TEST, 404, "Test case not found", None),
    (Code.E0703, Category.TEST, 400, "Could not resolve a parameter", None),
    (Code.E0704, Category.TEST, 504, "The test case timed out", None),
    (Code.W0705, Category.TEST, 200, "The test case was skipped", None),
    (Code.E0706, Category.TEST, 500, "Run stopped before the test case finished", None),
    (Code.E0801, Category.GENERAL, 500, "Unexpected error", None),
    (Code.E0802, Category.GENERAL, 500, "Unhandled error", None),
    (Code.E0901, Category.LIVE, 404, "Named element not defined in this project",
     "Add the element to the elements file of the project."),
)

ERROR_REGISTRY: Dict[str, ErrorSpec] = {
    code: ErrorSpec(code=code, category=category, default_status=status, default_message=message, hint=hint)
    for code, category, status, message, hint in _SPECS
}


class ErrorPayload(BaseModel):
    """Pydantic model for serialized error payloads."""

    type: str
    code: str
    status: Optional[int] = None
    message: str
    hint: Optional[str] = None
    details: Optional[Any] = None
    meta: Optional[Dict[str, Any]] = None
    cause: Optional[str] = None


_CAUSE_MAX_CHARS = 300


def is_element_not_found(code: "str | Code") -> bool:
    """True for the codes meaning the element was not found (E02xx, X0201), which another locator or value may fix."""
    value = getattr(code, "value", code)
    return value.startswith("E02") or value == Code.X0201.value


# XCUITest reports Apple NSErrors whose readable part is NSLocalizedDescription.
_NS_DESCRIPTION = re.compile(r"NSLocalizedDescription=(.+?)(?:, [A-Za-z]+=|\}|$)")


def _first_line(error: BaseException) -> str:
    # Driver errors append stack traces and call logs after the first line.
    text = next((line.strip() for line in str(error).splitlines() if line.strip()), "")
    if apple := _NS_DESCRIPTION.search(text):
        return apple.group(1)[:_CAUSE_MAX_CHARS]
    return text.removeprefix("Message: ")[:_CAUSE_MAX_CHARS]


def describe_cause(error: BaseException) -> str:
    """One line naming an exception: an OpticsError's own text, else its type and message."""
    if isinstance(error, OpticsError):
        return _first_line(error)
    line = _first_line(error)
    return f"{type(error).__name__}: {line}" if line else type(error).__name__


def plain(error: BaseException) -> str:
    """An error as a person reads it: an OpticsError's summary, else the first line of its message."""
    if isinstance(error, OpticsError):
        return error.summary
    return _first_line(error) or type(error).__name__


def _chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and all(current is not seen for seen in chain):
        chain.append(current)
        current = current.__cause__
    return chain


@dataclass(frozen=True)
class Explanation:
    """What a person needs to know about a failure: what happened, what to do next and, for the report, why."""

    summary: str
    hint: str | None
    code: str | None
    detail: tuple[str, ...]


def explain(error: BaseException) -> Explanation:
    """The summary, the next step and the technical chain of ``error``, in one place for every surface."""
    chain = _chain(error)
    optics = [e for e in chain if isinstance(e, OpticsError)]
    hints = list(dict.fromkeys(e.own_hint for e in reversed(optics) if e.own_hint))
    general = next((h for h in (ERROR_REGISTRY[e.code].hint for e in reversed(optics) if e.code in ERROR_REGISTRY) if h), None)
    if general and not hints:
        hints.append(general)
    return Explanation(
        summary=plain(error),
        hint=" ".join(hints) or None,
        code=str(optics[0].code) if optics else None,
        detail=_detail(chain),
    )


def _detail(chain: list[BaseException]) -> tuple[str, ...]:
    """One line per layer of the chain that says something the layer above it did not."""
    lines: list[str] = []
    above = ""
    for error in chain:
        text = error.message if isinstance(error, OpticsError) else describe_cause(error)
        if text and text not in above:
            lines.append(f"{error.code}: {text}" if isinstance(error, OpticsError) else text)
        above = text
    return tuple(lines)


class OpticsError(Exception):
    """An Optics failure: a code, one plain sentence for this layer, an optional next step and its cause.

    A layer that wraps another error names only what it adds; ``summary`` joins the layers, so a
    wrapper never repeats the code or the message of the error it wraps.
    """

    show_cause = True

    def __init__(
        self,
        code: str | Code,
        message: Optional[str] = None,
        details: Optional[Any] = None,
        cause: Optional[BaseException] = None,
        meta: Optional[Dict[str, Any]] = None,
        hint: Optional[str] = None,
    ):
        spec = ERROR_REGISTRY.get(code)
        if spec is None:
            spec = ErrorSpec(
                code=Code(code) if isinstance(code, str) else code,
                default_message=message or "Unknown error",
                category=Category.GENERAL,
                default_status=500,
                meta=meta,
            )

        self.code = spec.code
        self.category = spec.category
        self.status_code = int(spec.default_status)
        self.message = message or spec.default_message
        self.own_hint = hint
        self.details = details
        self.__cause__ = cause
        self.meta = meta or getattr(spec, "meta", None)
        super().__init__(f"{self.code}: {self.message}")

    @property
    def summary(self) -> str:
        """This error and the causes that add to it, as one or more plain sentences without codes."""
        cause = self.__cause__
        if cause is None or not self.show_cause:
            return self.message
        inner = plain(cause)
        if not inner or inner in self.message:
            return self.message
        if self.message in inner:
            return inner
        return f"{self.message}: {inner}"

    @property
    def hint(self) -> str | None:
        return explain(self).hint

    @property
    def cause_text(self) -> str | None:
        """The root of the cause chain as one line, unless the summary already says it."""
        if self.__cause__ is None or not self.show_cause:
            return None
        root = _chain(self)[-1]
        if plain(root) in self.summary:
            return None
        return describe_cause(root)

    def __str__(self) -> str:
        return f"{self.code}: {self.summary}"

    def log(self, logger=None, level=None, extra=None, use_rich: bool | None = None):
        """
        Print the error as a rich panel, or log it when ``logger``, ``level`` or ``extra`` is given.
        Args:
            logger: logging.Logger instance or compatible. Defaults to internal_logger from logging_config.
            level: log level (default: ERROR for E/X, WARNING for W).
            extra: dict of extra metadata for structured logging.
            use_rich: force the rich panel (True) or the logger (False).
        """
        if use_rich is None:
            use_rich = logger is None and level is None and extra is None
        code_prefix = str(self.code)[0] if self.code else "E"
        resolved_level = self._resolve_log_level(level, code_prefix)
        log_msg, log_extra = self._build_log_message(extra)
        if use_rich:
            self._print_rich(log_msg, code_prefix)
        else:
            self._log_with_logger(logger, resolved_level, log_msg, log_extra)

    def _resolve_log_level(self, level, code_prefix):
        if level is not None:
            return level
        if code_prefix == "W":
            return logging.WARNING
        return logging.ERROR

    def _build_log_message(self, extra):
        log_msg = f"[{self.code}] {self.summary}"
        log_extra = {
            "code": str(self.code),
            "category": str(self.category),
            "status": self.status_code,
            "details": self.details,
            "meta": self.meta,
        }
        if extra:
            log_extra.update(extra)
        return log_msg, log_extra

    def _print_rich(self, log_msg, code_prefix):
        try:
            console = get_console()
            code_color = "red" if code_prefix in ("E", "X") else "yellow"
            body = Text(self.summary, style="bold")
            if self.hint:
                body.append(f"\n{self.hint}")
            if self.details:
                body.append(f"\n{self.details}", style="dim")
            console.print(Panel(body, title=str(self.code), title_align="left", border_style=code_color))
        except ImportError:
            print(log_msg)
            if self.details:
                print(f"Details: {self.details}")

    def _log_with_logger(self, logger, level, log_msg, log_extra):
        logger = logger or internal_logger
        if logger:
            logger.log(level, log_msg, extra=log_extra)
        else:
            print(log_msg)
            if self.details:
                print(f"Details: {self.details}")
            if self.meta:
                print(f"Meta: {self.meta}")

    def to_payload(
        self, include_status: bool = False, meta: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """A JSON-serializable payload for HTTP error responses: ``{type, code, status, message, hint, details, meta, cause}``."""
        payload = ErrorPayload(
            type=f"optics:{self.category.value}",
            code=str(self.code),
            status=(self.status_code if include_status else None),
            message=self.summary,
            hint=self.hint,
            details=self.details,
            meta=meta or self.meta,
            cause=self.cause_text,
        )
        return payload.model_dump()


def element_label(locator: str) -> str:
    """The element's name in the project when a ``${name}`` param named it, else the quoted locator."""
    from optics_framework.common import test_context

    names = test_context.element_names.get({})
    return names.get(locator) or f"'{locator}'"


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


class ElementNotFound(OpticsError):
    """The screen does not show the element; ``locators`` are the values that were looked for.

    A specific reason in the cause chain (a web view that cannot be read, a covered element) is
    shown; a source's own miss or a failed lookup inside a source is only in the technical detail.
    """

    show_cause = False

    def __init__(
        self,
        locator: str,
        code: str | Code = Code.E0201,
        cause: Optional[BaseException] = None,
        hint: Optional[str] = None,
    ):
        self.locators: tuple[str, ...] = (locator,)
        specific = next(
            (e for e in _chain(cause) if isinstance(e, OpticsError) and not isinstance(e, ElementNotFound)), None
        ) if cause is not None else None
        self.specific = specific
        if specific is not None:
            label = element_label(locator)
            named = label != f"'{locator}'" or label not in specific.summary
            message = f"Cannot find {label}: {specific.summary}" if named else specific.summary
        else:
            message = self.describe((locator,))
        super().__init__(code, message=message, cause=cause, hint=hint)

    @staticmethod
    def describe(locators: tuple[str, ...]) -> str:
        """``name is not on the screen. Looked for 'a' and 'b'.``, or ``'a' is not on the screen.`` without a name."""
        labels = list(dict.fromkeys(element_label(locator) for locator in locators))
        quoted = [f"'{locator}'" for locator in dict.fromkeys(locators)]
        if labels == quoted:
            return f"{_and(quoted)} is not on the screen." if len(quoted) == 1 else f"None of {_and(quoted)} is on the screen."
        return f"{_and(labels)} is not on the screen. Looked for {_and(quoted)}."

    @staticmethod
    def merge(errors: list[OpticsError]) -> str | None:
        """One sentence for several plain misses of the same element, else None."""
        if not errors or not all(isinstance(e, ElementNotFound) and e.specific is None for e in errors):
            return None
        return ElementNotFound.describe(tuple(locator for e in errors for locator in e.locators))


def undefined_element(name: str) -> OpticsError:
    """A ``${name}`` reference with no value in the project's elements."""
    return OpticsError(
        Code.E0201,
        message=f"${{{name}}} is not defined in the project's elements",
        hint=f"Add {name} to an elements file, or correct the name.",
    )


def register_error(spec: ErrorSpec) -> None:
    """Register or override an ErrorSpec at runtime.

    This allows CLI or application code to add domain-specific error codes.
    """
    ERROR_REGISTRY[spec.code] = spec


def from_code(
    code: str, message: Optional[str] = None, details: Optional[Any] = None
) -> OpticsError:
    """Create an OpticsError from a known code; message/details override defaults."""
    return OpticsError(code, message=message, details=details)


def raise_code(
    code: str, message: Optional[str] = None, details: Optional[Any] = None
) -> None:
    """Convenience to raise an OpticsError created from code."""
    raise from_code(code, message=message, details=details)


__all__ = [
    "Category",
    "ElementNotFound",
    "ErrorSpec",
    "ERROR_REGISTRY",
    "Explanation",
    "OpticsError",
    "explain",
    "from_code",
    "plain",
    "raise_code",
    "register_error",
    "undefined_element",
    "ErrorPayload",
]
