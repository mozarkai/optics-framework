"""Unit tests for ScreenshotStream thread lifecycle and timeout bounds."""
from unittest.mock import MagicMock

from optics_framework.common.screenshot_stream import ScreenshotStream


class TestStopCaptureTimeoutBounds:
    """stop_capture's ``timeout`` is a total budget across threads, not per-thread."""

    def test_total_join_time_does_not_exceed_timeout(self, monkeypatch):
        stream = ScreenshotStream(lambda: None)
        capture_thread = MagicMock()
        capture_thread.is_alive.return_value = True
        dedup_thread = MagicMock()
        dedup_thread.is_alive.return_value = True
        stream.capture_thread = capture_thread
        stream.dedup_thread = dedup_thread

        clock = {"now": 0.0}
        monkeypatch.setattr(
            "optics_framework.common.screenshot_stream.time.time", lambda: clock["now"]
        )

        def join(timeout=None):
            clock["now"] += timeout

        capture_thread.join.side_effect = join
        dedup_thread.join.side_effect = join

        stream.stop_capture(timeout=1.0)

        assert clock["now"] == 1.0
        capture_thread.join.assert_called_once_with(timeout=1.0)
        dedup_thread.join.assert_not_called()

    def test_zero_timeout_skips_join_but_sets_stop_event(self, monkeypatch):
        stream = ScreenshotStream(lambda: None)
        capture_thread = MagicMock()
        capture_thread.is_alive.return_value = True
        stream.capture_thread = capture_thread

        monkeypatch.setattr(
            "optics_framework.common.screenshot_stream.time.time", lambda: 5.0
        )

        stream.stop_capture(timeout=0.0)

        assert stream.stop_event.is_set()
        capture_thread.join.assert_not_called()
