"""Reports that agents send to the Optics maintainers through the MCP server's send_feedback tool."""
import base64
import binascii
import re
import sys
from collections.abc import Collection
from typing import Literal

import requests

from optics_framework.common.logging_config import mask_secrets
from optics_framework.helper.updates import turned_off, user_agent
from optics_framework.helper.version import VERSION

FEEDBACK_URL = "https://optics-framework.org/api/feedback"
ENV_NO_FEEDBACK = "OPTICS_NO_FEEDBACK"
Kind = Literal["broken", "missing", "confusing", "idea", "worked"]
Impact = Literal["blocked", "worked_around", "friction", "none"]
LIMITS = {"task": 200, "description": 4000, "tool": 100, "error": 1000, "suggestion": 1000, "client": 100}
_TIMEOUT_S = 5.0
_SCRUB = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_-]{16,}|\b(?:ghp|gho|ghs|github_pat)_\w{20,}|\bAKIA[0-9A-Z]{16}\b"), "[key]"),
    (re.compile(r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}"), "[token]"),
    (re.compile(r"(?i)\b(authorization\s*[:=]\s*(?:bearer\s+|basic\s+)?)[\w.~+/=-]+"), r"\1[token]"),
    (re.compile(r"(?i)\b(bearer\s+|token\s*[:=]\s*)(?=[\w.~+/=-]*\d)[\w.~+/=-]{6,}"), r"\1[token]"),
)
_BASIC = re.compile(r"(?i)\b(basic\s+)([A-Za-z0-9+/]{4,}={0,2})(?![\w+/=])")


def enabled() -> bool:
    return not turned_off(ENV_NO_FEEDBACK)


def _basic_credential(match: re.Match[str]) -> str:
    """Masks ``Basic <base64>`` only when it decodes to ``user:password``, so prose such as "basic usage" stays."""
    try:
        decoded = base64.b64decode(match.group(2), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        return match.group(0)
    return f"{match.group(1)}[token]" if ":" in decoded else match.group(0)


def scrub(text: str, secrets: Collection[str] = ()) -> str:
    text = mask_secrets(text, secrets)
    for pattern, replacement in _SCRUB:
        text = pattern.sub(replacement, text)
    return _BASIC.sub(_basic_credential, text)


def send(report: dict[str, str], transport: str, secrets: Collection[str] = ()) -> str:
    """Send one report with ``secrets`` masked; returns the line to show the agent, which never asks it to retry."""
    body = {name: scrub(value, secrets).strip()[:LIMITS.get(name, 40)] for name, value in report.items() if value and value.strip()}
    body |= {"version": VERSION, "transport": transport, "platform": sys.platform}
    try:
        response = requests.post(FEEDBACK_URL, json=body, headers={"User-Agent": user_agent("mcp")}, timeout=_TIMEOUT_S)
    except requests.RequestException:
        return "The report could not be sent. Carry on with your task."
    if response.status_code == 201:
        return "Thanks, the Optics maintainers will read it. Carry on with your task."
    return f"The report was not stored ({response.status_code}). Carry on with your task."
