"""Unit tests for ``utils.save_screenshot`` filenames and write failures.

Two properties are pinned here, both of which used to hold only by accident on
POSIX. First, the filename must be usable on every platform: callers pass
``utils.get_timestamp()``, whose ISO-8601 colons are reserved on Windows.
Second, a write that does not happen must be visible: ``cv2.imwrite`` reports a
rejected path by *returning False*, so an unchecked call drops the capture while
the run still reports success.
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from optics_framework.common import utils
from optics_framework.common.error import OpticsError
from optics_framework.common.logging_config import internal_logger

pytestmark = pytest.mark.white_box

# Reserved on Windows; a filename containing any of them cannot be created.
RESERVED = '<>:"/\\|?*'

ISO_TIMESTAMP = "2026-09-22T16:51:50.397462+05:30"


@pytest.fixture
def image() -> np.ndarray:
    return np.zeros((8, 8, 3), dtype=np.uint8)


@contextmanager
def captured_logs():
    """Collect ``internal_logger`` records emitted inside the block."""
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector()
    previous = internal_logger.level
    internal_logger.setLevel(logging.DEBUG)
    internal_logger.addHandler(handler)
    try:
        yield records
    finally:
        internal_logger.removeHandler(handler)
        internal_logger.setLevel(previous)


class TestFilenamePortability:
    def test_iso8601_timestamp_is_sanitized(self, tmp_path, image):
        path = utils.save_screenshot(
            image, "press_element", output_dir=str(tmp_path), time_stamp=ISO_TIMESTAMP)

        assert path is not None
        assert not set(os.path.basename(path)) & set(RESERVED)
        assert os.path.isfile(path)

    def test_the_timestamp_callers_actually_pass_is_sanitized(self, tmp_path, image):
        """``get_timestamp()`` is what verifier.py and the annotated-result
        helpers hand in, so pin the real producer rather than a literal."""
        path = utils.save_screenshot(
            image, "assert_elements", output_dir=str(tmp_path),
            time_stamp=utils.get_timestamp())

        assert path is not None
        assert not set(os.path.basename(path)) & set(RESERVED)
        assert os.path.isfile(path)

    def test_generated_timestamp_stays_portable(self, tmp_path, image):
        path = utils.save_screenshot(image, "capture_screenshot", output_dir=str(tmp_path))

        assert path is not None
        assert not set(os.path.basename(path)) & set(RESERVED)

    def test_timestamp_remains_readable(self, tmp_path, image):
        """Sanitising must not collapse the timestamp into something opaque —
        the filename is how a reader orders the captures of a run."""
        path = utils.save_screenshot(
            image, "press_element", output_dir=str(tmp_path), time_stamp=ISO_TIMESTAMP)

        assert os.path.basename(path) == "2026-09-22T16-51-50.397462+05-30-press_element.jpg"


class TestWriteFailureIsVisible:
    def test_rejected_write_returns_none_and_warns(self, tmp_path, image):
        with patch.object(utils.cv2, "imwrite", return_value=False):
            with captured_logs() as records:
                path = utils.save_screenshot(
                    image, "press_element", output_dir=str(tmp_path))

        assert path is None
        assert [r for r in records if r.levelno >= logging.WARNING]

    def test_write_exception_is_warned_not_raised(self, tmp_path, image):
        with patch.object(utils.cv2, "imwrite", side_effect=OSError("disk full")):
            with captured_logs() as records:
                path = utils.save_screenshot(
                    image, "press_element", output_dir=str(tmp_path))

        assert path is None
        warnings = [r for r in records if r.levelno >= logging.WARNING]
        assert warnings and "disk full" in warnings[0].getMessage()

    def test_a_failed_capture_does_not_abort_the_run(self, tmp_path, image):
        """Screenshots are diagnostic output: loud, but never fatal."""
        with patch.object(utils.cv2, "imwrite", return_value=False):
            assert utils.save_screenshot(
                image, "press_element", output_dir=str(tmp_path)) is None

    def test_missing_output_dir_returns_none(self, image):
        assert utils.save_screenshot(image, "press_element", output_dir=None) is None

    def test_empty_image_still_raises(self, tmp_path):
        with pytest.raises(ValueError):
            utils.save_screenshot(None, "press_element", output_dir=str(tmp_path))


class TestLiveCaptureScreenshot:
    """``/screenshot`` reports the path it wrote instead of rebuilding a guess."""

    def _controller(self, tmp_path, image):
        from optics_framework.helper.live import LiveController

        ctrl = LiveController.__new__(LiveController)
        ctrl.folder_path = str(tmp_path)
        ctrl._action_keyword = SimpleNamespace(
            strategy_manager=MagicMock(capture_screenshot=MagicMock(return_value=image)))
        return ctrl

    def test_returns_the_path_that_was_written(self, tmp_path, image):
        path = self._controller(tmp_path, image).capture_screenshot()

        assert os.path.isfile(path)

    def test_raises_when_nothing_was_written(self, tmp_path, image):
        ctrl = self._controller(tmp_path, image)
        with patch.object(utils.cv2, "imwrite", return_value=False):
            with pytest.raises(OpticsError):
                ctrl.capture_screenshot()
