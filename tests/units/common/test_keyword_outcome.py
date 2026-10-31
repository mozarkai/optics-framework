import asyncio
import logging
import threading

from optics_framework.common.keyword_outcome import (
    capture_keyword_logs,
    find_error_log,
    silent_error,
    winning_strategy,
)
from optics_framework.common.logging_config import execution_logger, internal_logger


def _record(name: str, level: int, message: str) -> logging.LogRecord:
    return logging.LogRecord(name, level, __file__, 1, message, None, None)


def test_find_error_log_returns_the_first_internal_error():
    records = [
        _record("optics.internal", logging.WARNING, "slow screenshot"),
        _record("optics.execution", logging.ERROR, "Unable to send batch events"),
        _record("optics.internal", logging.ERROR, "Unknown swipe direction: sideways"),
        _record("optics.internal", logging.ERROR, "second error"),
    ]
    assert find_error_log(records) == "Unknown swipe direction: sideways"
    assert find_error_log(records[:2]) is None


def test_winning_strategy_reads_the_first_success_without_an_action():
    records = [
        _record("optics.execution", logging.INFO, "Trying ScreenshotStrategy on 'screenshot' ... SUCCESS"),
        _record("optics.execution", logging.INFO, "Trying XPathStrategy on 'Sign in' ... FAIL"),
        _record("optics.execution", logging.INFO, "Trying TextDetectionStrategy on 'Sign\nin' ... SUCCESS"),
        _record("optics.execution", logging.INFO, "Trying ImageDetectionStrategy on 'Sign in' ... SUCCESS"),
    ]
    assert winning_strategy(records) == "OCR"
    assert winning_strategy([_record("optics.execution", logging.INFO, "Trying MyStrategy on 'x' ... SUCCESS")]) == "My"
    assert winning_strategy(records[:2]) is None


def test_winning_strategy_names_the_result_the_action_used():
    records = [
        _record("optics.execution", logging.INFO, "Trying TextElementStrategy on 'Sign in' ... SUCCESS"),
        _record("optics.execution", logging.INFO, "Trying TextDetectionStrategy on 'Sign in' ... SUCCESS"),
        _record("optics.execution", logging.INFO, "Action 'press_element' succeeded with TextElementStrategy"),
    ]
    assert winning_strategy(records) == "Text"


def test_silent_error_counts_only_what_the_working_act_logged():
    records = [
        _record("optics.internal", logging.ERROR, "XPath lookup failed"),
        _record("optics.execution", logging.INFO, "Trying XPathStrategy on 'e7' ... FAIL"),
        _record("optics.execution", logging.INFO, "Trying TextElementStrategy on 'e7' ... SUCCESS"),
        _record("optics.execution", logging.INFO, "Trying TextDetectionStrategy on 'e7' ... SUCCESS"),
        _record("optics.internal", logging.ERROR, "stale element"),
        _record("optics.internal", logging.WARNING, "Action 'swipe_from_element' failed with TextElementStrategy: stale"),
        _record("optics.internal", logging.ERROR, "Unknown swipe direction: diagonal"),
        _record("optics.execution", logging.INFO, "Action 'swipe_from_element' succeeded with TextDetectionStrategy"),
    ]
    assert silent_error(records) == "Unknown swipe direction: diagonal"
    assert silent_error(records[:6] + records[7:]) is None
    assert silent_error(records[:4]) is None


def test_silent_error_ignores_a_lookup_that_missed_without_an_act():
    records = [
        _record("optics.internal", logging.ERROR, "Timeout reached: None of the specified elements were found."),
        _record("optics.execution", logging.INFO, "Trying TextElementStrategy on 'Accept cookies' ... FAIL"),
    ]
    assert silent_error(records) is None
    assert silent_error([*records, _record("optics.internal", logging.ERROR, "press failed")]) == "press failed"


def _keyword_that_logs_and_returns() -> None:
    execution_logger.info("Trying XPathStrategy on 'Swipe' ... SUCCESS")
    internal_logger.error("Unknown swipe direction: sideways")


def test_capture_sees_a_keyword_run_in_a_worker_thread():
    prev_level = execution_logger.level
    execution_logger.setLevel(logging.WARNING)

    async def run() -> list[logging.LogRecord]:
        with capture_keyword_logs() as records:
            await asyncio.to_thread(_keyword_that_logs_and_returns)
        return records

    try:
        records = asyncio.run(run())
        assert execution_logger.level == logging.WARNING
    finally:
        execution_logger.setLevel(prev_level)
    assert find_error_log(records) == "Unknown swipe direction: sideways"
    assert winning_strategy(records) == "XPath"


def test_concurrent_captures_keep_their_own_records():
    both_logging = threading.Barrier(2, timeout=5)

    def keyword(message: str) -> None:
        both_logging.wait()
        internal_logger.error(message)
        both_logging.wait()

    async def call(message: str) -> list[str]:
        with capture_keyword_logs() as records:
            await asyncio.to_thread(keyword, message)
        return [r.getMessage() for r in records]

    async def run() -> list[list[str]]:
        return list(await asyncio.gather(call("first"), call("second")))

    assert asyncio.run(run()) == [["first"], ["second"]]


def test_records_outside_a_capture_are_not_kept():
    with capture_keyword_logs() as records:
        pass
    internal_logger.error("after the block")
    assert records == []
