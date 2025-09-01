"""Structured error definitions and helpers for Optics Framework.

This module provides:
- ErrorSpec: static registry entries for known error codes.
- OpticsError: an exception carrying structured metadata (category, code, http status, details).
- helpers: from_code, raise_code and to_response to produce machine-readable payloads.

"""

from __future__ import annotations


import logging
from enum import Enum
from typing import Dict, Optional, Any
from pydantic import BaseModel
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from optics_framework.common.logging_config import internal_logger


class Category(str, Enum):
    DRIVER = "driver"
    ELEMENT = "element"
    SCREENSHOT = "screenshot"
    KEYWORD = "keyword"
    CONFIG = "config"
    MODULE = "module"
    TEST = "test"
    GENERAL = "general"


class Code(str, Enum):
    # Driver issue
    E0101 = "E0101"
    E0102 = "E0102"
    E0103 = "E0103"
    E0104 = "E0104"
    W0104 = "W0104"

    # Element location issue
    E0201 = "E0201"
    X0201 = "X0201"
    E0202 = "E0202"
    W0203 = "W0203"
    E0204 = "E0204"
    E0205 = "E0205"

    # Screenshot issues
    E0301 = "E0301"
    W0302 = "W0302"
    E0303 = "E0303"

    # Keyword Execution issues
    E0401 = "E0401"
    X0401 = "X0401"
    E0402 = "E0402"
    E0403 = "E0403"
    W0404 = "W0404"

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

    # General Issues
    E0801 = "E0801"
    E0802 = "E0802"


class ErrorSpec(BaseModel):
    """Specification for a known error code.

    Using pydantic so the spec can be serialized and validated across
    CLI, library, and HTTP contexts without tying this module to an
    HTTP library.
    """

    code: Code
    default_message: str
    category: Category
    default_status: int = 500
    meta: Optional[Dict[str, Any]] = None
    # severity can be inferred from `code` prefix; don't store it here

    model_config = {
        "frozen": True,
    }


# Registry built from the list the user provided. Keep messages short; callers may override.
ERROR_REGISTRY: Dict[str, ErrorSpec] = {
    # Driver issue
    "E0101": ErrorSpec(
        code=Code.E0101,
        default_message="Driver not initialized",
        category=Category.DRIVER,
        default_status=500,
    ),
    "E0102": ErrorSpec(
        code=Code.E0102,
        default_message="Failed to start session",
        category=Category.DRIVER,
        default_status=500,
    ),
    "E0103": ErrorSpec(
        code=Code.E0103,
        default_message="Failed to end session",
        category=Category.DRIVER,
        default_status=500,
    ),
    "E0104": ErrorSpec(
        code=Code.E0104,
        default_message="Driver config incomplete",
        category=Category.DRIVER,
        default_status=400,
    ),
    "W0104": ErrorSpec(
        code=Code.W0104,
        default_message="Driver config incomplete (warning)",
        category=Category.DRIVER,
        default_status=200,
    ),
    # Element location issue
    "E0201": ErrorSpec(
        code=Code.E0201,
        default_message="Element not found",
        category=Category.ELEMENT,
        default_status=404,
    ),
    "X0201": ErrorSpec(
        code=Code.X0201,
        default_message="Element not found after all fallbacks",
        category=Category.ELEMENT,
        default_status=500,
    ),
    "E0202": ErrorSpec(
        code=Code.E0202,
        default_message="Unsupported operation for element source",
        category=Category.ELEMENT,
        default_status=400,
    ),
    "W0203": ErrorSpec(
        code=Code.W0203,
        default_message="Element format mismatch",
        category=Category.ELEMENT,
        default_status=200,
    ),
    "E0204": ErrorSpec(
        code=Code.E0204,
        default_message="Timeout locating element",
        category=Category.ELEMENT,
        default_status=504,
    ),
    "E0205": ErrorSpec(
        code=Code.E0205,
        default_message="Invalid element type",
        category=Category.ELEMENT,
        default_status=400,
    ),
    # Screenshot issues
    "E0301": ErrorSpec(
        code=Code.E0301,
        default_message="Screenshot disabled",
        category=Category.SCREENSHOT,
        default_status=400,
    ),
    "W0302": ErrorSpec(
        code=Code.W0302,
        default_message="Fallback to next camera supported driver",
        category=Category.SCREENSHOT,
        default_status=200,
    ),
    "E0303": ErrorSpec(
        code=Code.E0303,
        default_message="Screenshot empty/black",
        category=Category.SCREENSHOT,
        default_status=500,
    ),
    # Keyword Execution issues
    "E0401": ErrorSpec(
        code=Code.E0401,
        default_message="Action failed",
        category=Category.KEYWORD,
        default_status=500,
    ),
    "X0401": ErrorSpec(
        code=Code.X0401,
        default_message="Action failed with exception",
        category=Category.KEYWORD,
        default_status=500,
    ),
    "E0402": ErrorSpec(
        code=Code.E0402,
        default_message="Keyword not found",
        category=Category.KEYWORD,
        default_status=404,
    ),
    "E0403": ErrorSpec(
        code=Code.E0403,
        default_message="Invalid keyword parameters",
        category=Category.KEYWORD,
        default_status=400,
    ),
    "W0404": ErrorSpec(
        code=Code.W0404,
        default_message="Keyword deprecated",
        category=Category.KEYWORD,
        default_status=200,
    ),
    # Config/File issues
    "E0501": ErrorSpec(
        code=Code.E0501,
        default_message="Missing required files",
        category=Category.CONFIG,
        default_status=400,
    ),
    "X0502": ErrorSpec(
        code=Code.X0502,
        default_message="File read exception",
        category=Category.CONFIG,
        default_status=500,
    ),
    "E0503": ErrorSpec(
        code=Code.E0503,
        default_message="Config parser error (important param)",
        category=Category.CONFIG,
        default_status=400,
    ),
    "W0503": ErrorSpec(
        code=Code.W0503,
        default_message="Config parser warning (can be ignored)",
        category=Category.CONFIG,
        default_status=200,
    ),
    # Module/Factory Issues
    "E0601": ErrorSpec(
        code=Code.E0601,
        default_message="Module not found",
        category=Category.MODULE,
        default_status=404,
    ),
    "W0602": ErrorSpec(
        code=Code.W0602,
        default_message="No valid instances, ignoring it",
        category=Category.MODULE,
        default_status=200,
    ),
    "E0603": ErrorSpec(
        code=Code.E0603,
        default_message="Factory init failed",
        category=Category.MODULE,
        default_status=500,
    ),
    "X0604": ErrorSpec(
        code=Code.X0604,
        default_message="Import related problem",
        category=Category.MODULE,
        default_status=500,
    ),
    # Test Case Issues
    "E0701": ErrorSpec(
        code=Code.E0701,
        default_message="Execution failed",
        category=Category.TEST,
        default_status=500,
    ),
    "E0702": ErrorSpec(
        code=Code.E0702,
        default_message="Test case not found",
        category=Category.TEST,
        default_status=404,
    ),
    "E0703": ErrorSpec(
        code=Code.E0703,
        default_message="Parameter resolution failed",
        category=Category.TEST,
        default_status=400,
    ),
    "E0704": ErrorSpec(
        code=Code.E0704,
        default_message="Test case timeout",
        category=Category.TEST,
        default_status=504,
    ),
    "W0705": ErrorSpec(
        code=Code.W0705,
        default_message="Test case skipped",
        category=Category.TEST,
        default_status=200,
    ),
    # General Issues
    "E0801": ErrorSpec(
        code=Code.E0801,
        default_message="Unexpected error",
        category=Category.GENERAL,
        default_status=500,
    ),
    "E0802": ErrorSpec(
        code=Code.E0802,
        default_message="Unhandled exception",
        category=Category.GENERAL,
        default_status=500,
    ),
}


class ErrorPayload(BaseModel):
    """Pydantic model for serialized error payloads."""

    type: str
    code: str
    status: Optional[int] = None
    message: str
    details: Optional[Any] = None
    meta: Optional[Dict[str, Any]] = None


class OpticsError(Exception):
    """Exception carrying structured metadata for HTTP responses and logging.

    Attributes:
        code: short code like 'E0101'
        message: human readable message
        category: Category enum
        status_code: HTTP status integer
        details: optional dict with extra data
    """

    def __init__(
        self,
        code: str | Code,
        message: Optional[str] = None,
        details: Optional[Any] = None,
        cause: Optional[BaseException] = None,
        meta: Optional[Dict[str, Any]] = None,
    ):
        spec = ERROR_REGISTRY.get(code)
        if spec is None:
            # Unknown code: create a fallback spec but don't mutate registry
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
        # severity can be inferred by the first char of the code (E/W/X)
        # allow callers to override the default message
        self.message = message or spec.default_message
        self.details = details
        self.__cause__ = cause
        self.meta = meta or getattr(spec, "meta", None)
        super().__init__(f"{self.code}: {self.message}")

    def log(self, logger=None, level=None, extra=None, use_rich: bool = True):
        """
        Log the error using the provided logger (defaults to internal_logger), or print a rich, colored message if use_rich is True.
        Args:
            logger: logging.Logger instance or compatible. Defaults to internal_logger from logging_config.
            level: log level (default: ERROR for E/X, WARNING for W).
            extra: dict of extra metadata for structured logging.
            use_rich: if True, print a colored, structured error message using rich (for CLI or interactive use).
        """
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
        log_msg = f"[{self.code}] {self.message} (category: {self.category}, status: {self.status_code})"
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
            console = Console()
            payload = self.to_payload(include_status=True)
            code_color = "red" if code_prefix in ("E", "X") else "yellow"
            header = Text(f"❌ {payload['code']}", style=f"bold {code_color}")
            category = Text(f"({payload['type']})", style="yellow")
            message = Text(payload['message'], style="bold")
            details = payload.get("details")
            meta = payload.get("meta")
            body = message
            if details:
                body += Text(f"\n[dim]Details:[/dim] {details}")
            if meta:
                body += Text(f"\n[dim]Meta:[/dim] {meta}")
            panel = Panel(body, title=header.plain + " " + category.plain, border_style=code_color)
            console.print(panel)
        except ImportError:
            print(log_msg)
            if self.details:
                print(f"Details: {self.details}")
            if self.meta:
                print(f"Meta: {self.meta}")

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
        """Return a JSON-serializable payload suitable for HTTP error responses.

        Format (machine-friendly):
        {
            "type": "optics:<category>",
            "code": "E0101",
            "status": 500,
            "message": "...",
            "details": {...},
            "meta": {...}
        }
        """
        payload = ErrorPayload(
            type=f"optics:{self.category.value}",
            code=str(self.code),
            status=(self.status_code if include_status else None),
            message=self.message,
            details=self.details,
            meta=meta or self.meta,
        )
        # return primitive dict for downstream compatibility
        return payload.model_dump()


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
    "ErrorSpec",
    "ERROR_REGISTRY",
    "OpticsError",
    "from_code",
    "raise_code",
    "register_error",
    "ErrorPayload",
]
