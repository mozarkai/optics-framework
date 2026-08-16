"""Unit tests for targeted truncation of oversized payload dumps in log records.

Only records emitted by the third-party loggers that embed full HTTP payloads
(selenium's remote connection and urllib3 — page-source XML and base64
screenshots) are trimmed to head + tail. Every other log line must pass
through byte-for-byte untouched.
"""
import logging
import sys

import pytest

from optics_framework.common.logging_config import (
    LOG_MESSAGE_HEAD_CHARS,
    LOG_MESSAGE_MAX_CHARS,
    LOG_MESSAGE_TAIL_CHARS,
    SensitiveDataFormatter,
    truncate_log_message,
)

pytestmark = pytest.mark.white_box

_SELENIUM_LOGGER = "selenium.webdriver.remote.remote_connection"
_URLLIB3_LOGGER = "urllib3.connectionpool"


def _record(logger_name, message, args=()):
    return logging.LogRecord(
        name=logger_name,
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=args,
        exc_info=None,
    )


def _page_source_dump(size=20_000):
    xml = '<hierarchy rotation="0">' + "".join(
        f'<node index="{i}" text="item-{i}" />' for i in range(size)
    ) + "</hierarchy>"
    return f"Remote response: status=200 | data={xml} | headers={{'Content-Type': 'text/xml'}}"


def _base64_dump(size=20_000):
    return f"Remote response: status=200 | data={'iVBORw0KGgoAAAANSUhEUg==' * (size // 24)} | headers={{'Content-Type': 'image/png'}}"


class TestTruncateLogMessage:
    def test_short_message_is_unchanged(self):
        message = "a normal sized log line"
        assert truncate_log_message(message) == message

    def test_oversized_message_keeps_head_and_tail(self):
        message = "A" * 10_000
        out = truncate_log_message(message)
        assert out.startswith("A" * LOG_MESSAGE_HEAD_CHARS)
        assert out.endswith("A" * LOG_MESSAGE_TAIL_CHARS)
        expected_omitted = 10_000 - LOG_MESSAGE_HEAD_CHARS - LOG_MESSAGE_TAIL_CHARS
        assert f"[{expected_omitted} characters truncated]" in out


class TestPayloadDumpTruncation:
    def test_selenium_page_source_dump_is_trimmed(self):
        record = _record(_SELENIUM_LOGGER, _page_source_dump())
        out = SensitiveDataFormatter().format(record)
        assert len(out) <= LOG_MESSAGE_MAX_CHARS
        assert out.startswith(_page_source_dump()[:LOG_MESSAGE_HEAD_CHARS])
        assert out.endswith(_page_source_dump()[-LOG_MESSAGE_TAIL_CHARS:])
        assert "characters truncated" in out

    def test_selenium_base64_screenshot_dump_is_trimmed(self):
        record = _record(_SELENIUM_LOGGER, _base64_dump())
        out = SensitiveDataFormatter().format(record)
        assert len(out) <= LOG_MESSAGE_MAX_CHARS
        assert "characters truncated" in out

    def test_urllib3_dump_is_trimmed(self):
        record = _record(_URLLIB3_LOGGER, _base64_dump())
        out = SensitiveDataFormatter().format(record)
        assert len(out) <= LOG_MESSAGE_MAX_CHARS
        assert "characters truncated" in out

    def test_args_carrying_the_payload_are_trimmed(self):
        # selenium logs the body as the first %s arg: logger.debug("Remote
        # response: status=%s | data=%s | headers=%s", status, data, headers)
        record = _record(_SELENIUM_LOGGER, "Remote response: status=%s | data=%s | headers=%s",
                         args=(200, "iVBORw0KGgoAAAANSUhEUg==" * 1000, {"Content-Type": "image/png"}))
        out = SensitiveDataFormatter().format(record)
        assert len(out) <= LOG_MESSAGE_MAX_CHARS
        assert "characters truncated" in out


class TestOtherLogsUntouched:
    def test_optics_internal_log_is_never_truncated(self):
        big = "x" * 50_000
        record = _record("optics.internal", f"Large but legitimate payload: {big}")
        out = SensitiveDataFormatter().format(record)
        assert big in out
        assert "characters truncated" not in out

    def test_other_third_party_logger_is_never_truncated(self):
        big = "x" * 50_000
        record = _record("requests.packages.urllib3", f"some other library: {big}")
        out = SensitiveDataFormatter().format(record)
        assert big in out
        assert "characters truncated" not in out


class TestTracebackPreservation:
    def test_traceback_is_not_chopped(self):
        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()
        record = _record(_SELENIUM_LOGGER, _page_source_dump())
        record.exc_info = exc_info
        out = SensitiveDataFormatter().format(record)
        assert "characters truncated" in out
        assert "ValueError: boom" in out
